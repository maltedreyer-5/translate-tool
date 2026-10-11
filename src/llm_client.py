# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
LLM client for OpenAI-compatible APIs.
Supports local LLMs via OpenAI-compatible endpoints; one client type serves
all document types.
"""

import asyncio
import atexit
import logging
import time
import threading
from typing import Dict, List, Optional, Any, AsyncGenerator, Tuple
from dataclasses import dataclass
from enum import Enum
from contextlib import asynccontextmanager

import random

import aiohttp

from .config import get_config, resolve_path
from . import prompt_texts

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base exception for LLM-related errors"""
    pass


class LLMConnectionError(LLMError):
    """Error connecting to the LLM API"""
    pass


class LLMRateLimitError(LLMError):
    """Rate limit exceeded"""
    pass


class LLMTimeoutError(LLMError):
    """Request Timeout"""
    pass


class LLMTruncatedError(LLMError):
    """
    The answer was cut off at the length limit even with the largest possible
    limit. The caller should split the text and translate it in parts.
    """

    def __init__(self, message: str, partial: str = ""):
        super().__init__(message)
        self.partial = partial


class MessageRole(Enum):
    """Roles of chat messages"""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Message:
    """A chat message"""
    role: MessageRole
    content: str

    def to_dict(self) -> Dict[str, str]:
        """Convert to a dictionary for the API"""
        return {
            "role": self.role.value,
            "content": self.content
        }


@dataclass
class CompletionResponse:
    """Response of the LLM API"""
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None
    finish_reason: Optional[str] = None
    response_time: Optional[float] = None


class LLMClient:
    """
    Async client for OpenAI-compatible LLM APIs, with event loop validation
    and thread-safe session management.
    """

    def __init__(self, config=None, shared_semaphore: Optional[asyncio.Semaphore] = None):
        """
        Initialise the LLM client.

        Args:
            config: optional Config object; the global config is used otherwise
            shared_semaphore: Optional application-wide limit on simultaneous
                requests (parallel_processing.max_concurrent_requests), shared
                by the clients of all jobs
        """
        self.config = config or get_config()
        self.shared_semaphore = shared_semaphore
        self.llm_config = self.config.llm

        # API Endpoint konstruieren
        self.base_url = self.llm_config.api_base
        self.chat_endpoint = f"{self.base_url}/chat/completions"

        # Headers for API requests
        self.headers = {
            "Authorization": f"Bearer {self.llm_config.api_key}",
            "Content-Type": "application/json"
        }

        # Caps the number of simultaneous requests of this client. One client
        # is created per translation job, so this is a per-job limit
        # (parallel_processing.rate_limit).
        self.semaphore = asyncio.Semaphore(self.config.parallel_processing.rate_limit)

        # Session management with thread safety
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = threading.Lock()
        self._closed = False

        logger.info(f"LLM Client initialized with endpoint: {self.base_url}")

    def _check_event_loop(self) -> bool:
        """
        Check if event loop is available and not closed

        Returns:
            bool: True if event loop is available and running
        """
        try:
            loop = asyncio.get_running_loop()
            return not loop.is_closed()
        except RuntimeError:
            return False

    async def __aenter__(self):
        """Async Context Manager Entry"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async Context Manager Exit"""
        await self.close()

    async def start(self):
        """
        Start the client session (with event loop validation).
        """
        if self._closed:
            raise RuntimeError("Client has already been closed")

        if not self._check_event_loop():
            raise RuntimeError("Event loop is not available or closed")

        with self._session_lock:
            if self._session is None:
                # Create connector with proper settings
                connector = aiohttp.TCPConnector(
                    limit=20,  # Total connection pool size
                    limit_per_host=5,  # Connections per host
                    ttl_dns_cache=300,  # DNS cache TTL
                    use_dns_cache=True,
                    keepalive_timeout=30,
                    enable_cleanup_closed=True
                )

                # A non-streamed completion sends nothing until generation is
                # finished, so a per-read timeout shorter than the total would
                # abort every request that takes longer to generate. Only the
                # total (llm.timeout) and the connection setup are bounded.
                timeout = aiohttp.ClientTimeout(
                    total=self.llm_config.timeout,
                    connect=min(30, self.llm_config.timeout)
                )

                self._session = aiohttp.ClientSession(
                    headers=self.headers,
                    timeout=timeout,
                    connector=connector
                )

                # No atexit registration here: one client is created per job
                # and closed by its owner. An atexit callback would keep every
                # client (and everything it references) alive until shutdown.

                logger.debug("HTTP session started with proper connector configuration")

    async def close(self):
        """
        Close the client session gracefully.
        """
        if self._closed:
            return

        self._closed = True

        with self._session_lock:
            if self._session:
                try:
                    # Graceful shutdown with timeout
                    if not self._session.closed:
                        await asyncio.wait_for(
                            self._session.close(),
                            timeout=5.0
                        )
                except asyncio.TimeoutError:
                    logger.warning("Session close timeout, forcing close")
                except Exception as e:
                    logger.error(f"Error closing session: {e}")
                finally:
                    self._session = None
                    logger.debug("HTTP session closed")

    def _sync_cleanup(self):
        """Synchronous cleanup for atexit"""
        if self._session and not self._session.closed:
            try:
                # Check if event loop exists and is running
                if self._check_event_loop():
                    loop = asyncio.get_running_loop()
                    if not loop.is_closed():
                        # Schedule cleanup task
                        loop.create_task(self.close())
                else:
                    # Create new event loop for cleanup
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(self.close())
                        loop.close()
                    except Exception as e:
                        logger.error(f"Failed to cleanup in sync mode: {e}")
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

    async def health_check(self) -> bool:
        """
        Check whether the LLM API is reachable (with event loop validation).

        Returns:
            bool: True if the API is reachable, otherwise False
        """
        if not self._check_event_loop():
            logger.error("Event loop not available for health check")
            return False

        try:
            # Send a minimal test request
            response = await self.complete(
                messages=[Message(MessageRole.USER, "Test")],
                max_tokens=1
            )
            logger.info("LLM API health check successful")
            return True
        except Exception as e:
            logger.error(f"LLM API health check failed: {e}")
            return False

    def _auto_max_tokens(self) -> bool:
        return str(getattr(self.llm_config, 'max_tokens', 'auto')).lower() == 'auto'

    # Characters per token used for budgeting. Deliberately low (more tokens
    # per character than most languages need), so that answers are not cut
    # off and the prompt is not underestimated; for Chinese, Japanese and
    # Korean, configured values of 1.5-2.5 are covered.
    _BUDGET_CHARS_PER_TOKEN_TEXT = 1.5
    _BUDGET_CHARS_PER_TOKEN_PROMPT = 2.5
    # A translation can be longer than its source (e.g. English -> German).
    _EXPANSION = 1.5

    def _output_budget(self, messages: List[Message]) -> Tuple[int, int]:
        """
        (limit, cap) for one request with max_tokens "auto".

        The limit covers the text to translate (the last user message) with
        room for a longer target language, plus a fixed margin and the
        configured reasoning reserve. The cap is what remains of the context
        window after the prompt; the limit never exceeds it.
        """
        reserve = int(getattr(self.llm_config, 'reasoning_reserve_tokens', 0) or 0)
        window = int(getattr(self.llm_config, 'context_window', 32000) or 32000)
        text = messages[-1].content if messages else ""
        prompt_chars = sum(len(m.content) for m in messages)
        cap = window - int(prompt_chars / self._BUDGET_CHARS_PER_TOKEN_PROMPT) - 128
        cap = max(256, cap)
        wanted = int(len(text) / self._BUDGET_CHARS_PER_TOKEN_TEXT * self._EXPANSION) + 256 + reserve
        return max(256, min(wanted, cap)), cap

    # Errors worth another attempt: the request may succeed when repeated.
    _RETRYABLE = (LLMConnectionError, LLMTimeoutError, LLMRateLimitError)

    async def complete(
        self,
        messages: List[Message],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        **kwargs
    ) -> CompletionResponse:
        """
        Send a completion request to the LLM API (with event loop validation).

        This is the only place where requests are retried: connection errors,
        timeouts, HTTP 429 and 5xx are attempted up to llm.max_retries times
        in total with capped exponential backoff. Callers must not add a
        second retry layer for these errors.

        Args:
            messages: list of chat messages
            temperature: sampling temperature; None = config value, and if that
                is also None the parameter is not sent at all
            max_tokens: maximum number of generated tokens
            stream: whether the response should be streamed
            **kwargs: additional API parameters

        Returns:
            CompletionResponse: the API response

        Raises:
            LLMError: on API errors
        """
        attempts = max(1, int(getattr(self.llm_config, 'max_retries', 3)))
        base_delay = getattr(self.llm_config, 'retry_delay', 5)
        max_delay = getattr(self.llm_config, 'max_retry_delay', 30)

        cap = None
        if max_tokens is None and self._auto_max_tokens():
            max_tokens, cap = self._output_budget(messages)

        for attempt in range(attempts):
            try:
                response = await self._complete_once(
                    messages, temperature, max_tokens, stream, **kwargs)
                if (cap is not None and getattr(response, 'finish_reason', None) == 'length'
                        and max_tokens < cap):
                    # The estimate was too small (e.g. a reasoning model):
                    # once more with everything the context window allows.
                    logger.info(f"Answer reached the limit of {max_tokens} tokens; "
                                f"requesting again with {cap}")
                    max_tokens = cap
                    response = await self._complete_once(
                        messages, temperature, max_tokens, stream, **kwargs)
                return response
            except self._RETRYABLE as e:
                if attempt == attempts - 1:
                    raise
                delay = min(base_delay * (2 ** attempt), max_delay)
                delay += random.uniform(0, min(1.0, delay * 0.1))
                logger.warning(
                    f"Request failed ({e}); attempt {attempt + 2}/{attempts} "
                    f"in {delay:.1f}s")
                await asyncio.sleep(delay)

    async def _complete_once(
        self,
        messages: List[Message],
        temperature: Optional[float],
        max_tokens: Optional[int],
        stream: bool,
        **kwargs
    ) -> CompletionResponse:
        """Send a single completion request (no retry)."""
        if not self._check_event_loop():
            raise LLMError("Event loop is not available or closed")

        if self._closed:
            raise LLMError("Client is closed")

        if not self._session:
            await self.start()

        current_timeout = self.llm_config.timeout
        # Use 'is not None' so an explicit 0 is not silently replaced.
        current_max_tokens = max_tokens if max_tokens is not None else self.llm_config.max_tokens
        if not isinstance(current_max_tokens, int):  # "auto" without a computed budget
            current_max_tokens = self._output_budget(messages)[0]
        current_temperature = temperature if temperature is not None else self.llm_config.temperature

        # Request-Body konstruieren
        request_data = {
            "model": self.llm_config.model,
            "messages": [msg.to_dict() for msg in messages],
            "max_tokens": current_max_tokens,
            "stream": stream,
            **kwargs
        }
        # Only send a temperature if one is configured; otherwise the server
        # default applies (some current models reject non-default values).
        if current_temperature is not None:
            request_data["temperature"] = current_temperature

        # Per-job limit first, then the application-wide one. Both are always
        # acquired in this order, so they cannot deadlock.
        async with self.semaphore, self._shared_slot():
            start_time = time.time()

            try:
                async with self._session.post(
                    self.chat_endpoint,
                    json=request_data
                ) as response:
                    response_time = time.time() - start_time

                    if response.status == 200:
                        data = await response.json()

                        if stream:
                            # Streaming is not implemented
                            raise NotImplementedError("Streaming is not implemented")

                        # Extract the answer
                        if 'choices' in data and len(data['choices']) > 0:
                            choice = data['choices'][0]
                            content = choice.get('message', {}).get('content') or ''

                            return CompletionResponse(
                                content=content,
                                model=data.get('model', self.llm_config.model),
                                usage=data.get('usage'),
                                finish_reason=choice.get('finish_reason'),
                                response_time=response_time
                            )
                        else:
                            # The response body is not logged or echoed: it can
                            # contain document text.
                            raise LLMError("Unexpected response format (no choices)")

                    elif response.status == 429:
                        raise LLMRateLimitError("Rate limit exceeded")

                    elif response.status in (500, 502, 503, 504):
                        raise LLMConnectionError(f"Server error: {response.status}")

                    else:
                        error_text = await response.text()
                        raise LLMError(f"API error {response.status}: {error_text[:200]}")

            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                raise LLMTimeoutError(
                    f"Request timeout after {elapsed:.0f}s (llm.timeout={current_timeout}s)")

            except aiohttp.ClientError as e:
                raise LLMConnectionError(f"Connection error: {e}")

    @asynccontextmanager
    async def _shared_slot(self):
        """Hold a slot of the application-wide limit, if one is configured."""
        if self.shared_semaphore is None:
            yield
        else:
            async with self.shared_semaphore:
                yield

    def _get_style_instruction(self, style: str) -> str:
        """
        Get style-specific translation instructions

        Args:
            style: Style identifier

        Returns:
            Style-specific instruction text
        """
        style_instructions = prompt_texts.STYLE_INSTRUCTIONS[self._prompt_language()]
        return style_instructions.get(style.lower(), style_instructions.get('professionell_sie', style_instructions['professional']))

    def _get_formality_instruction(self, formality: str, target_lang: str) -> str:
        """
        Get formality-specific translation instructions (Du/Sie for German)

        Args:
            formality: Formality level ('formal' or 'informal')
            target_lang: Target language code

        Returns:
            Formality-specific instruction text
        """
        texts = prompt_texts.TEXTS[self._prompt_language()]
        # Formality matters mainly for German (Sie/Du)
        if target_lang.lower() in ['de', 'deutsch', 'german']:
            return texts['informal_de'] if formality == 'informal' else texts['formal_de']
        # Other languages: generic formal/informal register
        return texts['informal_other'] if formality == 'informal' else texts['formal_other']

    @staticmethod
    def _glossary_chars(glossary: Dict[str, str]) -> int:
        """Approximate prompt cost of a glossary in characters."""
        return sum(len(k) + len(v) + 12 for k, v in glossary.items())

    def _prompt_budget_chars(self) -> Optional[int]:
        """
        Character budget available for the prompt.

        Derived from the configured context window minus the space reserved for
        the model's answer. Returns None if no window is configured, in which
        case the glossary is never reduced.
        """
        window = getattr(self.llm_config, 'context_window', None)
        if not window:
            return None
        # Room for the completion: the fixed limit, or with "auto" a third of
        # the window (the answer is about as long as the text it translates).
        reserved = (window // 3 if self._auto_max_tokens()
                    else int(self.llm_config.max_tokens))
        usable_tokens = max(window - reserved, window // 4)
        return int(usable_tokens * 3.5)  # conservative chars-per-token estimate

    @staticmethod
    def _relevant_glossary(glossary: Dict[str, str], text: str,
                           case_sensitive: bool) -> Dict[str, str]:
        """Keep only glossary entries whose source term occurs in the text."""
        haystack = text if case_sensitive else text.lower()
        result = {}
        for source_term, target_term in glossary.items():
            needle = source_term if case_sensitive else source_term.lower()
            if needle and needle in haystack:
                result[source_term] = target_term
        return result

    async def translate_text(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        style: str = "professionell",
        context: Optional[str] = None,
        glossary: Optional[Dict[str, str]] = None,
        formality: str = "formal",
        extra_instructions: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Translate a text (with event loop validation and style handling).

        Args:
            text: text to translate
            source_lang: source language
            target_lang: target language
            style: translation style
            context: optional context
            glossary: optional glossary
            formality: formality level ('formal' for Sie, 'informal' for Du)
            extra_instructions: optional instructions appended to the system
                message (e.g. how to treat formatting markers)
            **kwargs: additional parameters

        Returns:
            str: translated text
        """
        if not self._check_event_loop():
            raise LLMError("Event loop is not available for translation")

        # Load the base translation prompt
        prompt = await self._load_translation_prompt()
        texts = prompt_texts.TEXTS[self._prompt_language()]

        # Build the system message with style handling
        style_instruction = self._get_style_instruction(style)
        formality_instruction = self._get_formality_instruction(formality, target_lang)
        
        system_content = prompt.format(
            source_lang=self._get_language_name(source_lang),
            target_lang=self._get_language_name(target_lang),
            style=style_instruction,
            formality=formality_instruction
        )

        # Add the glossary, if any
        if glossary:
            glossary_config = getattr(self.config, 'glossary', None)
            case_sensitive = getattr(glossary_config, 'case_sensitive_default', False)

            # Safety net: the glossary is normally passed in full so the model
            # sees the complete terminology. Only if the assembled prompt would
            # not fit the model's context window is it reduced to the terms that
            # actually occur in this text - otherwise the server would silently
            # truncate the request.
            effective_glossary = glossary
            budget = self._prompt_budget_chars()
            projected = len(system_content) + len(text) + self._glossary_chars(glossary)
            if budget and projected > budget:
                effective_glossary = self._relevant_glossary(glossary, text, case_sensitive)
                logger.info(
                    f"Glossary reduced from {len(glossary)} to "
                    f"{len(effective_glossary)} entries to fit the context window"
                )

            if effective_glossary:
                glossary_text = f"\n\n{texts['glossary_header']}\n"
                if case_sensitive:
                    glossary_text += f"{texts['glossary_case']}\n"
                for source_term, target_term in effective_glossary.items():
                    glossary_text += f"- '{source_term}' → '{target_term}'\n"
                system_content += glossary_text

        # Add the context, if any
        if context:
            system_content += f"\n\n{texts['context_header']}\n{context}"

        if extra_instructions:
            system_content += f"\n\n{extra_instructions}"

        messages = [
            Message(MessageRole.SYSTEM, system_content),
            Message(MessageRole.USER, f"{texts['user_message']}\n\n{text}")
        ]

        response = await self.complete(messages, **kwargs)

        # A response cut off at the length limit must not be used as if it
        # were complete: the end of the text would silently be missing. The
        # caller splits the text and translates the parts instead.
        if getattr(response, 'finish_reason', None) == 'length':
            raise LLMTruncatedError(
                f"Answer cut off at the length limit ({len(text)} characters sent)",
                partial=response.content or "")

        return response.content.strip()

    async def detect_language(self, text: str) -> str:
        """
        Detect the language of a text, with robust error handling.

        Args:
            text: text to analyse

        Returns:
            str: detected language (ISO code)
        """
        if not self._check_event_loop():
            logger.error("Event loop not available for language detection")
            return 'en'  # Fallback

        try:
            messages = [
                Message(
                    MessageRole.SYSTEM,
                    "Du bist ein Spracherkennungs-System. Antworte NUR mit dem ISO 639-1 "
                    "Sprachcode (z.B. 'de' für Deutsch, 'en' für Englisch, 'fr' für Französisch)."
                ),
                Message(
                    MessageRole.USER,
                    f"Welche Sprache ist das? Antworte nur mit dem Sprachcode:\n\n{text[:200]}"
                )
            ]

            response = await self.complete(messages, temperature=0.0, max_tokens=10)
            detected = response.content.strip().lower()

            # Validate the language code
            valid_codes = ['de', 'en', 'fr', 'es', 'it', 'pt', 'nl', 'pl', 'ru', 'zh', 'ja', 'ko']
            if detected in valid_codes:
                logger.info(f"Detected language: {detected}")
                return detected

        except Exception as e:
            logger.error(f"Language detection failed: {e}")

        logger.warning(f"Language detection failed or invalid, falling back to 'en'")
        return 'en'

    async def _load_translation_prompt(self) -> str:
        """Load the base translation prompt"""
        prompt_path = resolve_path(prompt_texts.PROMPT_FILES[self._prompt_language()])
        if prompt_path.exists():
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()

        # Fallback prompt with style placeholder
        return """Du bist ein professioneller Übersetzer.

Übersetze den gegebenen Text von {source_lang} nach {target_lang}.

Beachte dabei:
- Behalte die Struktur und Formatierung bei
- {style}
- Sei präzise und akkurat
- Behalte technische Begriffe bei, wenn sie üblicherweise nicht übersetzt werden

Übersetze NUR den Text, füge keine Erklärungen oder Kommentare hinzu."""

    def _prompt_language(self) -> str:
        """Language of the instructions to the model (translation.prompt_language)."""
        translation = getattr(self.config, 'translation', None)
        return prompt_texts.normalize_prompt_language(getattr(translation, 'prompt_language', 'de'))

    def _get_language_name(self, lang_code: str) -> str:
        """Language name as used inside the prompt."""
        return prompt_texts.language_name(lang_code, self._prompt_language())

    def estimate_tokens(self, text: str, language: str = 'en') -> int:
        """
        Estimate the number of tokens in a text.

        Args:
            text: text to estimate
            language: language of the text

        Returns:
            int: estimated token count
        """
        chars_per_token = self.config.get_chars_per_token(language)
        return int(len(text) / chars_per_token)


class LLMClientManager:
    """
    Thread-safe LLM client manager for shared client instances.
    """

    def __init__(self):
        self._clients: Dict[str, LLMClient] = {}
        self._lock = threading.Lock()
        self._shutdown = False
        atexit.register(self.cleanup)

    def get_client(self, config=None) -> LLMClient:
        """
        Get or create a client instance (thread-safe).

        Args:
            config: optional Config object

        Returns:
            LLMClient: client instance
        """
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Manager has been shut down")

            # Use config hash as key
            config = config or get_config()
            key = str(hash(str(config.llm.api_base) + config.llm.model))

            if key not in self._clients:
                self._clients[key] = LLMClient(config)
                logger.info(f"Created new standard LLM client instance: {key}")

            return self._clients[key]

    async def start_all(self):
        """Start all client instances"""
        with self._lock:
            for client in self._clients.values():
                await client.start()

    async def close_all(self):
        """Close all client instances gracefully"""
        with self._lock:
            if self._shutdown:
                return

            self._shutdown = True
            close_tasks = []

            for client in self._clients.values():
                close_tasks.append(client.close())

            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)

            self._clients.clear()
            logger.info("All LLM clients closed")

    def cleanup(self):
        """Synchronous cleanup for atexit"""
        if self._clients and not self._shutdown:
            try:
                # Try async cleanup first
                if asyncio.get_event_loop().is_running():
                    asyncio.create_task(self.close_all())
                else:
                    asyncio.run(self.close_all())
            except Exception as e:
                logger.error(f"Manager cleanup error: {e}")


# Global manager instance for shared client access
_client_manager: Optional[LLMClientManager] = None
_manager_lock = threading.Lock()


def get_client_manager() -> LLMClientManager:
    """Get the global client manager instance (thread-safe)"""
    global _client_manager
    with _manager_lock:
        if _client_manager is None:
            _client_manager = LLMClientManager()
        return _client_manager


@asynccontextmanager
async def get_llm_client(config=None):
    """
    Context manager for thread-safe LLM client access.

    Args:
        config: optional Config object

    Yields:
        LLMClient: started client
    """
    manager = get_client_manager()
    client = manager.get_client(config)

    try:
        await client.start()
        yield client
    finally:
        # The client is managed by the manager and not closed here
        pass


async def close_all_clients():
    """Close all LLM client instances"""
    manager = get_client_manager()
    await manager.close_all()
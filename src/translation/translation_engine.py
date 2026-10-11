# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Translation engine of the document translation system.
"""

import asyncio
import logging
import time
import threading
import random
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable, Set, Tuple
from enum import Enum

from ..config import get_config
from ..llm_client import (LLMClient, Message, MessageRole, get_client_manager,
                          LLMConnectionError, LLMTimeoutError, LLMRateLimitError,
                          LLMTruncatedError)
from ..markdown_utils import is_table_separator, split_table_row, is_valid_table_lines
from ..chunking import SimpleChunk, SimpleChunkingResult
from ..models.document import MarkdownDocument

logger = logging.getLogger(__name__)


_SENTENCE_END_RE = re.compile(r'(?<=[.!?:;。！？])\s+')


def split_text_in_two(text: str):
    """
    Split text near its middle at the most natural boundary: a paragraph
    break, else a line break, else a sentence end, else a space. Returns
    (first, separator, second), or None if the text cannot be split. A
    Markdown table or code block is never split inside a line.
    """
    middle = len(text) // 2

    def nearest(positions):
        candidates = [p for p in positions if 0 < p[0] < len(text)]
        return min(candidates, key=lambda p: abs(p[0] - middle)) if candidates else None

    for pattern in (r'\n\s*\n', r'\n'):
        found = nearest([(m.start(), m.end()) for m in re.finditer(pattern, text)])
        if found:
            return text[:found[0]], text[found[0]:found[1]], text[found[1]:]
    found = nearest([(m.start(), m.end()) for m in _SENTENCE_END_RE.finditer(text)])
    if found is None:
        found = nearest([(m.start(), m.end()) for m in re.finditer(r'\s+', text)])
    if found is None:
        return None
    return text[:found[0]], text[found[0]:found[1]], text[found[1]:]


_FINDINGS_EN = {
    'list_items': "Structure: {source} list items in the source, {target} in the translation",
    'table_rows': "Structure: {source} table rows in the source, {target} in the translation",
    'numbers': "Numbers differ (missing: {missing}; added: {added})",
    'length': "Suspicious length: translation {target} characters vs. {source} in the source",
    'terminology': "Terminology: '{source}' -> '{target}' not applied",
    'content': "Content: {detail}",
}


def render_finding_en(code: str, params: Dict[str, Any]) -> str:
    """English text of a quality finding (logs, API users)."""
    template = _FINDINGS_EN.get(code)
    return template.format(**params) if template else f"{code}: {params}"


class TranslationStatus(Enum):
    """Status of a translation task."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


class TableStructure:
    """Analyzes and manages table structure for translation."""

    def __init__(self, content: str):
        self.content = content
        self.lines = [line.strip() for line in content.split('\n') if line.strip()]
        self.is_valid_table = self._validate_table()
        self.headers, self.separator, self.data_rows = self._parse_table()

    def _validate_table(self) -> bool:
        """Validate if content is a proper markdown table."""
        # Shared definition, see src/markdown_utils.py.
        return is_valid_table_lines(self.lines)

    def _parse_table(self) -> Tuple[List[str], str, List[List[str]]]:
        """Parse table into headers, separator, and data rows."""
        if not self.is_valid_table or len(self.lines) < 3:
            return [], "", []

        # Extract header
        header_line = self.lines[0]
        headers = self._parse_table_row(header_line)

        # Extract separator
        separator = self.lines[1]

        # Extract data rows
        data_rows = []
        for line in self.lines[2:]:
            row_data = self._parse_table_row(line)
            if row_data:
                data_rows.append(row_data)

        return headers, separator, data_rows

    def _parse_table_row(self, line: str) -> List[str]:
        """Parse a single table row into cells."""
        if not line:
            return []
        return split_table_row(line)

    def reconstruct_table(self, translated_headers: List[str], translated_data: List[List[str]]) -> str:
        """Reconstruct table from translated parts."""
        if not translated_headers:
            return self.content

        lines = []

        # Header line
        header_line = '| ' + ' | '.join(translated_headers) + ' |'
        lines.append(header_line)

        # Separator line (keep original formatting)
        lines.append(self.separator)

        # Data lines
        for row in translated_data:
            # Ensure row has same number of columns as headers
            padded_row = (row + [''] * len(translated_headers))[:len(translated_headers)]
            row_line = '| ' + ' | '.join(padded_row) + ' |'
            lines.append(row_line)

        return '\n'.join(lines)


@dataclass
class TranslationContext:
    """Context information for translating a chunk."""
    glossary: Dict[str, str] = field(default_factory=dict)
    key_terms: List[Dict[str, str]] = field(default_factory=list)
    # Tail of an already translated passage of the same document. Gives the
    # model the established tone, register and terminology (translation
    # context modes "two_wave" and "sequential").
    style_reference: Optional[str] = None
    style: str = "professional"
    formality: str = "formal"  # 'formal' (Sie) or 'informal' (Du)
    source_language: str = "auto"
    target_language: str = "en"
    document_title: Optional[str] = None
    current_section: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """Convert context to prompt-ready format."""
        context_parts = []

        if self.document_title:
            context_parts.append(f"DOCUMENT: {self.document_title}")

        if self.current_section:
            context_parts.append(f"CURRENT SECTION: {self.current_section}")

        # Neighbouring text is reference only - it must never be translated
        # again, it only resolves references across chunk boundaries
        # (pronouns, "this application", continuing sentences).
        preceding = self.metadata.get('preceding_text')
        following = self.metadata.get('following_text')
        if preceding or following:
            neighbour = ("SURROUNDING TEXT (for reference only - do NOT translate "
                         "or repeat this, it is provided so that references and "
                         "pronouns can be resolved correctly):\n")
            if preceding:
                neighbour += f"[text before this section]: ...{preceding}\n"
            if following:
                neighbour += f"[text after this section]: {following}...\n"
            context_parts.append(neighbour)

        if self.key_terms:
            # Terminology already established for this document. Passed to
            # every chunk so the same source term is rendered identically
            # throughout, which chunk-local translation cannot achieve.
            terms = "\n".join(
                f"- '{t['source']}' -> '{t['target']}'"
                for t in self.key_terms if t.get('source') and t.get('target')
            )
            if terms:
                context_parts.append(
                    "DOCUMENT TERMINOLOGY (use these renderings consistently):\n" + terms
                )

        if self.style_reference:
            context_parts.append(
                "STYLE REFERENCE (an already translated passage of this document "
                "- match its tone, register, form of address and terminology; do "
                "NOT translate, repeat or continue it):\n" + self.style_reference)

        return "\n\n".join(context_parts)


@dataclass
class TranslationTask:
    """Represents a single translation task."""
    chunk: SimpleChunk
    chunk_index: int
    total_chunks: int
    context: TranslationContext
    status: TranslationStatus = TranslationStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    task_id: Optional[str] = None

    def get_duration(self) -> Optional[float]:
        """Get task duration in seconds."""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'chunk_index': self.chunk_index,
            'total_chunks': self.total_chunks,
            'status': self.status.value,
            'result': self.result,
            'error': self.error,
            'attempts': self.attempts,
            'duration': self.get_duration(),
            'token_count': self.chunk.token_count,
            'task_id': self.task_id
        }


@dataclass
class TranslationResult:
    """Result of a complete translation operation."""
    success: bool
    translated_chunks: List[Dict[str, Any]]
    total_time: float
    total_tokens: int
    failed_chunks: List[int] = field(default_factory=list)
    cancelled_chunks: List[int] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)

    def get_summary(self) -> str:
        """Get summary of translation result."""
        success_count = len(self.translated_chunks) - len(self.failed_chunks) - len(self.cancelled_chunks)
        total_chunks = len(self.translated_chunks)
        success_rate = (success_count / total_chunks * 100) if total_chunks > 0 else 0

        status = "completed" if self.success else "completed with errors"
        if self.cancelled_chunks:
            status = "partially cancelled"

        return (f"Translation {status}: "
                f"{total_chunks} chunks, "
                f"{success_rate:.1f}% success rate, "
                f"{self.total_time:.1f}s total time")


class TableAwareTranslationEngine:
    """
    Translation engine with specialized table handling and optimized progress reporting.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None, config=None):
        """Initialize the translation engine."""
        self.config = config or get_config()

        if llm_client is None:
            raise ValueError("LLM client is required - pass existing instance for coordination")
        self.llm_client = llm_client

        self.max_workers = self.config.parallel_processing.max_workers
        self.rate_limit = self.config.parallel_processing.rate_limit
        self.max_retries = self.config.llm.max_retries
        self.retry_delay = self.config.llm.retry_delay
        # Cap the exponential backoff so a persistently failing chunk cannot
        # stall the whole job for many minutes. Falls back to a sane default
        # if the config does not define it.
        self.max_retry_delay = getattr(self.config.llm, 'max_retry_delay', 30)

        # Task management with thread safety
        self._active_tasks: Set[asyncio.Task] = set()
        self._task_lock = threading.Lock()
        self._shutdown = False
        self._shutdown_event = asyncio.Event()

        # Progress callbacks with thread-safe handling
        self.progress_callbacks: List[Callable] = []
        self._callback_lock = threading.Lock()
        self._suppress_status_updates = False  # Flag to suppress conflicting status updates

        # Statistics
        self.stats = {
            'total_chunks': 0,
            'completed_chunks': 0,
            'failed_chunks': 0,
            'cancelled_chunks': 0,
            'total_retries': 0,
            'total_tokens': 0,
            'total_time': 0,
            'tables_translated': 0,
            'table_rows_translated': 0
        }

        # Called with (chunk_index, text) when a section is finished; used to
        # show partial results while the job is running.
        self.on_chunk_done: Optional[Callable[[int, str], None]] = None

        # No atexit registration: one engine is created per job. An atexit
        # callback would keep every engine alive until the process exits.
        logger.info(f"TableAwareTranslationEngine initialized with max_workers={self.max_workers}")

    def _check_event_loop(self) -> bool:
        """Check if event loop is available and not closed"""
        try:
            loop = asyncio.get_running_loop()
            return not loop.is_closed()
        except RuntimeError:
            return False

    async def translate_document(
        self,
        chunking_result: SimpleChunkingResult,
        source_lang: str,
        target_lang: str,
        style: str = "professional",
        glossary: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable] = None,
        formality: str = "formal"
    ) -> TranslationResult:
        """
        Translate all chunks of a document with table awareness.
        Progress reporting is coordinated to avoid conflicting updates.
        """
        if self._shutdown:
            raise RuntimeError("TranslationEngine is shut down")

        if not self._check_event_loop():
            raise RuntimeError("Event loop not available for translation")

        logger.info(f"=== TABLE-AWARE TRANSLATION STARTED ===")
        logger.info(f"Style received: '{style}'")
        logger.info(f"Formality received: '{formality}'")
        logger.info(f"Source: {source_lang} -> Target: {target_lang}")
        logger.info(f"Chunks to translate: {len(chunking_result.chunks)}")
        logger.info(f"Glossary terms: {len(glossary) if glossary else 0}")

        start_time = time.time()

        # Check whether the external progress callback wants exclusive control
        if progress_callback:
            with self._callback_lock:
                self.progress_callbacks.append(progress_callback)
                # Check if external callback wants to suppress our status updates
                try:
                    # Test call to see if callback returns False (suppress status updates)
                    test_data = {'current': 0, 'total': 1, 'message': 'test'}
                    if asyncio.iscoroutinefunction(progress_callback):
                        result = await progress_callback(test_data)
                    else:
                        result = progress_callback(test_data)

                    if result is False:
                        self._suppress_status_updates = True
                        logger.debug("External progress callback requested status suppression")
                except:
                    pass  # If test fails, proceed normally

        self._reset_stats()
        self.stats['total_chunks'] = len(chunking_result.chunks)

        effective_glossary = await self.prepare_glossary(
            chunking_result.chunks, source_lang, target_lang, style, formality, glossary)

        # Create base context. Terminology flows ONLY via the glossary (the
        # GLOSSAR block in the prompt); populating key_terms as well would put
        # every entry into the system message twice.
        base_context = TranslationContext(
            glossary=effective_glossary,
            style=style,
            formality=formality,
            source_language=source_lang,
            target_language=target_lang
        )

        logger.info(f"TranslationContext created with style: '{base_context.style}', formality: '{base_context.formality}'")

        # Create translation tasks
        tasks = []
        for i, chunk in enumerate(chunking_result.chunks):
            task = TranslationTask(
                chunk=chunk,
                chunk_index=i,
                total_chunks=len(chunking_result.chunks),
                context=self._create_chunk_context(base_context, chunk, i, chunking_result.chunks),
                task_id=f"chunk_{i}_{int(time.time())}"
            )
            tasks.append(task)

        try:
            translated_tasks = await self._execute_by_context_mode(tasks)

            # Process results
            translated_chunks = []
            failed_chunks = []
            cancelled_chunks = []

            for task in translated_tasks:
                if task.status == TranslationStatus.CANCELLED:
                    cancelled_chunks.append(task.chunk_index)
                    continue

                # Completed chunks and failed-but-recovered chunks (which fell
                # back to their original text) are both kept, in order, so the
                # document is never silently truncated.
                if task.result:
                    is_failed = (task.status == TranslationStatus.FAILED)
                    translated_chunks.append({
                        'position': task.chunk.position,
                        'original_content': task.chunk.content,
                        'translated_content': task.result,
                        'metadata': task.chunk.metadata,
                        'token_count': task.chunk.token_count,
                        'attempts': task.attempts,
                        'task_id': task.task_id,
                        'is_table': task.chunk.is_table,
                        'is_code': task.chunk.is_code,
                        'failed': is_failed
                    })
                    if is_failed:
                        failed_chunks.append(task.chunk_index)
                else:
                    failed_chunks.append(task.chunk_index)

            total_time = time.time() - start_time
            self.stats['total_time'] = total_time
            self.stats['cancelled_chunks'] = len(cancelled_chunks)

            result = TranslationResult(
                success=(len(failed_chunks) == 0 and len(cancelled_chunks) == 0),
                translated_chunks=translated_chunks,
                total_time=total_time,
                total_tokens=self.stats['total_tokens'],
                failed_chunks=failed_chunks,
                cancelled_chunks=cancelled_chunks,
                statistics=self.stats.copy()
            )

            logger.info(f"=== TABLE-AWARE TRANSLATION COMPLETED ===")
            logger.info(f"Result: {result.get_summary()}")
            logger.info(f"Tables processed: {self.stats['tables_translated']}, "
                       f"Table rows: {self.stats['table_rows_translated']}")

            return result

        finally:
            # Reset flags and cleanup
            self._suppress_status_updates = False
            if progress_callback:
                with self._callback_lock:
                    if progress_callback in self.progress_callbacks:
                        self.progress_callbacks.remove(progress_callback)

    async def translate_chunk(
        self,
        chunk: SimpleChunk,
        context: TranslationContext
    ) -> str:
        """
        Translate a single chunk with table-aware processing.
        """
        if self._shutdown:
            raise asyncio.CancelledError("Engine is shutting down")

        logger.debug(f"Translating chunk {context.metadata.get('chunk_index', '?')} with style: '{context.style}'")

        # Special handling for tables
        if chunk.is_table:
            logger.debug("Using specialized table translation")
            result = await self._translate_table_chunk(chunk, context)
            self.stats['tables_translated'] += 1
            return result

        # Special handling for code blocks
        elif chunk.is_code:
            logger.debug("Using specialized code translation")
            result = await self._translate_code_chunk(chunk, context)
            return result

        # Regular text translation
        else:
            logger.debug("Using standard text translation")
            result = await self._translate_text_splitting(chunk.content, context)
            logger.debug(f"Chunk {context.metadata.get('chunk_index', '?')} translated with style '{context.style}', formality '{context.formality}'")
            return result

    # How deep a text cut off at the answer limit is split further.
    MAX_SPLIT_DEPTH = 4

    async def _translate_text_splitting(self, text: str, context: TranslationContext,
                                        depth: int = 0) -> str:
        """
        Translate running text; if the answer is cut off at the length limit,
        split the text and translate the parts.

        Nothing of a cut-off answer is used: the parts are translated anew and
        joined with the separator at which the text was split.
        """
        try:
            result = await self.llm_client.translate_text(
                text=text,
                source_lang=context.source_language,
                target_lang=context.target_language,
                style=context.style,
                context=context.to_prompt_context(),
                glossary=context.glossary,
                formality=context.formality
            )
            return self._clean_llm_output(result, text)
        except LLMTruncatedError:
            parts = split_text_in_two(text)
            if depth >= self.MAX_SPLIT_DEPTH or parts is None:
                raise
            first, separator, second = parts
            logger.info(f"Answer cut off; translating the section in two parts "
                        f"({len(first)} + {len(second)} chars, depth {depth + 1})")
            self.stats['split_sections'] = self.stats.get('split_sections', 0) + 1
            left = await self._translate_text_splitting(first, context, depth + 1)
            right = await self._translate_text_splitting(second, context, depth + 1)
            return f"{left}{separator}{right}"

    # Common conversational prefaces local models add despite the prompt.
    _PREAMBLE_RE = re.compile(
        r'^\s*(hier ist (die|deine)?\s*[^\n:]{0,40}:|'
        r'here (is|\'s) (the )?[^\n:]{0,40}:|'
        r'(die )?übersetzung(\s+lautet)?:|translation:|'
        r'übersetzter text:|translated text:)\s*',
        re.IGNORECASE,
    )

    # Reasoning models may return their reasoning inline, depending on the
    # server: <think>...</think> (or only the closing tag, when the server
    # strips the opening one).
    _REASONING_BLOCK_RE = re.compile(
        r'<(think|thinking|reasoning)>.*?</\1>\s*', re.DOTALL | re.IGNORECASE)
    _REASONING_CLOSE_RE = re.compile(r'</(think|thinking|reasoning)>', re.IGNORECASE)

    @classmethod
    def _strip_reasoning(cls, text: str) -> str:
        """Remove inline reasoning blocks from a model answer."""
        if not text:
            return text
        cleaned = cls._REASONING_BLOCK_RE.sub('', text)
        # Dangling closing tag: everything before it is reasoning.
        closings = list(cls._REASONING_CLOSE_RE.finditer(cleaned))
        if closings:
            cleaned = cleaned[closings[-1].end():]
        return cleaned.strip()

    def _clean_llm_output(self, text: str, original: str) -> str:
        """
        Remove artefacts local models tend to add despite the prompt:
        inline reasoning blocks, a leading "Here is the translation:" preface
        and a code fence that wraps the *entire* answer (but not fences that
        are genuine content).
        """
        if not text:
            return text

        cleaned = self._strip_reasoning(text.strip())
        if not cleaned:
            return cleaned

        # Strip a leading conversational preface.
        cleaned = self._PREAMBLE_RE.sub('', cleaned, count=1).strip()

        # Strip a code fence wrapping the whole response, unless the original
        # chunk was itself a fenced code block (then the fence is legitimate).
        if cleaned.startswith('```') and cleaned.rstrip().endswith('```') \
                and not original.strip().startswith('```'):
            lines = cleaned.split('\n')
            if len(lines) >= 2:
                lines = lines[1:]  # drop opening fence (with optional language)
                if lines and lines[-1].strip() == '```':
                    lines = lines[:-1]
                cleaned = '\n'.join(lines).strip()

        return cleaned or text.strip()

    async def _translate_table_chunk(self, chunk: SimpleChunk, context: TranslationContext) -> str:
        """
        Specialized translation for table chunks.
        """
        logger.debug("=== SPECIALIZED TABLE TRANSLATION ===")

        table_structure = TableStructure(chunk.content)

        if not table_structure.is_valid_table:
            logger.warning("Invalid table structure, falling back to regular translation")
            return await self.llm_client.translate_text(
                text=chunk.content,
                source_lang=context.source_language,
                target_lang=context.target_language,
                style=context.style,
                context=context.to_prompt_context(),
                glossary=context.glossary,
                formality=context.formality
            )

        logger.debug(f"Table structure: {len(table_structure.headers)} headers, "
                   f"{len(table_structure.data_rows)} data rows")

        # One cache per table: identical cells are translated once.
        cache: Dict = {}

        # Translate headers with appropriate context
        translated_headers = await self._translate_table_headers(
            table_structure.headers, context, cache
        )

        # Translate data rows
        translated_data_rows = await self._translate_table_data(
            table_structure.data_rows, context, translated_headers, cache
        )

        # Reconstruct table
        result = table_structure.reconstruct_table(translated_headers, translated_data_rows)

        # Update statistics
        self.stats['table_rows_translated'] += len(table_structure.data_rows)

        logger.debug(f"Table translation completed: {len(translated_headers)} headers, "
                   f"{len(translated_data_rows)} data rows")

        return result

    async def _translate_cell_text(
        self,
        text: str,
        context: TranslationContext,
        extra_context: str,
        cache: Dict[Tuple[str, str], "asyncio.Future"]
    ) -> str:
        """
        Translate one table cell (or header), sharing work between identical cells.

        Cells with the same text and the same column context are translated
        once per table; repeated values (status, departments, yes/no) are the
        common case in real tables. On failure the original text is kept.
        """
        key = (text, extra_context)
        future = cache.get(key)
        if future is None:
            async def run() -> str:
                try:
                    translated = await self.llm_client.translate_text(
                        text=text,
                        source_lang=context.source_language,
                        target_lang=context.target_language,
                        style=context.style,
                        context=f"{context.to_prompt_context()}\n\n{extra_context}",
                        glossary=context.glossary,
                        formality=context.formality
                    )
                    return self._clean_llm_output(translated, text) or text
                except Exception as e:
                    # Cell text is not logged: it is document content.
                    logger.warning(f"Table cell translation failed, keeping original "
                                   f"({len(text)} chars): {type(e).__name__}")
                    self.stats['failed_cells'] = self.stats.get('failed_cells', 0) + 1
                    return text
            future = asyncio.ensure_future(run())
            cache[key] = future
        return await future

    async def _translate_table_headers(
        self,
        headers: List[str],
        context: TranslationContext,
        cache: Optional[Dict] = None
    ) -> List[str]:
        """Translate table headers with header-specific context."""
        if not headers:
            return []

        cache = {} if cache is None else cache
        logger.debug(f"Translating {len(headers)} table headers")

        header_context = "TABLE HEADERS - These are column headings that describe the data categories below."

        async def one(header: str) -> str:
            if not header.strip():
                return ""
            return await self._translate_cell_text(header, context, header_context, cache)

        # Concurrency is bounded by the client's request semaphore.
        return list(await asyncio.gather(*(one(h) for h in headers)))

    async def _translate_table_data(
        self,
        data_rows: List[List[str]],
        context: TranslationContext,
        translated_headers: List[str],
        cache: Optional[Dict] = None
    ) -> List[List[str]]:
        """Translate table data rows with data-specific context."""
        if not data_rows:
            return []

        cache = {} if cache is None else cache
        logger.debug(f"Translating {len(data_rows)} table data rows")

        # Create data-specific context
        header_info = ", ".join(translated_headers[:3])  # First 3 headers for context
        data_context = (f"TABLE DATA - These are data values in a table with columns: {header_info}{'...' if len(translated_headers) > 3 else ''}. "
                       f"Translate the content while preserving data meaning and format.")

        async def one(cell_idx: int, cell: str) -> str:
            if not cell.strip():
                return ""
            # Cells without any letters (numbers, dates, IDs, currency,
            # percentages) carry no translatable text. Sending them to the
            # model only costs a round trip and risks the value being
            # altered, so they are passed through unchanged.
            if not self._is_translatable_cell(cell):
                return cell
            column_context = ""
            if cell_idx < len(translated_headers):
                column_context = f" (Column: {translated_headers[cell_idx]})"
            return await self._translate_cell_text(
                cell, context, f"{data_context}{column_context}", cache)

        table_mode = getattr(getattr(self.config, 'translation', None), 'table_mode', 'cell')
        if table_mode == 'row':
            async def whole_row(row: List[str]) -> List[str]:
                translated = await self._translate_row(row, context, translated_headers, data_context)
                if translated is not None:
                    return translated
                # The answer did not have the right cells: translate them one by one.
                return list(await asyncio.gather(*(one(c, cell) for c, cell in enumerate(row))))
            rows = await asyncio.gather(*(whole_row(row) for row in data_rows))
            return [list(r) for r in rows]

        # All cells of the table are scheduled at once; the client's request
        # semaphore bounds how many are actually in flight.
        rows = await asyncio.gather(*(
            asyncio.gather(*(one(c, cell) for c, cell in enumerate(row)))
            for row in data_rows
        ))
        return [list(r) for r in rows]

    _CELL_MARKER_RE = re.compile(r'<c(\d+)>(.*?)</c\1>', re.DOTALL)

    async def _translate_row(
        self,
        row: List[str],
        context: TranslationContext,
        translated_headers: List[str],
        data_context: str
    ) -> Optional[List[str]]:
        """
        Translate all cells of a table row in one request (table_mode "row").

        Cells are wrapped in <cN>...</cN>; the answer must contain exactly the
        same markers. Returns None if it does not, so the caller can fall back
        to translating cell by cell.
        """
        wanted = {i: cell for i, cell in enumerate(row)
                  if cell.strip() and self._is_translatable_cell(cell)}
        if not wanted:
            return list(row)
        columns = ", ".join(f"<c{i + 1}> = {translated_headers[i]}"
                            for i in wanted if i < len(translated_headers))
        text = " ".join(f"<c{i + 1}>{cell}</c{i + 1}>" for i, cell in wanted.items())
        instructions = (
            "TABLE ROW: every <cN>...</cN> is one cell of the same table row. "
            "Translate the content of every cell and return every cell with its "
            "marker, in the same order. Do not add, remove or merge markers. "
            "Output nothing else." + (f" Columns: {columns}" if columns else ""))
        try:
            answer = await self.llm_client.translate_text(
                text=text,
                source_lang=context.source_language,
                target_lang=context.target_language,
                style=context.style,
                context=f"{context.to_prompt_context()}\n\n{data_context}",
                glossary=context.glossary,
                formality=context.formality,
                extra_instructions=instructions
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Row translation failed, translating cells one by one: {type(e).__name__}")
            return None
        found = {int(m.group(1)) - 1: m.group(2).strip()
                 for m in self._CELL_MARKER_RE.finditer(self._strip_reasoning(answer or ""))}
        if set(found) != set(wanted) or any(not v for v in found.values()):
            return None
        return [found.get(i, cell) for i, cell in enumerate(row)]

    @staticmethod
    def _is_translatable_cell(cell: str) -> bool:
        """
        True if a table cell contains text worth sending to the model.

        Pure numbers, dates, percentages, currency amounts and short codes have
        no translatable content; a cell needs at least two consecutive letters
        to qualify (so 'X-9' or 'kg' style unit codes are not needlessly sent,
        while real words always are).
        """
        text = (cell or "").strip()
        if not text:
            return False
        return bool(re.search(r'[^\W\d_]{2,}', text, re.UNICODE))

    async def _translate_code_chunk(self, chunk: SimpleChunk, context: TranslationContext) -> str:
        """
        Specialized translation for code chunks.
        """
        logger.debug("=== SPECIALIZED CODE TRANSLATION ===")

        # Create code-specific context
        code_context = ("CODE BLOCK - Translate comments and documentation strings only. "
                       "Preserve all code syntax, variable names, function names, and structure exactly. "
                       "Only translate human-readable text like comments and string literals.")

        try:
            result = await self.llm_client.translate_text(
                text=chunk.content,
                source_lang=context.source_language,
                target_lang=context.target_language,
                style=context.style,
                context=f"{context.to_prompt_context()}\n\n{code_context}",
                glossary=context.glossary,
                formality=context.formality
            )

            logger.debug("Code block translation completed")
            return result

        except Exception as e:
            logger.error(f"Code translation failed: {e}")
            raise

    # Valid values of translation.context_mode.
    CONTEXT_MODES = ("two_wave", "sequential", "parallel")

    def _context_mode(self) -> str:
        mode = getattr(getattr(self.config, 'translation', None), 'context_mode', 'two_wave')
        return mode if mode in self.CONTEXT_MODES else 'two_wave'

    def _style_reference_chars(self) -> int:
        return int(getattr(getattr(self.config, 'translation', None),
                           'style_reference_chars', 600) or 0)

    @staticmethod
    def _is_text_task(task: "TranslationTask") -> bool:
        return not task.chunk.is_table and not task.chunk.is_code

    def _style_sample(self, task: "TranslationTask") -> Optional[str]:
        """Tail of a completed text translation, used as style reference."""
        limit = self._style_reference_chars()
        if (limit <= 0 or task.status != TranslationStatus.COMPLETED
                or not task.result or not self._is_text_task(task)):
            return None
        return task.result.strip()[-limit:]

    async def _execute_by_context_mode(
        self,
        tasks: List["TranslationTask"]
    ) -> List["TranslationTask"]:
        """
        Run the translation tasks according to translation.context_mode.

        Sections are translated in small pieces because long inputs are not
        translated reliably. To keep the writing style consistent across
        sections, already translated text is handed on as STYLE REFERENCE:

        - "two_wave" (default): the first text section is translated alone;
          its translation is the style reference for all other sections,
          which then run in parallel. Costs the time of one section.
        - "sequential": every text section sees the end of the translation of
          the text section before it. Strongest continuity, but sections run
          one after another (no parallelism).
        - "parallel": no style reference; all sections run in parallel.
        """
        mode = self._context_mode()
        if mode == "parallel" or self._style_reference_chars() <= 0:
            return await self._execute_parallel_translations(tasks)

        text_tasks = [t for t in tasks if self._is_text_task(t)]
        if not text_tasks:
            return await self._execute_parallel_translations(tasks)

        if mode == "sequential":
            semaphore = asyncio.Semaphore(1)
            reference = None
            for task in tasks:
                if self._is_text_task(task):
                    task.context.style_reference = reference
                    await self._translate_with_retry(task, semaphore)
                    reference = self._style_sample(task) or reference
                else:
                    await self._translate_with_retry(task, semaphore)
            return tasks

        # two_wave
        first = text_tasks[0]
        await self._translate_with_retry(first, asyncio.Semaphore(1))
        reference = self._style_sample(first)
        if reference:
            for task in text_tasks[1:]:
                task.context.style_reference = reference
        rest = [t for t in tasks if t is not first]
        if rest:
            await self._execute_parallel_translations(rest)
        return tasks

    async def _execute_parallel_translations(
        self,
        tasks: List[TranslationTask]
    ) -> List[TranslationTask]:
        """Execute translations in parallel with rate limiting and task tracking."""
        semaphore = asyncio.Semaphore(self.max_workers)

        async_tasks = []
        for task in tasks:
            async_task = asyncio.create_task(
                self._translate_with_retry(task, semaphore)
            )

            with self._task_lock:
                self._active_tasks.add(async_task)

            async_task.add_done_callback(
                lambda t: self._active_tasks.discard(t)
            )

            async_tasks.append(async_task)

        try:
            completed_tasks = await asyncio.gather(*async_tasks, return_exceptions=True)

            for i, result in enumerate(completed_tasks):
                if isinstance(result, asyncio.CancelledError):
                    tasks[i].status = TranslationStatus.CANCELLED
                    tasks[i].error = "Task was cancelled"
                    logger.info(f"Task {i} was cancelled")
                elif isinstance(result, Exception):
                    tasks[i].status = TranslationStatus.FAILED
                    tasks[i].error = str(result)
                    logger.error(f"Task {i} failed with exception: {result}")
                else:
                    tasks[i] = result

        except Exception as e:
            logger.error(f"Parallel execution failed: {e}")
            await self._cancel_active_tasks()
            raise

        return tasks

    async def _translate_with_retry(
        self,
        task: TranslationTask,
        semaphore: asyncio.Semaphore
    ) -> TranslationTask:
        """Translate one task and report the finished section (see on_chunk_done)."""
        task = await self._translate_with_retry_inner(task, semaphore)
        callback = getattr(self, 'on_chunk_done', None)
        if callback is not None and task.status in (TranslationStatus.COMPLETED, TranslationStatus.FAILED):
            try:
                callback(task.chunk_index, task.result if task.result is not None else task.chunk.content)
            except Exception as e:
                logger.debug(f"on_chunk_done callback failed: {e}")
        return task

    async def _translate_with_retry_inner(
        self,
        task: TranslationTask,
        semaphore: asyncio.Semaphore
    ) -> TranslationTask:
        """Translate with retry logic and optimized progress reporting."""
        async with semaphore:
            if self._shutdown:
                task.status = TranslationStatus.CANCELLED
                return task

            task.start_time = time.time()
            task.status = TranslationStatus.IN_PROGRESS

            for attempt in range(self.max_retries):
                task.attempts = attempt + 1

                try:
                    if self._shutdown:
                        task.status = TranslationStatus.CANCELLED
                        return task

                    chunk_type = "TABLE" if task.chunk.is_table else "CODE" if task.chunk.is_code else "TEXT"

                    # Only report progress if not suppressed
                    if not self._suppress_status_updates:
                        await self._update_progress(
                            task,
                            f"Translating {chunk_type} chunk {task.chunk_index + 1}/{task.total_chunks} (style: {task.context.style})"
                        )

                    result = await self.translate_chunk(task.chunk, task.context)

                    if result and len(result.strip()) > 0:
                        task.result = result
                        task.status = TranslationStatus.COMPLETED
                        task.end_time = time.time()

                        self.stats['completed_chunks'] += 1
                        self.stats['total_tokens'] += task.chunk.token_count

                        logger.debug(f"Chunk {task.chunk_index} ({chunk_type}) translated successfully with style '{task.context.style}' (attempt {attempt + 1})")
                        return task
                    else:
                        raise ValueError("Empty translation result")

                except asyncio.CancelledError:
                    task.status = TranslationStatus.CANCELLED
                    task.end_time = time.time()
                    logger.info(f"Chunk {task.chunk_index} translation was cancelled")
                    # Propagate: the job itself is being cancelled. Swallowing
                    # it would let the job continue (and report "failed").
                    raise

                except (LLMConnectionError, LLMTimeoutError, LLMRateLimitError, LLMTruncatedError) as e:
                    # Already retried by LLMClient.complete() (or, for a cut-off
                    # answer, already split as far as possible). Retrying again
                    # here would multiply the attempts (and the waiting time).
                    task.status = TranslationStatus.FAILED
                    task.error = str(e)
                    task.end_time = time.time()
                    task.result = task.chunk.content
                    self.stats['failed_chunks'] += 1
                    logger.error(f"Chunk {task.chunk_index} failed, keeping original text: {e}")
                    return task

                except Exception as e:
                    logger.warning(f"Translation attempt {attempt + 1} failed for chunk {task.chunk_index}: {e}")

                    if attempt < self.max_retries - 1:
                        # Exponential backoff, capped, with light jitter.
                        delay = min(self.retry_delay * (2 ** attempt), self.max_retry_delay)
                        delay += random.uniform(0, min(1.0, delay * 0.1))

                        task.status = TranslationStatus.RETRYING

                        # Only report progress if not suppressed
                        if not self._suppress_status_updates:
                            await self._update_progress(
                                task,
                                f"Retrying chunk {task.chunk_index + 1} in {delay}s (attempt {attempt + 2}/{self.max_retries})"
                            )

                        if self._check_event_loop():
                            try:
                                await asyncio.sleep(delay)
                            except asyncio.CancelledError:
                                task.status = TranslationStatus.CANCELLED
                                raise
                        else:
                            logger.error("Event loop not available during retry delay")
                            break

                        self.stats['total_retries'] += 1
                    else:
                        task.status = TranslationStatus.FAILED
                        task.error = str(e)
                        task.end_time = time.time()
                        # Fall back to the original (untranslated) content so the
                        # section is preserved instead of silently dropped and
                        # the whole document discarded.
                        task.result = task.chunk.content
                        self.stats['failed_chunks'] += 1
                        logger.error(f"Chunk {task.chunk_index} failed after {self.max_retries} attempts, keeping original text: {e}")
                        return task

            return task

    def _create_chunk_context(
        self,
        base_context: TranslationContext,
        chunk: SimpleChunk,
        chunk_index: int,
        all_chunks: Optional[List[SimpleChunk]] = None
    ) -> TranslationContext:
        """Create specific context for a chunk."""
        import copy

        chunk_context = copy.deepcopy(base_context)

        if chunk.parent_heading:
            chunk_context.current_section = chunk.parent_heading

        # Give the model a glimpse of the neighbouring text so that references
        # across chunk boundaries can be resolved. Reference only - the prompt
        # states explicitly that it must not be translated.
        if all_chunks:
            if chunk_index > 0:
                prev = all_chunks[chunk_index - 1].content.strip()
                if prev and not all_chunks[chunk_index - 1].is_table:
                    chunk_context.metadata['preceding_text'] = prev[-300:]
            if chunk_index + 1 < len(all_chunks):
                nxt = all_chunks[chunk_index + 1].content.strip()
                if nxt and not all_chunks[chunk_index + 1].is_table:
                    chunk_context.metadata['following_text'] = nxt[:300]

        chunk_context.metadata['chunk_index'] = chunk_index
        chunk_context.metadata['chunk_position'] = chunk.position
        chunk_context.metadata['is_table'] = chunk.is_table
        chunk_context.metadata['is_code'] = chunk.is_code

        return chunk_context

    # Words that look like terms but carry no domain meaning.
    _TERM_STOPWORDS = {
        'der', 'die', 'das', 'dem', 'den', 'des', 'ein', 'eine', 'einer', 'eines',
        'und', 'oder', 'aber', 'auch', 'nicht', 'wird', 'werden', 'wurde', 'sind',
        'ist', 'sein', 'haben', 'hat', 'für', 'von', 'mit', 'bei', 'aus', 'nach',
        'the', 'this', 'that', 'these', 'those', 'and', 'for', 'with', 'from',
    }

    async def prepare_glossary(
        self,
        chunks: List[SimpleChunk],
        source_lang: str,
        target_lang: str,
        style: str,
        formality: str,
        glossary: Optional[Dict[str, str]]
    ) -> Dict[str, str]:
        """
        The glossary used for all sections: the automatic glossary (first pass,
        if enabled) with the user's entries taking precedence.
        """
        auto_glossary: Dict[str, str] = {}
        if getattr(self.config.translation, 'auto_glossary', True):
            try:
                auto_glossary = await self.build_document_glossary(
                    chunks, source_lang, target_lang,
                    style, formality, glossary or {},
                    max_terms=getattr(self.config.translation,
                                      'auto_glossary_max_terms', 100)
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Auto-glossary pass failed, continuing without it: {e}")

        # Manual glossary entries take precedence over derived ones.
        effective_glossary = {**auto_glossary, **(glossary or {})}
        # Remembered so that verify_translation() also checks adherence to the
        # auto-derived terms, not only the manually supplied ones.
        self._last_effective_glossary = dict(effective_glossary)
        return effective_glossary

    async def build_document_glossary(
        self,
        chunks: List[SimpleChunk],
        source_lang: str,
        target_lang: str,
        style: str = "professional",
        formality: str = "formal",
        existing_glossary: Optional[Dict[str, str]] = None,
        max_terms: int = 40
    ) -> Dict[str, str]:
        """
        First pass: derive a document-wide terminology list.

        The recurring domain terms of the document are collected, translated
        ONCE, and then handed to every chunk. Without this, each chunk is
        translated in isolation and the same source term can end up with
        different renderings in different sections.
        """
        existing_glossary = existing_glossary or {}
        candidates = self._extract_term_candidates(chunks, existing_glossary, max_terms)

        method = getattr(getattr(self.config, 'translation', None), 'auto_glossary_method', 'model')
        if method == 'model':
            glossary = await self._build_glossary_with_model(
                chunks, candidates, source_lang, target_lang, existing_glossary, max_terms)
            if glossary is not None:
                return glossary
            logger.info("Auto-glossary: model answer unusable, using the heuristic")

        if not candidates:
            logger.info("Auto-glossary: no recurring terms found")
            return {}

        logger.info(f"Auto-glossary: translating {len(candidates)} recurring terms")

        term_context = (
            "TERMINOLOGY LIST - translate each term as it should appear "
            "consistently throughout a document. Return ONLY the translated "
            "term, no explanation."
        )

        examples = getattr(self, '_term_examples', {}) or {}

        async def translate_term(term: str):
            context = term_context
            example = examples.get(term)
            if example:
                # The sentence disambiguates the term (reference only).
                context += f"\nThe term is used in this sentence (do not translate it): {example}"
            try:
                translated = await self.llm_client.translate_text(
                    text=term,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    style=style,
                    context=context,
                    glossary=existing_glossary,
                    formality=formality
                )
                translated = self._strip_reasoning(translated or "")
                return term, translated.strip().strip('."\'')
            except Exception as e:
                logger.warning(f"Auto-glossary: a term translation failed: {type(e).__name__}")
                return term, None

        # Terms are independent of each other - translate them concurrently.
        # Rate limiting is handled by the client's own semaphore.
        results = await asyncio.gather(*(translate_term(t) for t in candidates))

        auto_glossary: Dict[str, str] = {}
        for term, translated in results:
            # Guard against the model returning a sentence instead of a term.
            if translated and len(translated) < len(term) * 4 and '\n' not in translated:
                auto_glossary[term] = translated

        logger.info(f"Auto-glossary built with {len(auto_glossary)} entries")
        return auto_glossary

    async def _build_glossary_with_model(
        self,
        chunks: List[SimpleChunk],
        candidates: List[str],
        source_lang: str,
        target_lang: str,
        existing_glossary: Dict[str, str],
        max_terms: int
    ) -> Optional[Dict[str, str]]:
        """
        One request: the model selects the terms that need a consistent
        translation and translates them.

        The request contains as much of the document as fits the context window
        and the capitalisation-based candidates (with an example sentence each),
        which cover the whole document. Terms that do not occur in the document
        are discarded. Returns None if the answer cannot be used, so that the
        caller can fall back to the heuristic.
        """
        from ..prompt_texts import language_name

        document_text = "\n\n".join(c.content for c in chunks if not c.is_code)
        if not document_text.strip():
            return {}
        budget = None
        try:
            budget = self.llm_client._prompt_budget_chars()
        except Exception:
            pass
        excerpt_limit = max(4000, min(24000, (budget or 24000) - 8000))
        excerpt = document_text[:excerpt_limit]

        examples = getattr(self, '_term_examples', {}) or {}
        hint_lines = "\n".join(
            f"- {term}" + (f"  (e.g. \"{examples[term][:120]}\")" if examples.get(term) else "")
            for term in candidates[:max_terms * 2])

        source_name = language_name(source_lang, "en")
        target_name = language_name(target_lang, "en")
        system = (
            "You build the terminology list for translating one document from "
            f"{source_name} into {target_name}. Select up to {max_terms} terms "
            "that occur in the document and must be translated the same way "
            "everywhere: domain terms, names of institutions, roles, documents, "
            "procedures and products, recurring multi-word expressions. Leave out "
            "general words, people's names and terms that stay untranslated. "
            "Translate each selected term as it should appear in the translated "
            "document. Answer with JSON only, in this form: "
            '{"terms": [{"source": "term as in the document", "target": "translation"}]}'
        )
        if existing_glossary:
            system += ("\nThese terms are already fixed, do not include them: "
                       + ", ".join(list(existing_glossary)[:200]))
        user = f"DOCUMENT (excerpt):\n{excerpt}"
        if hint_lines:
            user += f"\n\nCANDIDATES found in the whole document (may contain noise):\n{hint_lines}"

        reserve = int(getattr(getattr(self.config, 'llm', None), 'reasoning_reserve_tokens', 0) or 0)
        try:
            # The answer is a term list, not a translation of the excerpt.
            response = await self.llm_client.complete([
                Message(MessageRole.SYSTEM, system),
                Message(MessageRole.USER, user),
            ], max_tokens=max_terms * 40 + 512 + reserve)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Auto-glossary request failed: {type(e).__name__}")
            return None

        entries = self._parse_glossary_json(getattr(response, 'content', '') or '')
        if entries is None:
            return None

        lowered_document = document_text.lower()
        existing_lower = {k.lower() for k in existing_glossary}
        glossary: Dict[str, str] = {}
        for source, target in entries:
            if not source or not target or '\n' in target:
                continue
            if source.lower() not in lowered_document or source.lower() in existing_lower:
                continue  # invented by the model, or already fixed by the user
            if len(target) > len(source) * 4 + 20:
                continue  # a sentence, not a term
            glossary[source] = target
            if len(glossary) >= max_terms:
                break
        logger.info(f"Auto-glossary (model): {len(glossary)} entries")
        return glossary

    def _parse_glossary_json(self, answer: str) -> Optional[List[Tuple[str, str]]]:
        """[(source, target)] from the model's JSON answer, or None."""
        import json
        text = self._strip_reasoning(answer)
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
        start, end = text.find('{'), text.rfind('}')
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return None
        terms = data.get('terms') if isinstance(data, dict) else None
        if not isinstance(terms, list):
            return None
        result = []
        for item in terms:
            if isinstance(item, dict):
                source = str(item.get('source', '')).strip()
                target = str(item.get('target', '')).strip().strip('."\'')
                result.append((source, target))
        return result

    # Sentence boundaries used to recognise sentence-initial words.
    _SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?:;])\s+')
    _MULTIWORD_RE = re.compile(r'\b[A-ZÄÖÜ][\wäöüß]+(?:[ \t][A-ZÄÖÜ][\wäöüß]+){1,2}\b')
    _SINGLEWORD_RE = re.compile(r'\b[A-ZÄÖÜ][\wäöüß]{4,}\b')

    def _iter_term_segments(self, chunks: List[SimpleChunk]):
        """
        Yield (text, is_heading) segments in which terms are searched.

        Table cells are separate segments so that words of neighbouring cells
        are never joined into one "term"; table separator rows are skipped.
        """
        for chunk in chunks:
            if chunk.is_code:
                continue
            for line in chunk.content.split('\n'):
                stripped = line.strip()
                if not stripped or is_table_separator(stripped):
                    continue
                is_heading = stripped.startswith('#')
                if '|' in stripped:
                    parts = split_table_row(stripped)
                else:
                    parts = [stripped]
                for part in parts:
                    # Strip markdown syntax and URLs before counting.
                    part = re.sub(r'`[^`]*`|https?://\S+|[#*_>\[\]()]', ' ', part)
                    if part.strip():
                        yield part.strip(), is_heading or '|' in stripped

    def _extract_term_candidates(
        self,
        chunks: List[SimpleChunk],
        existing_glossary: Dict[str, str],
        max_terms: int
    ) -> List[str]:
        """
        Collect recurring domain terms from the document.

        Uses frequency and capitalisation rather than word position: pairing
        source and target words by their index cannot work between languages
        with different word order.

        Noise filters:
        - a candidate must occur at least twice;
        - in running text, a capitalised word at the start of a sentence is
          not counted (capitalisation there carries no information), while
          headings and table cells count in full;
        - words of neighbouring table cells are never joined.

        Limitation: candidates are recognised by an initial capital letter, so
        the pass finds little in languages without capitalisation (e.g.
        Chinese, Japanese, Korean) and mostly proper names in English.
        """
        from collections import Counter

        existing_lower = {k.lower() for k in existing_glossary}
        # Leading articles/prepositions are not part of the term itself.
        leading = re.compile(
            r'^(?:der|die|das|dem|den|des|ein|eine|einer|eines|the|a|an)\s+',
            re.IGNORECASE)
        occurrences: Counter = Counter()
        examples: Dict[str, str] = {}

        def count(term: str, sentence: str):
            term = leading.sub('', term).strip()
            if term:
                occurrences[term] += 1
                examples.setdefault(term, sentence[:200])

        for segment, counts_initial in self._iter_term_segments(chunks):
            sentences = [segment] if counts_initial else self._SENTENCE_SPLIT_RE.split(segment)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                first_word_end = len(sentence.split(None, 1)[0]) if not counts_initial else 0
                # Multi-word proper terms (e.g. "Digitale Souveränität").
                for m in self._MULTIWORD_RE.finditer(sentence):
                    if m.start() >= first_word_end or counts_initial:
                        count(m.group(0), sentence)
                # Single capitalised words of reasonable length.
                for m in self._SINGLEWORD_RE.finditer(sentence):
                    if m.start() >= first_word_end:
                        count(m.group(0), sentence)

        # Rank by occurrences, multi-word terms weighted higher.
        def score(term: str) -> int:
            return occurrences[term] * (2 if ' ' in term else 1)

        candidates: List[str] = []
        for term in sorted(occurrences, key=lambda t: (-score(t), t)):
            if occurrences[term] < 2:
                continue  # a term must actually recur
            low = term.lower()
            if low in self._TERM_STOPWORDS or low in existing_lower:
                continue
            if any(term == c or term in c for c in candidates):
                continue
            candidates.append(term)
            if len(candidates) >= max_terms:
                break

        # Example sentences give the term translation some context.
        self._term_examples = {t: examples.get(t, '') for t in candidates}
        return candidates

    async def verify_translation(
        self,
        translated_chunks: List[Dict[str, Any]],
        source_lang: str,
        target_lang: str,
        glossary: Optional[Dict[str, str]] = None,
        semantic: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Second pass: check each translated section against its original.

        semantic=False runs only the local checks (no model calls).

        Deliberately does NOT re-translate. It reports findings so a human can
        judge them: omissions/additions, changed numbers or units, terminology
        that deviates from the glossary, and structural drift (list items,
        table rows). Structural checks are done locally - only the semantic
        check needs the model.
        """
        findings: List[Dict[str, Any]] = []

        # Include the auto-derived terminology from the last translation run,
        # manual entries taking precedence.
        glossary = {**getattr(self, '_last_effective_glossary', {}), **(glossary or {})}

        for idx, chunk in enumerate(translated_chunks):
            original = chunk.get('original_content') or ""
            translated = chunk.get('translated_content') or ""
            if not original.strip() or not translated.strip():
                continue

            # (code, parameters) pairs; rendered by the interface.
            issues: List[Tuple[str, Dict[str, Any]]] = []

            # --- local structural checks (no model call needed) -------------
            issues.extend(self._structural_findings(original, translated))

            # Glossary adherence: if a source term occurs, its rendering should
            # appear in the translation. Respects glossary.case_sensitive_default.
            case_sensitive = getattr(
                getattr(self.config, 'glossary', None), 'case_sensitive_default', False)
            for src_term, tgt_term in (glossary or {}).items():
                hay_src = original if case_sensitive else original.lower()
                hay_tgt = translated if case_sensitive else translated.lower()
                needle_src = src_term if case_sensitive else src_term.lower()
                needle_tgt = tgt_term if case_sensitive else tgt_term.lower()
                if needle_src in hay_src and needle_tgt not in hay_tgt:
                    issues.append(('terminology', {'source': src_term, 'target': tgt_term}))

            # --- semantic check via the model -------------------------------
            if chunk.get('is_code') or not semantic:
                pass  # code is passed through; local-only mode skips the model
            else:
                try:
                    verdict = await self._semantic_check(
                        original, translated, source_lang, target_lang)
                    if verdict:
                        issues.append(('content', {'detail': verdict}))
                except Exception as e:
                    logger.warning(f"Quality check failed for section {idx}: {e}")

            if issues:
                findings.append({
                    'chunk_index': idx,
                    'position': chunk.get('position'),
                    # English text for logs and API users; the interface
                    # renders 'issue_details' in its own language.
                    'issues': [render_finding_en(code, params) for code, params in issues],
                    'issue_details': issues,
                    'excerpt': original.strip()[:120],
                })

        logger.info(f"Quality check: {len(findings)} of {len(translated_chunks)} sections flagged")
        return findings

    @staticmethod
    def _structural_findings(original: str, translated: str) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Compare structure of source and translation without a model call.

        Returns (code, parameters) pairs; the interface renders them in its
        own language (finding.<code> in src/i18n.py).
        """
        issues: List[Tuple[str, Dict[str, Any]]] = []

        def count_list_items(text):
            return len([l for l in text.split('\n')
                        if re.match(r'^\s*([-*+]|\d+[.)])\s+', l)])

        def count_table_rows(text):
            return len([l for l in text.split('\n') if l.strip().startswith('|')])

        def numbers(text):
            # Normalise decimal separators so 1,5 and 1.5 compare equal.
            return sorted(n.replace(',', '.') for n in re.findall(r'\d+(?:[.,]\d+)?', text))

        if count_list_items(original) != count_list_items(translated):
            issues.append(('list_items', {'source': count_list_items(original),
                                          'target': count_list_items(translated)}))

        if count_table_rows(original) != count_table_rows(translated):
            issues.append(('table_rows', {'source': count_table_rows(original),
                                          'target': count_table_rows(translated)}))

        src_numbers, tgt_numbers = numbers(original), numbers(translated)
        if src_numbers != tgt_numbers:
            missing = [n for n in src_numbers if n not in tgt_numbers]
            added = [n for n in tgt_numbers if n not in src_numbers]
            if missing or added:
                issues.append(('numbers', {'missing': ', '.join(missing[:5]) or '-',
                                           'added': ', '.join(added[:5]) or '-'}))

        # A translation far shorter than the source suggests dropped content.
        if len(original) > 100 and len(translated) < len(original) * 0.55:
            issues.append(('length', {'source': len(original), 'target': len(translated)}))

        return issues

    async def _semantic_check(
        self, original: str, translated: str, source_lang: str, target_lang: str
    ) -> Optional[str]:
        """
        Ask the model to report omissions/additions only, not to retranslate.

        Deliberately calls the client's complete() directly instead of
        translate_text(): the latter wraps its input in a translation
        instruction ("Bitte übersetze folgenden Text: ..."), so the review
        prompt itself would be translated instead of executed.
        """
        from ..llm_client import Message, MessageRole

        system = (
            "You are a strict but fair translation reviewer. You never "
            "translate; you only compare a source text with its translation "
            "and report concrete factual defects:\n"
            "- content present in the source but missing from the translation\n"
            "- content in the translation that is not in the source\n"
            "- a statement whose meaning was changed, negated or reversed\n"
            "- wrong numbers, dates, units or proper names\n\n"
            "Do NOT report: different wording, synonyms, word order, register, "
            "punctuation, formatting, or anything you would merely phrase "
            "differently yourself. A faithful translation that reads "
            "differently from a literal rendering is CORRECT.\n\n"
            "If there is no concrete defect, answer with exactly: OK\n"
            "Otherwise answer in one short sentence naming the defect."
        )
        user = (
            f"SOURCE ({source_lang}):\n{original}\n\n"
            f"TRANSLATION ({target_lang}):\n{translated}"
        )

        response = await self.llm_client.complete([
            Message(MessageRole.SYSTEM, system),
            Message(MessageRole.USER, user),
        ])
        verdict = self._strip_reasoning((getattr(response, 'content', '') or "").strip())

        if not verdict:
            return None
        # Tolerate answers such as "OK.", "OK -", "ok" and short confirmations.
        normalised = verdict.upper().lstrip('*_- ').rstrip('.!*_ ')
        if normalised.startswith("OK") or normalised in ("KEINE", "NONE", "NO ISSUES"):
            return None
        # A "review" that merely echoes the translation is not a finding.
        if verdict.strip() == translated.strip():
            return None
        return verdict[:400]

    async def _update_progress(self, task: TranslationTask, message: str):
        """
        Report progress to the callbacks, avoiding conflicting updates.
        """
        # Skip if status updates are suppressed by external callback
        if self._suppress_status_updates:
            return

        progress_data = {
            'chunk_index': task.chunk_index,
            'total_chunks': task.total_chunks,
            'status': task.status.value,
            'message': message,
            'progress': (task.chunk_index / task.total_chunks) * 100,
            'attempts': task.attempts,
            'task_id': task.task_id,
            'style_used': task.context.style,
            'chunk_type': 'table' if task.chunk.is_table else 'code' if task.chunk.is_code else 'text',
            'is_table': task.chunk.is_table,
            'is_code': task.chunk.is_code
        }

        with self._callback_lock:
            callbacks = list(self.progress_callbacks)

        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    result = await callback(progress_data)
                else:
                    result = callback(progress_data)

                # If callback returns False, suppress further status updates
                if result is False:
                    self._suppress_status_updates = True
                    logger.debug("Progress callback requested status suppression")
                    return

            except Exception as e:
                logger.warning(f"Progress callback error: {e}")

    def _reset_stats(self):
        """Reset statistics."""
        self.stats = {
            'total_chunks': 0,
            'completed_chunks': 0,
            'failed_chunks': 0,
            'cancelled_chunks': 0,
            'total_retries': 0,
            'total_tokens': 0,
            'total_time': 0,
            'tables_translated': 0,
            'table_rows_translated': 0
        }

    async def shutdown(self):
        """Graceful shutdown with task cancellation."""
        if self._shutdown:
            return

        logger.info("Starting TableAwareTranslationEngine shutdown...")
        self._shutdown = True
        self._shutdown_event.set()

        await self._cancel_active_tasks()

        with self._callback_lock:
            self.progress_callbacks.clear()

        logger.info("TableAwareTranslationEngine shutdown complete")

    async def _cancel_active_tasks(self):
        """Cancel all active tasks gracefully."""
        with self._task_lock:
            if not self._active_tasks:
                return
            tasks_to_cancel = list(self._active_tasks)

        logger.info(f"Cancelling {len(tasks_to_cancel)} active tasks...")

        for task in tasks_to_cancel:
            if not task.done():
                task.cancel()

        if tasks_to_cancel:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("Task cancellation timeout")

        with self._task_lock:
            self._active_tasks.clear()

    def cleanup(self):
        """Synchronous cleanup for atexit."""
        if self._shutdown:
            return

        try:
            if self._check_event_loop():
                loop = asyncio.get_running_loop()
                if not loop.is_closed():
                    loop.create_task(self.shutdown())
            else:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.shutdown())
                    loop.close()
                except Exception as e:
                    logger.error(f"Failed to cleanup in sync mode: {e}")
        except Exception as e:
            logger.error(f"TableAwareTranslationEngine cleanup error: {e}")

    async def close(self):
        """Close the translation engine and cleanup resources."""
        await self.shutdown()


# Backwards compatibility - use the enhanced engine as default
TranslationEngine = TableAwareTranslationEngine

# Utility function with table awareness
async def translate_chunks(
    chunks: List[SimpleChunk],
    source_lang: str,
    target_lang: str,
    llm_client: LLMClient,
    style: str = "professional",
    glossary: Optional[Dict[str, str]] = None,
    max_workers: int = 5
) -> TranslationResult:
    """
    Convenience function to translate chunks with table awareness.
    """
    from ..chunking import SimpleChunkingResult

    logger.info(f"=== TABLE-AWARE TRANSLATE_CHUNKS CALLED ===")
    logger.info(f"Style: '{style}' for {len(chunks)} chunks")

    # Count chunk types
    table_chunks = len([c for c in chunks if c.is_table])
    code_chunks = len([c for c in chunks if c.is_code])
    text_chunks = len(chunks) - table_chunks - code_chunks

    logger.info(f"Chunk types: {text_chunks} text, {table_chunks} tables, {code_chunks} code")

    chunking_result = SimpleChunkingResult(
        chunks=chunks,
        total_tokens=sum(c.token_count for c in chunks),
        chunk_count=len(chunks)
    )

    engine = TableAwareTranslationEngine(llm_client)
    engine.max_workers = max_workers
    
    try:
        result = await engine.translate_document(
            chunking_result,
            source_lang,
            target_lang,
            style,
            glossary
        )
        return result
    finally:
        await engine.close()
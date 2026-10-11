# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Document Translation System - Unified Pipeline Implementation
UNIFIED: All document types (PDF, Word, Markdown, Text) use the same processing pipeline
PDF → pymupdf4llm → Markdown → Standard Chunker → Standard Translation → Output
"""

import asyncio
import atexit
import logging
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import shutil
from types import SimpleNamespace
from typing import Optional, Tuple, Dict, Any, List, Callable
from datetime import datetime
import yaml
import time
import os

import gradio as gr

from .markdown_utils import is_table_separator
from .i18n import t, resolve_language, localized, SUPPORTED_LANGUAGES
from .organization_glossary import OrganizationGlossary

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def resolve_input_type(hint: Optional[str], text: Optional[str], file: Any) -> str:
    """
    Determine whether to translate the uploaded file or the entered text.

    The active tab is only a hint. What actually decides is which input is
    filled, so translation works even if the tab signal is missing or stale
    (Gradio does not guarantee an index for Tab select events). Only when both
    inputs are filled does the tab decide, because then the hint expresses what
    the user is currently looking at.
    """
    has_text = bool(text and str(text).strip())
    has_file = file is not None
    hint = hint if hint in ("File", "Text") else None

    if has_file and has_text:
        return hint or "File"
    if has_file:
        return "File"
    if has_text:
        return "Text"
    return hint or "File"


def reassemble_translated_chunks(chunks: List[Dict[str, Any]]) -> str:
    """
    Reassemble translated chunks into a single markdown document.

    Chunks are normally separated by a blank line. Parts of a table that the
    chunker had to split by rows are stitched back into ONE table: every part
    after the first repeats the header and separator row (needed so the LLM
    keeps the column context), and those repeated rows must be dropped again
    on reassembly - otherwise a long table would come out as several small
    tables with headers in the middle of the document.
    """
    pieces: List[str] = []
    prev_was_table_part = False
    prev_was_para_part = False

    for chunk in chunks:
        content = chunk.get('translated_content')
        if not content:
            continue

        meta = chunk.get('metadata') or {}
        is_table_part = bool(meta.get('is_split_table')) and meta.get('split_index', 0) > 0
        is_para_part = bool(meta.get('is_split_paragraph')) and meta.get('split_index', 0) > 0

        if is_table_part and prev_was_table_part and pieces:
            lines = content.split('\n')
            # Drop a leading header row plus its separator row, if present.
            if len(lines) >= 2 and _is_table_separator_line(lines[1]):
                lines = lines[2:]
            body = '\n'.join(lines).strip('\n')
            if body:
                pieces[-1] = pieces[-1].rstrip('\n') + '\n' + body
        elif is_para_part and prev_was_para_part and pieces:
            # Parts of one paragraph are rejoined with a space, not a blank
            # line, so a long paragraph does not turn into several paragraphs.
            pieces[-1] = pieces[-1].rstrip() + ' ' + content.lstrip()
        else:
            pieces.append(content)

        prev_was_table_part = bool(meta.get('is_split_table'))
        prev_was_para_part = bool(meta.get('is_split_paragraph'))

    return '\n\n'.join(pieces)


def _is_table_separator_line(line: str) -> bool:
    """True for markdown table separator rows such as |---|:--:|."""
    # Shared definition, see src/markdown_utils.py.
    return is_table_separator(line)


# Translation styles are identified by their key (translation.available_styles,
# translation.default_style, prompt instructions). The label shown in the
# interface comes from the catalog (style.<key>) and is display text only:
# the dropdown passes the key as its value, so behaviour never depends on
# the label.
def style_label(key: str, lang: Optional[str] = None) -> str:
    """Display label for a style key (unknown keys are title-cased)."""
    label = t(lang, f"style.{key}")
    return label if label != f"style.{key}" else key.replace('_', ' ').title()


def style_key_for_label(label: Optional[str]) -> str:
    """
    Internal style key for a dropdown value.

    The dropdown passes keys; display labels (of any interface language) are
    still accepted so that existing API-style callers keep working.
    """
    if not label:
        return ""
    for lang in SUPPORTED_LANGUAGES:
        for key_name, text in _style_labels(lang).items():
            if text == label:
                return key_name
    return label.lower().replace(" ", "_")


def _style_labels(lang: str) -> Dict[str, str]:
    from .i18n import CATALOG
    return {k[len("style."):]: v for k, v in CATALOG[lang].items() if k.startswith("style.")}


def formality_for_style(style_key: str) -> str:
    """'informal' (du) for styles whose key ends in '_du', otherwise 'formal'."""
    return "informal" if (style_key or "").endswith("_du") else "formal"


# Document conversion (PDF via PyMuPDF, Word via python-docx) is synchronous
# and can take tens of seconds for large files. It runs outside the event
# loop, otherwise every other session (page loads, progress, translations)
# stands still meanwhile. PyMuPDF must not be used from several threads at
# once, so PDF conversions share one dedicated thread.
_PDF_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-conversion")


async def convert_document_off_loop(path: Path, options: "Optional[ProcessingOptions]" = None):
    """Run process_document() in a worker thread (PDF: the single PDF thread)."""
    loop = asyncio.get_running_loop()
    executor = _PDF_EXECUTOR if path.suffix.lower() == '.pdf' else None
    return await loop.run_in_executor(
        executor, lambda: asyncio.run(process_document(path, options)))


# Name of the file in which an instance records its process id inside its own
# temp directory, so other instances can tell live directories from leftovers.
OWNER_FILE = "owner.pid"
# Directories without owner file are only removed when older than this.
STALE_TEMP_AGE_SECONDS = 3600


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def cleanup_stale_temp_dirs(temp_base: Path) -> int:
    """
    Remove working directories left behind by instances that are gone.

    A directory belongs to a live instance if its owner file names a running
    process. Directories without owner file are removed once they are older
    than STALE_TEMP_AGE_SECONDS. Returns the number of removed directories.
    """
    removed = 0
    if not temp_base.is_dir():
        return removed
    now = time.time()
    for entry in temp_base.iterdir():
        if not entry.is_dir() or not entry.name.startswith('tmp'):
            continue
        owner = entry / OWNER_FILE
        try:
            if owner.exists():
                pid = int(owner.read_text(encoding='utf-8').strip() or 0)
                if pid and _process_alive(pid):
                    continue
            elif now - entry.stat().st_mtime < STALE_TEMP_AGE_SECONDS:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        except (OSError, ValueError) as e:
            logger.warning(f"Could not check temp directory {entry.name}: {e}")
    if removed:
        logger.info(f"Removed {removed} stale temp director{'y' if removed == 1 else 'ies'}")
    return removed


# Import configuration
from .config import load_config, get_config, set_config, resolve_path, PROJECT_ROOT

# Import LLM client
from .llm_client import LLMClient, get_client_manager

# Import document processing
from .document_processing import (
    process_document,
    DocumentProcessorFactory,
    MarkdownConverter,
    MarkdownValidator
)

# Import models - UNIFIED: No document type enums
from .models.document import (
    ProcessingOptions,
    ProcessingResult,
    MarkdownDocument,
    DocumentElement,
    ElementType,
    DocumentMetadata
)

# Import chunking
from .chunking.simple_chunker import (
    SimpleChunker, SimpleChunkingResult,
    simple_chunk_text
)

# Import translation engines
from .translation.translation_engine import TableAwareTranslationEngine
from .translation.docx_inplace import DocxInPlaceTranslator

# Import export handlers
from .ui.export_handlers import EnhancedExportHandler, handle_export


class UnifiedRateLimiter:
    """Unified rate limiter for all document types using config.yaml settings"""

    def __init__(self, config):
        self.rate = config.parallel_processing.rate_limit
        self.tokens = self.rate
        self.max_tokens = self.rate
        self.last_update = time.time()
        self.lock = asyncio.Lock()
        logger.info(f"rate limiter initialized: {self.rate} requests/second")

    async def acquire(self, tokens: int = 1):
        """Acquire tokens, waiting if necessary"""
        async with self.lock:
            while tokens > self.tokens:
                now = time.time()
                elapsed = now - self.last_update
                new_tokens = elapsed * self.rate

                self.tokens = min(self.max_tokens, self.tokens + new_tokens)
                self.last_update = now

                if tokens > self.tokens:
                    wait_time = (tokens - self.tokens) / self.rate
                    logger.debug(f"rate limit hit, waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)

            self.tokens -= tokens


class UnifiedPerformanceMonitor:
    """Unified performance monitoring for all document types"""

    def __init__(self, config):
        self.config = config
        self.performance_logging = config.logging.performance_logging
        self.enabled = self.performance_logging.enabled
        self.slow_threshold = self.performance_logging.slow_translation_threshold
        self.log_slow = self.performance_logging.log_slow_translations
        self.log_memory = self.performance_logging.log_memory_usage
        self.translation_times = []

        if self.enabled:
            logger.info(f"performance monitoring enabled: slow threshold={self.slow_threshold}s")

    def start_translation(self):
        """Start timing a translation"""
        return {'start_time': time.time()} if self.enabled else None

    def end_translation(self, timing_data, chunk_count=0):
        """End timing and log if needed"""
        if not self.enabled or not timing_data:
            return

        duration = time.time() - timing_data['start_time']
        self.translation_times.append(duration)

        if self.log_slow and duration > self.slow_threshold:
            logger.warning(f"Slow unified translation: {duration:.2f}s for {chunk_count} chunks")

        if self.log_memory:
            try:
                import psutil
                memory_usage = psutil.Process().memory_info().rss / 1024 / 1024
                logger.info(f"Memory usage after unified translation: {memory_usage:.1f} MB")
            except ImportError:
                pass

    def get_average_time(self):
        """Get average translation time"""
        if not self.translation_times:
            return 0.0
        return sum(self.translation_times) / len(self.translation_times)

    def get_performance_summary(self) -> str:
        """Get unified performance summary"""
        if not self.enabled:
            return "monitoring disabled"

        total_translations = len(self.translation_times)
        avg_time = self.get_average_time()

        return (f"Performance: {total_translations} translations, "
               f"Avg: {avg_time:.1f}s")


class UnifiedLanguageManager:
    """Unified language manager for all document types"""

    def __init__(self, config_path: Path = None, config=None):
        self.config_path = config_path or (PROJECT_ROOT / "config" / "languages.yaml")
        self.config = config
        self.languages = {}
        self._load_configuration()

    def _load_configuration(self):
        """Load unified language configuration"""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                self.languages = config.get('languages', {})
                logger.info(f"Loaded {len(self.languages)} languages")
            else:
                self._load_fallback_config()
        except Exception as e:
            logger.error(f"Failed to load language config: {e}")
            self._load_fallback_config()

    def _load_fallback_config(self):
        """Load fallback language configuration"""
        chars_per_token_config = {}
        if self.config and hasattr(self.config, 'chunking'):
            chars_per_token_config = self.config.chunking.chars_per_token

        # Used only when config/languages.yaml is missing or unreadable.
        native_names = {
            'de': 'Deutsch', 'en': 'English', 'fr': 'Français', 'es': 'Español',
            'it': 'Italiano', 'pt': 'Português', 'nl': 'Nederlands', 'pl': 'Polski',
            'ru': 'Русский', 'zh': '中文', 'ja': '日本語', 'ko': '한국어',
        }
        defaults = {'de': 3.5, 'en': 4.0, 'fr': 4.2, 'es': 4.1, 'it': 4.0, 'pt': 4.0,
                    'nl': 3.8, 'pl': 3.6, 'ru': 3.0, 'zh': 1.5, 'ja': 2.0, 'ko': 2.5}
        self.languages = {'auto': {'code': 'auto', 'available_as_source': True,
                                   'available_as_target': False}}
        for code, native in native_names.items():
            self.languages[code] = {
                'code': code,
                'name': native,
                'native_name': native,
                'chars_per_token': chars_per_token_config.get(code, defaults[code]),
            }

    def get_source_languages(self) -> List[str]:
        """Get available source language names"""
        return [lang.get('name', code) for code, lang in self.languages.items()]

    def get_target_languages(self) -> List[str]:
        """Get available target language names (excluding auto-detect)"""
        return [lang.get('name', code) for code, lang in self.languages.items() if code != 'auto']

    def get_language_name(self, code: str) -> str:
        """Get display name for language code"""
        return self.languages.get(code, {}).get('name', code)

    def display_name(self, code: str, lang: Optional[str] = None) -> str:
        """
        Name shown in the interface: the language's own name (native_name,
        e.g. "Deutsch", "Français"), independent of the interface language.
        Automatic detection is labelled in the interface language.
        """
        if code == 'auto':
            return t(lang, "language.auto")
        entry = self.languages.get(code, {})
        return entry.get('native_name') or entry.get('name') or code

    def source_choices(self, lang: Optional[str] = None) -> List[Tuple[str, str]]:
        """(label, code) pairs for the source dropdown; the value is the code."""
        return [(self.display_name(code, lang), code) for code, entry in self.languages.items()
                if entry.get('available_as_source', True)]

    def target_choices(self, lang: Optional[str] = None) -> List[Tuple[str, str]]:
        """(label, code) pairs for the target dropdown (no automatic detection)."""
        return [(self.display_name(code, lang), code) for code, entry in self.languages.items()
                if code != 'auto' and entry.get('available_as_target', True)]

    def to_code(self, value: str) -> str:
        """Language code for a dropdown value (a code, or a display name)."""
        if value in self.languages:
            return value
        for code, entry in self.languages.items():
            if value in (entry.get('name'), entry.get('native_name')):
                return code
        return 'auto'

    def get_code_from_name(self, name: str) -> str:
        """Get language code from display name"""
        for code, lang in self.languages.items():
            if lang.get('name') == name:
                return code
        return 'auto'


class UnifiedDocumentTranslationApp:
    """
    Unified Document Translation Application
    ALL document types → Markdown → Standard Chunking → Standard Translation → Output
    """

    def __init__(self):
        """Initialize unified application"""
        logger.info("Initializing Document Translation System")

        # Load configuration
        try:
            self.config = load_config()
        except Exception as e:
            logger.error(f"Config loading failed: {e}. Using built-in defaults.")
            self.config = self._create_default_config()
        # Components that call get_config() (e.g. the document processors)
        # must see the same configuration instead of loading a second copy.
        set_config(self.config)

        # Glossary of the organisation (optional, re-read when the file changes).
        organization_file = getattr(self.config.glossary, 'organization_file', None)
        self.organization_glossary = OrganizationGlossary(
            resolve_path(organization_file) if organization_file else None,
            self.config.glossary.csv_delimiter)
        if self.organization_glossary.configured:
            self.organization_glossary.entries_for("de", "en")  # load and report problems early

        # Initialize core components
        self.language_manager = UnifiedLanguageManager(config=self.config)
        self.llm_client = None

        # Initialize unified chunker
        self._initialize_unified_chunker()

        # Initialize unified monitoring
        self.rate_limiter = UnifiedRateLimiter(self.config)
        self.performance_monitor = UnifiedPerformanceMonitor(self.config)

        # Initialize unified markdown converter
        from .document_processing.markdown_converter import create_table_aware_converter
        # Without frontmatter: document metadata (author, title, dates) is not
        # content - it must not be sent to the model or end up in exports.
        self.markdown_converter = create_table_aware_converter(
            preserve_formatting=True,
            repair_tables=True,
            include_frontmatter=False
        )

        # Initialize unified export handler
        export_config = getattr(self.config, 'export', None)
        self.export_handler = EnhancedExportHandler(
            include_word_header=bool(getattr(getattr(export_config, 'word', None),
                                             'include_header', False))
        )

        # Unified storage management
        temp_base = os.environ.get('TEMP_DIR')
        if temp_base is None:
            temp_base = getattr(self.config.document_processing, 'temp_dir', './temp')
        temp_base = resolve_path(temp_base)
        temp_base.mkdir(parents=True, exist_ok=True)
        # Leftovers of instances that were killed (they can contain uploaded
        # documents) are removed before this instance creates its own directory.
        cleanup_stale_temp_dirs(temp_base)
        self.temp_dir = Path(tempfile.mkdtemp(dir=temp_base))
        (self.temp_dir / OWNER_FILE).write_text(str(os.getpid()), encoding='utf-8')

        # Note: this instance is shared by ALL sessions. Only keep
        # session-independent infrastructure here (config, rate limiter, ...).
        # User data (document, translation, metadata) belongs in gr.State.

        atexit.register(self.cleanup)
        logger.info("Document Translation System ready")

    def _shared_request_semaphore(self) -> Optional[asyncio.Semaphore]:
        """
        Application-wide limit on simultaneous model requests
        (parallel_processing.max_concurrent_requests), or None.

        Created on first use, inside the running event loop that serves all
        sessions, and then shared by the clients of all jobs.
        """
        limit = getattr(self.config.parallel_processing, 'max_concurrent_requests', None)
        if not limit:
            return None
        if getattr(self, '_request_semaphore', None) is None:
            self._request_semaphore = asyncio.Semaphore(limit)
        return self._request_semaphore

    def _initialize_unified_chunker(self):
        """Initialize unified chunker for all document types"""
        chunking_config = getattr(self.config, 'simple_chunking', None)
        if chunking_config:
            max_tokens = chunking_config.max_tokens
            chars_per_token = chunking_config.chars_per_token
        else:
            max_tokens = self.config.chunking.max_tokens
            chars_per_token = self.config.chunking.chars_per_token.get('default', 4.0)

        self.unified_chunker = SimpleChunker(
            max_tokens=max_tokens,
            chars_per_token=chars_per_token,
            config=self.config
        )
        logger.info(f"chunker initialized: {max_tokens} max tokens")

    def _create_default_config(self):
        """Built-in defaults, used when the configuration files cannot be loaded."""
        from .config import Config
        config = Config()
        config.setup_logging()
        return config

    def _analyze_unified_content(self, content: str) -> Dict[str, Any]:
        """Analyze content complexity for unified processing"""
        has_tables = False
        has_code = False
        table_count = 0
        code_block_count = 0

        # Detect tables
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if '|' in line and i + 1 < len(lines) and ('---' in lines[i + 1] or '|-' in lines[i + 1]):
                has_tables = True
                table_count += 1

        # Detect code blocks
        if '```' in content:
            has_code = True
            code_block_count = content.count('```') // 2

        # Determine complexity for unified processing
        max_size = getattr(self.config.document_processing, 'max_file_size_mb', 30) * 1024 * 1024
        is_complex = has_tables or has_code or len(content) > (max_size // 10)

        return {
            'has_tables': has_tables,
            'has_code': has_code,
            'table_count': table_count,
            'code_block_count': code_block_count,
            'is_complex': is_complex,
            'length': len(content),
            'unified_processing': True
        }

    async def process_and_translate_unified(self,
                                           input_type: str,
                                           text_input: str,
                                           file_input,
                                           source_lang: str,
                                           target_lang: str,
                                           translation_mode: str,
                                           style: str,
                                           glossary_text: str,
                                           formality: str = "formal",
                                           progress=gr.Progress(),
                                           lang: Optional[str] = None,
                                           partial: Optional[Callable[[str], None]] = None
                                           ) -> Tuple[str, str, str, Dict[str, Any]]:
        """
        UNIFIED: Process and translate any document type through the same pipeline
        Input → Document Processor → Markdown → Standard Chunker → Standard Translation → Output

        Word documents are translated inside the original file when
        document_processing.docx_mode is "inplace"; the translated file is
        then returned in metadata["docx_path"] and the text shown is read back
        from it. partial(text), if given, receives the text translated so far.

        Returns:
            (original_content, translated_text, status, metadata). The result
            is deliberately NOT stored on self (shared instance); the caller
            writes it into the session's gr.State.
        """

        # Start unified performance monitoring
        timing_data = self.performance_monitor.start_translation()

        try:
            progress(0.1, desc=t(lang, "progress.analyzing_input"))

            # UNIFIED: Load content with standard processing for all types
            if input_type == "Text":
                if not text_input or not text_input.strip():
                    return "", "", t(lang, "status.enter_text"), {}
                content = text_input
                file_extension = ".txt"
                file_path = None
            else:  # File
                if not file_input:
                    return "", "", t(lang, "status.choose_file"), {}

                file_path = Path(file_input.name)

                # Check file size against unified limits
                file_size = file_path.stat().st_size
                max_size = self.config.document_processing.max_file_size_mb * 1024 * 1024
                if file_size > max_size:
                    return "", "", t(lang, "status.file_too_large", size_mb=file_size // 1024 // 1024,
                                      max_mb=max_size // 1024 // 1024), {}

                # One directory per request: uploads with the same file name from
                # parallel sessions must not overwrite each other.
                request_dir = Path(tempfile.mkdtemp(dir=self.temp_dir))
                try:
                    temp_path = request_dir / file_path.name
                    shutil.copy(file_path, temp_path)

                    progress(0.2, desc=t(lang, "progress.processing_document"))

                    # UNIFIED: Standard processing options for ALL document types
                    processing_options = ProcessingOptions(
                        extract_tables=True,
                        extract_footnotes=True,
                        preserve_formatting=True,
                        detect_headers=True,
                        detect_lists=True
                    )

                    # UNIFIED: Process document (PDF automatically via pymupdf4llm → Markdown)
                    processing_result = await convert_document_off_loop(temp_path, processing_options)

                    if not processing_result.success:
                        # Server paths are not shown to the user.
                        error = (processing_result.error_message or "").replace(
                            str(temp_path), file_path.name).replace(str(request_dir), "")
                        if error.startswith("Unsupported file format"):
                            error = t(lang, "status.unreadable_file", name=file_path.name)
                        return "", "", t(lang, "status.pipeline_error", error=error), {}

                    if file_path.suffix.lower() in ('.md', '.markdown', '.txt'):
                        # Markdown and text files are already in the pipeline's
                        # format; they are used verbatim, exactly like pasted
                        # text. Rebuilding them from parsed elements joins
                        # table rows, numbered list items and code lines.
                        content = processing_result.document.raw_text or processing_result.document.content
                    else:
                        content = self.markdown_converter.convert_document(processing_result.document)
                finally:
                    # Do not keep the user's document on disk longer than necessary
                    shutil.rmtree(request_dir, ignore_errors=True)
                file_extension = file_path.suffix.lower()

            # UNIFIED: Analyze content complexity
            progress(0.25, desc=t(lang, "progress.analyzing_content"))
            complexity = self._analyze_unified_content(content)

            # The dropdowns pass language codes; display names are still
            # accepted for callers that pass names.
            source_code = self.language_manager.to_code(source_lang)
            target_code = self.language_manager.to_code(target_lang)
            if target_code == 'auto' or target_code not in self.language_manager.languages:
                return content, "", t(lang, "status.invalid_target"), {}

            # Determine style for unified processing
            if translation_mode == "direct":
                actual_style = "preserve_original"
                style_display = t(lang, "style.original")
            else:
                actual_style = style_key_for_label(style)
                style_display = style_label(actual_style, lang)

            # Parse glossary for unified processing
            glossary = {}
            if glossary_text and glossary_text.strip():
                delimiter = self.config.glossary.csv_delimiter
                max_entries = self.config.glossary.max_entries
                lines = glossary_text.strip().split('\n')

                for line in lines[:max_entries]:
                    if delimiter in line:
                        parts = line.split(delimiter, 1)
                        if len(parts) == 2:
                            glossary[parts[0].strip()] = parts[1].strip()

            # Glossary of the organisation: only the entries whose term occurs in
            # this document. The user's own entries take precedence.
            organization_terms = {
                term: translation
                for term, translation in self.organization_glossary.relevant_entries(
                    source_code, target_code, content,
                    getattr(self.config.glossary, 'case_sensitive_default', False)).items()
                if term not in glossary}
            glossary = {**organization_terms, **glossary}

            # UNIFIED: Apply rate limiting
            await self.rate_limiter.acquire()

            translation_start_time = time.time()

            # UNIFIED: Initialize client and engine (same for all document types)
            progress(0.4, desc=t(lang, "progress.starting"))

            client = LLMClient(self.config, shared_semaphore=self._shared_request_semaphore())
            await client.start()
            # The client is closed in every case, also when the translation
            # fails or is cancelled.
            try:
                # UNIFIED: Use TableAwareTranslationEngine for all document types
                engine = TableAwareTranslationEngine(client, self.config)
                engine.max_workers = self.config.parallel_processing.max_workers
                engine.max_retries = self.config.llm.max_retries
                engine.retry_delay = self.config.llm.retry_delay

                # UNIFIED: Standard chunking for ALL documents (including PDF)
                chunking_result = self.unified_chunker.chunk_text(content, source_code)

                inplace = (file_path is not None and file_extension == '.docx'
                           and getattr(self.config.document_processing, 'docx_mode', 'inplace') == 'inplace')
                docx_path = None
                formatting_fallbacks = 0
                progress_callback = self._create_unified_progress_callback(progress, translation_start_time, lang)

                if inplace:
                    effective_glossary = await engine.prepare_glossary(
                        chunking_result.chunks, source_code, target_code,
                        actual_style, formality, glossary)
                    translator = DocxInPlaceTranslator(engine, self.config)
                    out_dir = self.export_handler._new_export_dir()
                    docx_path = out_dir / f"{file_path.stem}_{target_code}.docx"

                    def report(done, total):
                        progress_callback({'chunk_index': done - 1, 'total_chunks': total})

                    inplace_result = await translator.translate_file(
                        file_path, docx_path, source_code, target_code, actual_style,
                        formality, effective_glossary, progress=report, partial=partial)

                    # The text shown (and used for Markdown/HTML export) is read
                    # back from the translated file.
                    translated_result = await convert_document_off_loop(docx_path, ProcessingOptions())
                    translated_text = (self.markdown_converter.convert_document(translated_result.document)
                                       if translated_result.success else
                                       "\n\n".join(sec['translated_content'] for sec in inplace_result.sections))
                    checked_sections = inplace_result.sections
                    engine_result = SimpleNamespace(
                        success=inplace_result.failed_paragraphs < inplace_result.paragraphs,
                        translated_chunks=inplace_result.sections,
                        failed_chunks=[None] * inplace_result.failed_paragraphs,
                        total_tokens=0, total_time=time.time() - translation_start_time)
                    formatting_fallbacks = inplace_result.formatting_fallbacks
                    glossary_for_check = effective_glossary
                else:
                    if partial is not None:
                        finished: Dict[int, str] = {}

                        def on_chunk_done(index, text):
                            finished[index] = text
                            partial(reassemble_translated_chunks(
                                [{'chunk_index': i, 'position': i, 'translated_content': finished[i],
                                  'is_table': chunking_result.chunks[i].is_table}
                                 for i in sorted(finished)]))
                        engine.on_chunk_done = on_chunk_done

                    # UNIFIED: Execute translation
                    engine_result = await engine.translate_document(
                        chunking_result,
                        source_code,
                        target_code,
                        actual_style,
                        glossary,
                        progress_callback,
                        formality
                    )

                    translated_text = reassemble_translated_chunks(engine_result.translated_chunks)
                    checked_sections = engine_result.translated_chunks
                    glossary_for_check = glossary

                # Second pass: compare the translation with the source. The
                # local structural checks cost no model call and run by
                # default; the model-based review is optional.
                quality_findings = []
                run_structural = getattr(self.config.translation, 'structural_check', True)
                run_semantic = getattr(self.config.translation, 'quality_check', False)
                if run_structural or run_semantic:
                    try:
                        quality_findings = await engine.verify_translation(
                            checked_sections,
                            source_code, target_code, glossary_for_check,
                            semantic=run_semantic
                        )
                    except Exception as e:
                        logger.warning(f"Quality check failed: {e}")
                failed_cells = engine.stats.get('failed_cells', 0)
            finally:
                await client.close()

            failed_count = len(getattr(engine_result, 'failed_chunks', []) or [])

            # UNIFIED: Standard result processing
            result = {
                'success': engine_result.success,
                'translated_text': translated_text,
                'chunk_count': (inplace_result.paragraphs if docx_path is not None
                                else len(engine_result.translated_chunks)),
                'failed_count': failed_count,
                'total_tokens': engine_result.total_tokens,
                'total_time': engine_result.total_time
            }

            # Only treat it as a hard failure when nothing usable was produced.
            # Partial failures keep the document (with the original text for the
            # affected sections) and surface a warning instead of discarding it.
            if not translated_text.strip():
                return content, "", t(lang, "status.translation_failed"), {}

            # Nothing was translated at all (model server unreachable, wrong
            # model, ...): report a failure instead of presenting the original
            # text as the translation.
            total_units = result['chunk_count']
            if total_units and failed_count >= total_units:
                return content, "", t(lang, "status.all_failed"), {}

            progress(0.95, desc=t(lang, "progress.finalizing"))

            # End unified performance monitoring
            chunk_count = result.get('chunk_count', len(content.split('\n\n')))
            self.performance_monitor.end_translation(timing_data, chunk_count)

            progress(1.0, desc=t(lang, "progress.done"))

            # UNIFIED: Simplified metadata without document type specifics
            metadata = {
                "translation_info": {
                    "source_language": self.language_manager.display_name(source_code, lang),
                    "target_language": self.language_manager.display_name(target_code, lang),
                    "source_language_code": source_code,
                    "target_language_code": target_code,
                    "interface_language": lang,
                    "translation_style": actual_style,
                    "style_display": style_display,
                    "translation_timestamp": datetime.now().isoformat(),
                    "engine_used": "Unified-Pipeline",
                    "content_analysis": complexity,
                    "unified_processing": True,
                    "rate_limited": True,
                    "performance_monitored": self.performance_monitor.enabled
                }
            }
            if docx_path is not None:
                metadata["docx_path"] = str(docx_path)

            # UNIFIED: Status with unified pipeline info
            total_time = result.get('total_time', 0)
            total_chunks = result.get('chunk_count', chunk_count)
            chunks_per_second = total_chunks / total_time if total_time > 0 else 0
            avg_time = self.performance_monitor.get_average_time()

            status_parts = [
                t(lang, "status.summary", chunks=total_chunks, seconds=f"{total_time:.1f}",
                  rate=f"{chunks_per_second:.1f}")
            ]

            if translation_mode == "styled":
                status_parts.append(t(lang, "status.style", style=style_display))
            if glossary:
                status_parts.append(t(lang, "status.glossary_terms", count=len(glossary)))
            if organization_terms:
                status_parts.append(t(lang, "status.organization_terms", count=len(organization_terms)))

            if complexity['has_tables']:
                status_parts.append(t(lang, "status.tables", count=complexity['table_count']))
            if complexity['has_code']:
                status_parts.append(t(lang, "status.code_blocks", count=complexity['code_block_count']))

            if failed_count > 0:
                status_parts.insert(0, t(lang, "status.failed_sections",
                                         failed=failed_count, total=total_chunks))
            if failed_cells > 0:
                status_parts.insert(0, t(lang, "status.failed_cells", count=failed_cells))
            if formatting_fallbacks > 0:
                status_parts.insert(0, t(lang, "status.formatting_fallbacks", count=formatting_fallbacks))
            if docx_path is not None:
                status_parts.append(t(lang, "status.inplace"))

            if quality_findings:
                details = "; ".join(
                    t(lang, "status.finding_section", index=f['chunk_index'] + 1,
                      issues=" / ".join(t(lang, f"finding.{code}", **params)
                                        for code, params in f.get('issue_details', [])[:2]))
                    for f in quality_findings[:5]
                )
                more = ("" if len(quality_findings) <= 5 else
                        t(lang, "status.findings_more", count=len(quality_findings) - 5))
                status_parts.insert(0, t(lang, "status.findings", count=len(quality_findings),
                                         details=details, more=more))

            if avg_time > 0:
                status_parts.append(t(lang, "status.average", seconds=f"{avg_time:.1f}"))

            status = " | ".join(status_parts)

            return content, translated_text, status, metadata

        except Exception as e:
            # End performance monitoring on error
            if timing_data:
                self.performance_monitor.end_translation(timing_data, 0)

            logger.error(f"Pipeline failed: {str(e)}", exc_info=True)
            return "", "", t(lang, "status.pipeline_error", error=str(e)), {}

    def _create_unified_progress_callback(self, progress, start_time, lang=None):
        """Create unified progress callback for all document types"""
        def unified_progress(data):
            current = data.get('chunk_index', 0) + 1
            total = data.get('total_chunks', 1)
            kind = "table" if data.get('is_table') else "code" if data.get('is_code') else "text"
            chunk_type = t(lang, f"progress.kind.{kind}")

            # Calculate ETA
            elapsed = time.time() - start_time
            if current > 1:
                time_per_chunk = elapsed / (current - 1)
                remaining = (total - current) * time_per_chunk
                eta = f" (~{remaining:.0f}s)" if remaining > 5 else ""
            else:
                eta = ""

            desc = t(lang, "progress.section", kind=chunk_type, current=current, total=total, eta=eta)
            progress(0.4 + 0.5 * (current / total), desc=desc)

        return unified_progress

    def export_unified_document(self, format_type: str,
                                translation_state: Optional[Dict[str, Any]],
                                lang: Optional[str] = None) -> Tuple[str, str]:
        """
        Export the translation of the CURRENT SESSION.

        Args:
            format_type: "Word", "Markdown", "HTML" or "JSON"
            translation_state: content of the session's gr.State,
                {"translation": str, "metadata": dict} or None
            lang: interface language of the session
        """
        translation = (translation_state or {}).get("translation")
        if not translation:
            return "", t(lang, "status.no_translation")

        try:
            enhanced_metadata = dict((translation_state or {}).get("metadata") or {})
            enhanced_metadata.update({
                'export_timestamp': datetime.now().isoformat(),
                'unified_pipeline': True
            })

            exporters = {
                "Word": self.export_handler.export_word,
                "Markdown": self.export_handler.export_markdown,
                "HTML": self.export_handler.export_html,
                "JSON": self.export_handler.export_json,
            }
            exporter = exporters.get(format_type)
            if exporter is None:
                return "", t(lang, "status.unsupported_format", format=format_type)

            docx_source = (translation_state or {}).get("metadata", {}).get("docx_path")
            if format_type == "Word" and docx_source and Path(docx_source).exists():
                # Word input translated in place: hand out that file.
                export_dir = self.export_handler._new_export_dir()
                file_path = str(export_dir / f"translation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx")
                shutil.copy(docx_source, file_path)
            elif format_type == "Word":
                file_path = exporter(translation, metadata=enhanced_metadata, lang=lang)
            else:
                file_path = exporter(translation, metadata=enhanced_metadata)

            if file_path:
                filename = Path(file_path).name
                return file_path, t(lang, "status.export_done", filename=filename)
            else:
                return "", t(lang, "status.export_failed")

        except Exception as e:
            logger.error(f"export failed: {e}")
            return "", t(lang, "status.export_error", error=str(e))

    async def test_unified_connection(self) -> str:
        """Test LLM connection for unified pipeline"""
        try:
            client = LLMClient(self.config)
            await client.start()

            timeout = self.config.llm.timeout
            start_time = time.time()

            is_healthy = await client.health_check()
            elapsed = time.time() - start_time

            await client.close()

            if is_healthy:
                return (f"🟢Connection OK ({elapsed:.1f}s, timeout: {timeout}s)")
            else:
                return "🔴  Pipeline Connection failed"
        except Exception as e:
            return f"🔴  Pipeline Connection error: {str(e)}"

    def cleanup(self):
        """Cleanup unified resources (safe to call more than once)."""
        if getattr(self, '_cleaned_up', False):
            return
        self._cleaned_up = True
        try:
            if hasattr(self, 'temp_dir') and self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
            if hasattr(self, 'export_handler'):
                self.export_handler.cleanup()

            # Log final unified performance summary
            if hasattr(self, 'performance_monitor') and self.performance_monitor.enabled:
                logger.info(self.performance_monitor.get_performance_summary())

        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")

    def _upload_cache_policy(self) -> Optional[Tuple[int, int]]:
        """
        (frequency, age) in seconds for Gradio's delete_cache, from
        document_processing.upload_retention_minutes; None disables deletion.

        Without it, every uploaded document stays in Gradio's cache
        indefinitely, also after the application has stopped.
        """
        minutes = getattr(self.config.document_processing, 'upload_retention_minutes', None)
        if not minutes:
            return None
        age = max(1, int(minutes * 60))
        frequency = max(5, min(600, age // 2))
        return (frequency, age)

    def interface_language(self, request: Optional[gr.Request] = None) -> str:
        """
        Interface language for a page view: ?lang= URL parameter, a fixed
        interface.language, the browser's Accept-Language (when "auto"),
        otherwise interface.fallback_language.
        """
        ui = self.config.interface
        accept, url_lang = None, None
        if request is not None:
            try:
                accept = request.headers.get("accept-language")
                url_lang = dict(request.query_params or {}).get("lang")
            except Exception:
                pass
        return resolve_language(getattr(ui, 'language', 'auto'), accept, url_lang,
                                getattr(ui, 'fallback_language', 'en'))

    def create_unified_interface(self) -> gr.Blocks:
        """Create unified interface for all document types"""

        interface_config = self.config.interface
        # Built in the fallback (or fixed) language; every page view switches
        # to its own language on load (see on_page_load below).
        base_lang = self.interface_language(None)

        theme = gr.themes.Default(
            font=["ui-sans-serif", "sans-serif"],
            font_mono=["ui-monospace", "monospace"]
        )
        # Custom CSS
        custom_css = """
            footer{display:none !important}
        """

        # URL presets and tab tracking run natively via
        # interface.load(gr.Request) and input_tabs.select() -> gr.State;
        # no JavaScript is injected.
        url_tracking_js = ""

        # Gradio 6: theme, css and head are passed to launch(), not Blocks()
        self._launch_theme = theme
        self._launch_css = custom_css
        self._launch_head = url_tracking_js

        supported_formats = self.config.document_processing.supported_formats
        file_types = [f".{fmt}" for fmt in supported_formats]
        max_mb = self.config.document_processing.max_file_size_mb
        delimiter = self.config.glossary.csv_delimiter
        max_entries = self.config.glossary.max_entries
        available_styles = self.config.translation.available_styles
        default_style = self.config.translation.default_style
        if default_style not in available_styles:
            default_style = available_styles[0] if available_styles else None
        default_source = self.config.translation.default_source_lang
        default_target = self.config.translation.default_target_lang
        target_codes = {code for _, code in self.language_manager.target_choices()}

        def header_text(lang):
            title = localized(interface_config.title, lang)
            description = localized(interface_config.description, lang)
            # The heading marker must come first; "📝 # Title" is rendered as
            # literal text, not as a heading.
            return f"# 📝 {title}\n\n{description}"

        def style_choices(lang):
            return [(style_label(key, lang), key) for key in available_styles]

        def mode_choices(lang):
            return [(t(lang, "mode.direct"), "direct"), (t(lang, "mode.styled"), "styled")]

        L = base_lang
        with gr.Blocks(
            title=localized(interface_config.title, L),
            analytics_enabled=False,
            delete_cache=self._upload_cache_policy()
        ) as interface:

            header_md = gr.Markdown(header_text(L))

            # Main content - side by side
            with gr.Row(equal_height=True):

                # Left panel - Input
                with gr.Column(scale=1):
                    with gr.Group(elem_classes=["input-panel"]):
                        original_md = gr.Markdown(t(L, "panel.original"))

                        with gr.Tabs() as input_tabs:
                            with gr.TabItem(t(L, "tab.file"), id=0) as tab_file:
                                file_input = gr.File(
                                    label=t(L, "file.label", formats=", ".join(file_types), max_mb=max_mb),
                                    file_types=file_types,
                                    elem_classes=["file-area"]
                                )

                            with gr.TabItem(t(L, "tab.text"), id=1) as tab_text:
                                text_input = gr.Textbox(
                                    label="",
                    show_label=False,
                                    placeholder=t(L, "text.placeholder"),
                                    lines=15,
                                    max_lines=25,
                                    interactive=True
                                )
                                clear_text_btn = gr.Button(t(L, "button.clear_text"),
                                                           size="sm", variant="secondary")

                        original_content = gr.Textbox(
                            label="",
                    show_label=False,
                            lines=15,
                            max_lines=25,
                            interactive=False,
                            visible=False,
                            elem_id="original_display"
                        )

                # Right panel - Output
                with gr.Column(scale=1):
                    with gr.Group(elem_classes=["output-panel"]):
                        translation_md = gr.Markdown(t(L, "panel.translation"))

                        translation_output = gr.Textbox(
                            label="",
                    show_label=False,
                            placeholder=t(L, "translation.placeholder"),
                            lines=15,
                            max_lines=25,
                            interactive=False,
                            elem_id="translation_display",
                            buttons=["copy"]  # Gradio 6: copy button
                        )

                        with gr.Row(visible=False) as export_row:
                            export_word_btn = gr.Button("📄 Word", size="sm", elem_classes=["export-btn"])
                            export_md_btn = gr.Button("📝 Markdown", size="sm", elem_classes=["export-btn"])
                            export_html_btn = gr.Button("🌐 HTML", size="sm", elem_classes=["export-btn"])

                        download_file = gr.File(label=t(L, "label.download"), visible=False)

            # Unified control panel
            with gr.Group(elem_classes=["control-panel", "unified-pipeline"]):

                # Language selection: values are language codes, labels are
                # the languages' own names.
                with gr.Row():
                    source_lang = gr.Dropdown(
                        choices=self.language_manager.source_choices(L),
                        value=default_source,
                        label=t(L, "label.source_language"),
                        scale=2,
                        interactive=True
                    )

                    swap_btn = gr.Button("⇄", size="lg", scale=0, elem_classes=["vertical-center"])

                    target_lang = gr.Dropdown(
                        choices=self.language_manager.target_choices(L),
                        value=default_target,
                        label=t(L, "label.target_language"),
                        scale=2,
                        interactive=True
                    )

                # Translation mode
                with gr.Row():
                    translation_mode = gr.Radio(
                        choices=mode_choices(L),
                        value="direct",
                        label="",
                    show_label=False,
                        elem_classes=["mode-radio"]
                    )

                    # Values are style keys, labels come from the catalog.
                    style_dropdown = gr.Dropdown(
                        choices=style_choices(L),
                        value=default_style,
                        label="",
                    show_label=False,
                        scale=1,
                        visible=False,
                        interactive=True
                    )

                # Glossary
                with gr.Accordion(t(L, "glossary.title", max_entries=max_entries),
                                  open=False) as glossary_accordion:
                    glossary_input = gr.Textbox(
                        label="",
                    show_label=False,
                        placeholder=t(L, "glossary.placeholder", delimiter=delimiter),
                        lines=3,
                        max_lines=5
                    )

                # Unified translate button
                with gr.Row():
                    translate_btn = gr.Button(
                        t(L, "button.translate"),
                        variant="primary",
                        elem_id="translate-btn",
                        elem_classes=["primary-btn", "unified-pipeline"],
                        scale=4
                    )
                    cancel_btn = gr.Button(
                        t(L, "button.cancel"),
                        variant="stop",
                        elem_id="cancel-btn",
                        scale=1
                    )

                # Unified status display
                status_display = gr.Textbox(
                    label="",
                    show_label=False,
                    value=t(L, "status.ready"),
                    interactive=False,
                    max_lines=3,
                    visible=True,
                    elem_classes=["status-bar", "unified-pipeline"]
                )

                # Footer links (imprint, privacy policy, ...) come from the
                # configuration (interface.footer_links); none by default.
                footer_links = getattr(interface_config, 'footer_links', None) or []
                if footer_links:
                    gr.Markdown(" | ".join(
                        f"[{link.label}]({link.url})" for link in footer_links))

            # Event handlers

            # Session state. NOT stored on self: all users share this instance.
            # Active input tab ("File"/"Text").
            current_input_type = gr.State("File")
            # Translation result of this session (for export):
            # {"translation": str, "metadata": dict} or None.
            translation_state = gr.State(None)
            # Interface language of this session.
            lang_state = gr.State(L)

            # Components whose texts depend on the interface language, and
            # their updates for a given language (same order).
            localized_outputs = [
                header_md, original_md, tab_file, tab_text, file_input, text_input,
                clear_text_btn, translation_md, translation_output, download_file,
                source_lang, target_lang, translation_mode, style_dropdown,
                glossary_accordion, glossary_input, translate_btn, cancel_btn,
                status_display,
            ]

            def localized_updates(lang):
                return [
                    gr.update(value=header_text(lang)),
                    gr.update(value=t(lang, "panel.original")),
                    gr.update(label=t(lang, "tab.file")),
                    gr.update(label=t(lang, "tab.text")),
                    gr.update(label=t(lang, "file.label", formats=", ".join(file_types), max_mb=max_mb)),
                    gr.update(placeholder=t(lang, "text.placeholder")),
                    gr.update(value=t(lang, "button.clear_text")),
                    gr.update(value=t(lang, "panel.translation")),
                    gr.update(placeholder=t(lang, "translation.placeholder")),
                    gr.update(label=t(lang, "label.download")),
                    gr.update(label=t(lang, "label.source_language"),
                              choices=self.language_manager.source_choices(lang)),
                    gr.update(label=t(lang, "label.target_language"),
                              choices=self.language_manager.target_choices(lang)),
                    gr.update(choices=mode_choices(lang)),
                    gr.update(choices=style_choices(lang)),
                    gr.update(label=t(lang, "glossary.title", max_entries=max_entries)),
                    gr.update(placeholder=t(lang, "glossary.placeholder", delimiter=delimiter)),
                    gr.update(value=t(lang, "button.translate")),
                    gr.update(value=t(lang, "button.cancel")),
                    gr.update(value=t(lang, "status.ready")),
                ]

            # Tab switches are mirrored into the session state. For tabs,
            # SelectData carries 'value' (the tab label) and 'selected';
            # 'index' is not guaranteed and may be None. The label (in any
            # interface language) is therefore the primary signal.
            text_tab_labels = {t(code, "tab.text") for code in SUPPORTED_LANGUAGES}
            file_tab_labels = {t(code, "tab.file") for code in SUPPORTED_LANGUAGES}

            def on_input_tab_select(evt: gr.SelectData):
                label = str(getattr(evt, 'value', '') or '')
                if label in text_tab_labels:
                    return "Text"
                if label in file_tab_labels:
                    return "File"
                return "Text" if getattr(evt, 'index', 0) == 1 else "File"

            input_tabs.select(fn=on_input_tab_select, outputs=current_input_type,
                              api_visibility="private")

            # On page load: choose the interface language and apply deep-link
            # presets such as ?input=text / ?input=file and ?lang=de / ?lang=en.
            def on_page_load(request: gr.Request):
                params = {}
                if request is not None and request.query_params:
                    params = dict(request.query_params)
                lang = self.interface_language(request)
                requested = str(params.get("input", "file")).lower()
                tab_id = 1 if requested in ("text", "1") else 0
                input_type = "Text" if tab_id == 1 else "File"
                # Switch the tab AND set the state directly (independent of
                # whether the programmatic switch fires a select event).
                return [gr.update(selected=tab_id), input_type, lang] + localized_updates(lang)

            interface.load(
                fn=on_page_load,
                inputs=None,
                outputs=[input_tabs, current_input_type, lang_state] + localized_outputs,
                api_visibility="private"
            )

            def toggle_style(mode, lang):
                is_styled = (mode == "styled")
                btn_classes = ["styled-btn", "primary-btn", "unified-pipeline"] if is_styled else ["primary-btn", "unified-pipeline"]
                btn_text = t(lang, "button.translate_styled") if is_styled else t(lang, "button.translate")
                return (
                    gr.update(visible=is_styled),
                    gr.update(value=btn_text, elem_classes=btn_classes)
                )

            translation_mode.change(
                fn=toggle_style,
                inputs=[translation_mode, lang_state],
                outputs=[style_dropdown, translate_btn],
                api_visibility="private"
            )

            def swap_languages(source, target):
                # Values are codes; automatic detection cannot become a target.
                if source == "auto":
                    return source, target
                if source in target_codes and target in target_codes:
                    return target, source
                return source, target

            swap_btn.click(
                fn=swap_languages,
                inputs=[source_lang, target_lang],
                outputs=[source_lang, target_lang],
                api_visibility="private"
            )

            # Clear button: text, translation, status and download
            def clear_all_text(lang):
                return (
                    gr.update(value=""),           # text_input
                    gr.update(value=""),           # translation_output
                    gr.update(value="", visible=False),  # original_content
                    gr.update(value=t(lang, "status.ready")),
                    gr.update(visible=False),      # export_row
                    None,                          # translation_state
                    gr.update(value=None, visible=False)  # previous download
                )

            clear_text_btn.click(
                fn=clear_all_text,
                inputs=[lang_state],
                outputs=[text_input, translation_output, original_content, status_display,
                         export_row, translation_state, download_file],
                api_visibility="private"
            )

            # Translate (async generator on Gradio's event loop). While the job
            # runs, the sections translated so far are shown
            # (translation.show_partial_results); the final result replaces them.
            show_partial = getattr(self.config.translation, 'show_partial_results', True)

            async def safe_unified_translate(input_type, text, file, source, target, mode, style,
                                             glossary, lang, progress=gr.Progress()):
                # The active tab is only a hint; what decides is which input
                # is actually filled.
                input_type = resolve_input_type(input_type, text, file)
                logger.info(f"Translate requested: input_type='{input_type}', "
                            f"text_len={len(text) if text else 0}, file={file is not None}")
                # Formality follows the selected style (key ending in "_du").
                formality = formality_for_style(style_key_for_label(style))

                updates: asyncio.Queue = asyncio.Queue()
                job = asyncio.ensure_future(self.process_and_translate_unified(
                    input_type, text, file, source, target, mode, style, glossary, formality,
                    progress=progress, lang=lang,
                    partial=(updates.put_nowait if show_partial else None)
                ))
                try:
                    while not job.done():
                        try:
                            partial_text = await asyncio.wait_for(updates.get(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        while not updates.empty():
                            partial_text = updates.get_nowait()
                        yield (gr.update(), gr.update(), partial_text, gr.update(),
                               gr.update(), gr.update(), gr.update())
                    content, translation, status, metadata = job.result()
                except asyncio.CancelledError:
                    job.cancel()
                    logger.info("Translation cancelled by user")
                    raise
                except Exception as e:
                    logger.error(f"Translation error: {e}", exc_info=True)
                    yield (
                        gr.update(),
                        gr.update(),
                        "",
                        gr.update(value=t(lang, "status.pipeline_error", error=str(e)), visible=True),
                        gr.update(visible=False),
                        None,
                        gr.update(value=None, visible=False)
                    )
                    return

                if translation:
                    # Text input: the original is already visible in the input
                    # field. File input: show the extracted content.
                    show_original = (input_type == "File")
                    yield (
                        gr.update(value=content, visible=show_original),
                        gr.update(),  # text input stays unchanged and editable
                        translation,
                        gr.update(value=status, visible=True),
                        gr.update(visible=True),
                        {"translation": translation, "metadata": metadata},
                        gr.update(value=None, visible=False)  # drop previous download
                    )
                else:
                    show_original = (input_type == "File") and bool(content)
                    yield (
                        gr.update(value=content if content else "", visible=show_original),
                        gr.update(),
                        "",
                        gr.update(value=status, visible=True),
                        gr.update(visible=False),
                        None,
                        gr.update(value=None, visible=False)
                    )

            translate_event = translate_btn.click(
                fn=safe_unified_translate,
                inputs=[
                    current_input_type,  # gr.State: "File" or "Text" (active tab)
                    text_input,
                    file_input,
                    source_lang,
                    target_lang,
                    translation_mode,
                    style_dropdown,
                    glossary_input,
                    lang_state
                ],
                outputs=[
                    original_content,
                    text_input,
                    translation_output,
                    status_display,
                    export_row,
                    translation_state,
                    download_file
                ],
                # The interface is the only intended client; the endpoint is
                # not offered to API clients (see SECURITY.md).
                api_visibility="private"
            )

            # Cancelling stops the running job; the job closes its client and
            # removes its working files on the way out.
            cancel_btn.click(
                fn=lambda lang: gr.update(value=t(lang, "status.cancelled"), visible=True),
                inputs=[lang_state],
                outputs=[status_display],
                cancels=[translate_event],
                api_visibility="private"
            )

            # Export: only the gr.State of the calling session is exported.
            def safe_unified_export(format_type, state, lang):
                try:
                    file_path, status = self.export_unified_document(format_type, state, lang)
                    if file_path:
                        return (
                            gr.update(value=file_path, visible=True),
                            gr.update(value=status, visible=True)
                        )
                    return (
                        gr.update(visible=False),
                        gr.update(value=status, visible=True)
                    )
                except Exception as e:
                    return (
                        gr.update(visible=False),
                        gr.update(value=t(lang, "status.export_error", error=str(e)), visible=True)
                    )

            for btn, fmt in ((export_word_btn, "Word"),
                             (export_md_btn, "Markdown"),
                             (export_html_btn, "HTML")):
                btn.click(
                    fn=lambda state, lang, fmt=fmt: safe_unified_export(fmt, state, lang),
                    inputs=[translation_state, lang_state],
                    outputs=[download_file, status_display],
                    api_visibility="private"
                )

        return interface


def _raise_keyboard_interrupt(signum, frame):
    """SIGTERM handler: shut down like Ctrl+C so cleanup runs."""
    raise KeyboardInterrupt


def main():
    """Main entry point for unified document translation system"""
    app = None
    # Service managers stop processes with SIGTERM. By default Python then
    # exits without running cleanup, leaving working files (possibly uploaded
    # documents) behind; treat it like Ctrl+C instead.
    try:
        signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    except (ValueError, AttributeError):
        pass  # not in the main thread / platform without SIGTERM
    try:
        app = UnifiedDocumentTranslationApp()
        interface = app.create_unified_interface()

        config = app.config
        logger.info("Starting Document Translation Pipeline")

        # Queue configuration for async support
        max_concurrent = getattr(config.async_config, 'max_concurrent_tasks', 5)
        interface.queue(default_concurrency_limit=max_concurrent)
        logger.info(f"Queue enabled with concurrency_limit={max_concurrent}")

        auth = None
        if config.interface.auth_username and config.interface.auth_password:
            auth = (config.interface.auth_username, config.interface.auth_password)
        elif config.interface.auth_username or config.interface.auth_password:
            logger.warning("Authentication not enabled: both interface.auth_username "
                           "and interface.auth_password must be set")

        static_dir = PROJECT_ROOT / "static"
        interface.launch(
            server_name=config.interface.host,
            server_port=config.interface.port,
            root_path=config.interface.root_path,
            debug=config.interface.debug_mode,
            share=False,
            show_error=True,
            # Uploads above the limit are rejected by the server before they
            # are stored, not only after the complete file has been received.
            max_file_size=f"{config.document_processing.max_file_size_mb}mb",
            auth=auth,
            # No public API documentation link and no /monitoring endpoint.
            footer_links=[],
            enable_monitoring=False,
            # Gradio 6: theme, css and head are passed to launch()
            theme=app._launch_theme,
            css=app._launch_css,
            head=app._launch_head,
            # Favicon
            favicon_path=str(static_dir / "favicon.ico"),
            # Allow access to the static folder
            allowed_paths=[str(static_dir)]
        )

    except KeyboardInterrupt:
        logger.info("application terminated by user")
    except Exception as e:
        logger.error(f"application error: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        if app:
            app.cleanup()


if __name__ == "__main__":
    main()

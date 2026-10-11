# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Configuration management for the translation interface.
Uses Pydantic for type safety and validation.
"""

import os
import copy
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Type, Union
import yaml
from pydantic import BaseModel, Field, field_validator, ConfigDict
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

# Logger Setup
logger = logging.getLogger(__name__)

# All relative paths (configuration, prompts, static files, log file, temp
# directory) are resolved against the project root, so the application
# behaves the same regardless of the directory it is started from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_LANGUAGES_PATH = PROJECT_ROOT / "config" / "languages.yaml"
# Optional, git-ignored file for installation-specific values (endpoint,
# model, host, footer links, ...). It is deep-merged over config.yaml.
DEFAULT_LOCAL_CONFIG_PATH = PROJECT_ROOT / "config" / "config.local.yaml"


def resolve_path(path: str) -> Path:
    """Resolve a configured path; relative paths are taken from the project root."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of base with override merged in recursively (override wins)."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class LLMConfig(BaseModel):
    """LLM API configuration"""
    api_base: str = Field(default="http://localhost:11434/v1")
    api_key: str = Field(default="local-llm-key")
    # Placeholder: set this to the model name your server exposes.
    model: str = Field(default="your-model-name")
    # None means: do not send a temperature and let the server/model use its
    # own default. Current models (in particular reasoning models) are tuned
    # for their default and some reject other values. Consistency of
    # terminology is enforced via glossary/context, not via sampling.
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    # Upper limit for the length of an answer, in tokens. "auto" sizes it per
    # request from the text sent (with room for longer target languages) and
    # caps it at what is left of context_window after the prompt; an answer
    # that still hits the limit is requested again with the full remaining
    # window, and if it is cut off anyway, the text is split and translated in
    # parts. A number sets a fixed limit for every request.
    max_tokens: Union[int, str] = Field(default="auto")
    # Additional answer tokens for models that reason before answering (their
    # reasoning counts against the limit on many servers).
    reasoning_reserve_tokens: int = Field(default=0, ge=0)
    timeout: int = Field(default=120, gt=0)
    max_retries: int = Field(default=3, ge=1)
    retry_delay: int = Field(default=5, gt=0)
    # Upper bound for the exponential backoff so a failing chunk cannot stall
    # the job for many minutes.
    max_retry_delay: int = Field(default=30, gt=0)
    # Context window of the model. Prompt and answer must fit into it together;
    # used for the answer limit (max_tokens "auto") and, if prompt plus
    # glossary would not fit, to reduce the glossary to the relevant terms.
    context_window: int = Field(default=32000, gt=0)

    @field_validator('max_tokens')
    @classmethod
    def validate_max_tokens(cls, v):
        if isinstance(v, str):
            if v.strip().lower() == "auto":
                return "auto"
            if v.strip().isdigit():
                v = int(v.strip())
            else:
                raise ValueError('max_tokens must be "auto" or a positive number')
        if v <= 0:
            raise ValueError('max_tokens must be "auto" or a positive number')
        return v

    @field_validator('api_base')
    @classmethod
    def validate_api_base(cls, v):
        """Ensure API base URL is properly formatted"""
        if not v.startswith(('http://', 'https://')):
            raise ValueError('API base must start with http:// or https://')
        return v.rstrip('/')


class AsyncConfig(BaseModel):
    """Async operations configuration for graceful shutdown and Event Loop management"""
    # Graceful shutdown settings
    shutdown_timeout: int = Field(default=5, gt=0)
    task_cancellation_timeout: float = Field(default=3.0, gt=0)

    # Client pool settings
    client_pool_limit: int = Field(default=20, gt=0, le=100)
    client_pool_per_host: int = Field(default=2, gt=0, le=20)

    # Connection settings
    connection_keepalive_timeout: int = Field(default=120, gt=0)
    connection_ttl_dns_cache: int = Field(default=300, gt=0)

    # Event Loop monitoring
    enable_cleanup_logging: bool = Field(default=True)
    event_loop_check_interval: float = Field(default=1.0, gt=0)

    # Session management
    session_cleanup_interval: int = Field(default=80, gt=0)
    max_idle_sessions: int = Field(default=10, gt=0)

    # Task management
    max_concurrent_tasks: int = Field(default=10, gt=0, le=200)
    task_monitoring_enabled: bool = Field(default=True)

    # Recovery settings
    auto_recovery_enabled: bool = Field(default=True)
    max_recovery_attempts: int = Field(default=4, ge=1)
    recovery_delay: float = Field(default=60.0, gt=0)


class ChunkingConfig(BaseModel):
    """Chunking configuration"""
    max_tokens: int = Field(default=1500, gt=0)
    max_table_tokens: int = Field(default=2000, gt=0)
    chars_per_token: Dict[str, float] = Field(default_factory=lambda: {
        "en": 4.0,
        "de": 3.5,
        "fr": 4.2,
        "es": 4.1,
        "it": 4.0,
        "pt": 4.0,
        "nl": 3.8,
        "pl": 3.6,
        "ru": 3.0,
        "zh": 1.5,
        "ja": 2.0,
        "ko": 2.5,
        "ar": 3.0,
        "hi": 2.5,
        "default": 4.0
    })


class ParallelProcessingConfig(BaseModel):
    """
    Limits for parallel work.

    max_workers: sections of one job translated at the same time.
    rate_limit: simultaneous model requests of one job (sections, table
        cells and glossary terms together).
    max_concurrent_requests: simultaneous model requests of the whole
        application, all users together. None = no global limit (each job is
        still bounded by rate_limit, and the number of jobs by
        async_config.max_concurrent_tasks).
    """
    max_workers: int = Field(default=3, ge=1, le=10)
    rate_limit: int = Field(default=20, gt=0)
    max_concurrent_requests: Optional[int] = Field(default=None, gt=0)


class SimpleChunkingConfig(BaseModel):
    """Simple chunking configuration"""
    # Default chunking settings
    max_tokens: int = Field(default=1500, gt=0)
    chars_per_token: float = Field(default=4.0, gt=0)

    # Language-specific overrides
    language_overrides: Dict[str, float] = Field(default_factory=lambda: {
        "en": 4.0,
        "de": 3.5,
        "fr": 3.8,
        "es": 3.9,
        "it": 3.9,
        "pt": 3.9,
        "nl": 3.6,
        "ru": 3.2,
        "zh": 1.5,
        "ja": 1.8,
        "ko": 2.0,
        "ar": 3.0,
        "hi": 2.5
    })

    # Sentence splitting settings
    min_sentence_length: int = Field(default=10, ge=1)
    fallback_to_char_based: bool = Field(default=True)


class DocumentProcessingConfig(BaseModel):
    """Document processing configuration"""
    max_file_size_mb: int = Field(default=30, gt=0)
    supported_formats: List[str] = Field(default_factory=lambda: ["pdf", "docx", "txt", "md"])
    temp_dir: str = Field(default="./temp")
    # PDF: let pymupdf4llm run OCR (Tesseract) on pages without a text layer.
    # Only effective when Tesseract and the language data are installed.
    pdf_ocr: bool = Field(default=True)
    # Tesseract language codes, e.g. "eng", "deu" or "deu+eng".
    pdf_ocr_language: str = Field(default="eng")
    # Gradio keeps every uploaded file in its own cache (GRADIO_TEMP_DIR,
    # default <system temp>/gradio). Files older than this are deleted
    # periodically, and the cache is cleared when the server starts.
    # None keeps uploads until the cache is cleaned up by other means.
    upload_retention_minutes: Optional[float] = Field(default=60, gt=0)
    # Word documents: "inplace" translates inside the original file (layout,
    # headers/footers, footnotes, text boxes and formatting are kept);
    # "markdown" builds a new document from the translated text.
    docx_mode: str = Field(default="inplace")

    @field_validator('docx_mode')
    @classmethod
    def validate_docx_mode(cls, v):
        if v not in ("inplace", "markdown"):
            raise ValueError("docx_mode must be 'inplace' or 'markdown'")
        return v

    @field_validator('temp_dir')
    @classmethod
    def create_temp_dir(cls, v):
        """Ensure temp directory exists"""
        resolve_path(v).mkdir(parents=True, exist_ok=True)
        return v


class FooterLink(BaseModel):
    """A link shown in the page footer (e.g. imprint, privacy policy)."""
    label: str
    url: str


class InterfaceConfig(BaseModel):
    """Interface configuration"""
    # Bind to loopback by default. Exposing the service on all interfaces
    # (0.0.0.0) is a deliberate decision for the local configuration, because
    # the application itself has no user management.
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=7867, gt=0, lt=65536)
    # URL prefix when served behind a reverse proxy.
    root_path: str = Field(default="/translate")
    theme: str = Field(default="soft")
    debug_mode: bool = Field(default=False)
    # Interface language: "auto" follows the browser (Accept-Language), or a
    # fixed "en" / "de". fallback_language is used when the browser asks for
    # none of the supported languages. A ?lang=de / ?lang=en URL parameter
    # overrides both.
    language: str = Field(default="auto")
    fallback_language: str = Field(default="en")
    # Either one text for all languages or one text per language code,
    # e.g. {"en": "Document Translator", "de": "Dokumentenübersetzer"}.
    title: Union[str, Dict[str, str]] = Field(default_factory=lambda: {
        "en": "Document Translator", "de": "Dokumentenübersetzer"})
    description: Union[str, Dict[str, str]] = Field(default_factory=lambda: {
        "en": "Translate texts and documents with a local language model",
        "de": "Übersetzen Sie Texte und Dokumente mit einem lokalen Sprachmodell"})
    # Optional HTTP basic authentication. Both must be set to take effect.
    # Prefer setting the password via TRANSLATION_INTERFACE__AUTH_PASSWORD.
    auth_username: Optional[str] = Field(default=None)
    auth_password: Optional[str] = Field(default=None)
    footer_links: List[FooterLink] = Field(default_factory=list)


class PerformanceLoggingConfig(BaseModel):
    """Performance logging configuration"""
    enabled: bool = Field(default=True)
    log_slow_translations: bool = Field(default=True)
    slow_translation_threshold: float = Field(default=10.0, gt=0)  # seconds
    log_memory_usage: bool = Field(default=False)


class LoggingConfig(BaseModel):
    """Logging configuration"""
    level: str = Field(default="INFO")
    format: str = Field(default="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file: Optional[str] = Field(default="translation_app.log")
    max_size_mb: int = Field(default=10, gt=0)
    backup_count: int = Field(default=5, ge=0)

    # Performance Logging
    performance_logging: PerformanceLoggingConfig = Field(default_factory=PerformanceLoggingConfig)

    @field_validator('level')
    @classmethod
    def validate_level(cls, v):
        """Validate log level"""
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f'Invalid log level. Must be one of {valid_levels}')
        return v.upper()


class TranslationConfig(BaseModel):
    """Translation configuration"""
    default_source_lang: str = Field(default="de")
    default_target_lang: str = Field(default="en")
    available_styles: List[str] = Field(default_factory=lambda: [
        "professionell", "akademisch", "einfach", "technisch"
    ])
    default_style: str = Field(default="professionell")

    # Quality features (first/second pass). Both cost additional model calls
    # but noticeably improve consistency and completeness.
    auto_glossary: bool = Field(default=True)
    auto_glossary_max_terms: int = Field(default=100, ge=0, le=500)
    # Local structural comparison of source and translation (numbers, list
    # items, table rows, glossary adherence). No model calls, so it is cheap.
    structural_check: bool = Field(default=True)
    # Additional model-based review (omissions, additions, meaning changes).
    # Costs one extra model call per section.
    quality_check: bool = Field(default=False)
    # How already translated text is handed on to keep the style consistent:
    # "two_wave" (first text section first, then the rest in parallel with it
    # as style reference), "sequential" (each section sees the previous one,
    # no parallelism) or "parallel" (no style reference).
    context_mode: str = Field(default="two_wave")
    # Length of the style reference in characters (0 disables it).
    style_reference_chars: int = Field(default=600, ge=0)
    # Language of the instructions to the model: "de" or "en". Independent of
    # the interface language; compare both with tools/prompt_eval.py.
    prompt_language: str = Field(default="de")
    # Tables: "cell" translates every cell on its own, "row" a whole row in
    # one request (more context, fewer requests; falls back to cells when the
    # answer does not have the right number of cells).
    table_mode: str = Field(default="cell")
    # How the automatic glossary is built: "model" (one request that selects
    # and translates the terms) or "heuristic" (capitalisation-based
    # candidates, one request per term). "model" falls back to "heuristic"
    # if the answer cannot be read.
    auto_glossary_method: str = Field(default="model")
    # Show the translated sections in the interface while the job runs.
    show_partial_results: bool = Field(default=True)

    @field_validator('prompt_language')
    @classmethod
    def validate_prompt_language(cls, v):
        if v not in ("de", "en"):
            raise ValueError("prompt_language must be 'de' or 'en'")
        return v

    @field_validator('table_mode')
    @classmethod
    def validate_table_mode(cls, v):
        if v not in ("cell", "row"):
            raise ValueError("table_mode must be 'cell' or 'row'")
        return v

    @field_validator('auto_glossary_method')
    @classmethod
    def validate_glossary_method(cls, v):
        if v not in ("model", "heuristic"):
            raise ValueError("auto_glossary_method must be 'model' or 'heuristic'")
        return v

    @field_validator('context_mode')
    @classmethod
    def validate_context_mode(cls, v):
        allowed = ("two_wave", "sequential", "parallel")
        if v not in allowed:
            raise ValueError(f"context_mode must be one of {allowed}")
        return v


class ExportConfig(BaseModel):
    """Export configuration"""

    class MarkdownConfig(BaseModel):
        preserve_line_breaks: bool = True
        code_style: str = "fenced"

    class WordConfig(BaseModel):
        # template, font and font_size are accepted for compatibility with
        # existing configuration files but are not used by the exporter.
        template: Optional[str] = None
        font: str = "Calibri"
        font_size: int = 11
        # Prepend an information block (languages, style, date) to the
        # exported document. Off by default: the export should contain the
        # translated content only.
        include_header: bool = False

    markdown: MarkdownConfig = Field(default_factory=MarkdownConfig)
    word: WordConfig = Field(default_factory=WordConfig)


class DevelopmentConfig(BaseModel):
    """Development configuration (ignored)"""
    hot_reload: bool = Field(default=True)
    show_examples: bool = Field(default=True)
    collect_metrics: bool = Field(default=True)
    test_mode: bool = Field(default=False)


class GlossaryConfig(BaseModel):
    """Glossary configuration"""
    max_entries: int = Field(default=1000, gt=0)
    case_sensitive_default: bool = Field(default=False)
    csv_delimiter: str = Field(default=",")
    # Glossary of the organisation (CSV: source_lang,target_lang,term,
    # translation), relative to the project directory. None = not used.
    # See config/organization_glossary.example.csv.
    organization_file: Optional[str] = Field(default=None)


class DebuggingConfig(BaseModel):
    """Debugging configuration for event loop and session monitoring (ignored)"""
    # Event Loop monitoring
    log_event_loop_status: bool = Field(default=False)
    event_loop_check_frequency: int = Field(default=30, gt=0)  # seconds

    # Session lifecycle tracking
    track_session_lifecycle: bool = Field(default=False)
    log_session_creation: bool = Field(default=False)
    log_session_closure: bool = Field(default=False)

    # Task monitoring
    monitor_task_completion: bool = Field(default=False)
    log_task_lifecycle: bool = Field(default=False)
    track_active_tasks: bool = Field(default=True)

    # Client management debugging
    log_client_operations: bool = Field(default=False)
    monitor_client_pool: bool = Field(default=False)

    # Performance monitoring
    track_request_timing: bool = Field(default=True)
    log_slow_requests: bool = Field(default=True)
    slow_request_threshold: float = Field(default=5.0, gt=0)  # seconds

    # Memory monitoring
    enable_memory_monitoring: bool = Field(default=False)
    memory_check_interval: int = Field(default=60, gt=0)  # seconds


class BackwardsCompatibilityConfig(BaseModel):
    """Backwards compatibility configuration (ignored)"""
    # Legacy system support
    support_legacy_chunker: bool = Field(default=True)
    support_legacy_retry_handler: bool = Field(default=True)
    support_legacy_translation_engine: bool = Field(default=True)

    # Migration warnings
    warn_on_legacy_usage: bool = Field(default=True)
    deprecation_warnings: bool = Field(default=True)


class Config(BaseSettings):
    """Main configuration class"""
    model_config = ConfigDict(
        env_prefix="TRANSLATION_",
        # Nested keys use a double underscore, e.g. TRANSLATION_LOGGING__LEVEL.
        env_nested_delimiter="__",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """
        Precedence: environment > .env > YAML files > defaults.

        The YAML content arrives as init arguments. Without this override the
        init arguments would win, and TRANSLATION_* variables could never
        change a value that is also present in config.yaml.
        """
        return env_settings, dotenv_settings, init_settings, file_secret_settings

    # Core configuration
    llm: LLMConfig = Field(default_factory=LLMConfig)
    async_config: AsyncConfig = Field(default_factory=AsyncConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    parallel_processing: ParallelProcessingConfig = Field(default_factory=ParallelProcessingConfig)

    # Chunking configuration (runtime)
    simple_chunking: SimpleChunkingConfig = Field(default_factory=SimpleChunkingConfig)

    # Standard configurations
    document_processing: DocumentProcessingConfig = Field(default_factory=DocumentProcessingConfig)
    interface: InterfaceConfig = Field(default_factory=InterfaceConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    development: DevelopmentConfig = Field(default_factory=DevelopmentConfig)
    glossary: GlossaryConfig = Field(default_factory=GlossaryConfig)
    debugging: DebuggingConfig = Field(default_factory=DebuggingConfig)
    backwards_compatibility: BackwardsCompatibilityConfig = Field(default_factory=BackwardsCompatibilityConfig)

    # Additional configuration from languages.yaml
    languages: Dict[str, Any] = Field(default_factory=dict)
    language_pairs: Dict[str, Any] = Field(default_factory=dict)
    style_adjustments: Dict[str, Any] = Field(default_factory=dict)

    def __init__(self, **kwargs):
        """Initialize with environment variable overrides"""
        super().__init__(**kwargs)

        # Overrides from environment variables
        if api_key := os.getenv("OPENAI_API_KEY"):
            self.llm.api_key = api_key

        if api_base := os.getenv("OPENAI_API_BASE"):
            self.llm.api_base = api_base

        if debug := os.getenv("DEBUG"):
            self.interface.debug_mode = debug.lower() in ("true", "1", "yes")
            # Enable debugging features in debug mode
            if self.interface.debug_mode:
                self.debugging.log_event_loop_status = True
                self.debugging.track_session_lifecycle = True
                self.debugging.monitor_task_completion = True

    def setup_logging(self):
        """Configure console and (optional) rotating file logging."""
        handlers = []

        # Console Handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(self.logging.format))
        handlers.append(console_handler)

        # File Handler. delay=True: the file is only created once something is
        # actually logged.
        if self.logging.file:
            from logging.handlers import RotatingFileHandler
            log_path = resolve_path(self.logging.file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=self.logging.max_size_mb * 1024 * 1024,
                backupCount=self.logging.backup_count,
                encoding="utf-8",
                delay=True
            )
            file_handler.setFormatter(logging.Formatter(self.logging.format))
            handlers.append(file_handler)

        # force=True: replace handlers installed earlier (by imported libraries
        # or a previous call). Without it basicConfig silently does nothing
        # once the root logger has a handler, and the log file is never written.
        logging.basicConfig(
            level=getattr(logging, self.logging.level),
            handlers=handlers,
            force=True
        )

        logger.info("Logging system configured")

        # Log performance logging status
        if self.logging.performance_logging.enabled:
            logger.info(f"Performance logging enabled: slow threshold={self.logging.performance_logging.slow_translation_threshold}s")

    def get_language_config(self, lang_code: str) -> Dict[str, Any]:
        """Get the configuration of a specific language"""
        return self.languages.get(lang_code, {})

    def get_chars_per_token(self, lang_code: str) -> float:
        """Get the estimated number of characters per token for a language"""
        # Priority: languages.yaml > simple_chunking.language_overrides > chunking.chars_per_token
        lang_config = self.get_language_config(lang_code)
        if 'chars_per_token' in lang_config:
            return lang_config['chars_per_token']

        # Try simple_chunking overrides
        if hasattr(self, 'simple_chunking') and lang_code in self.simple_chunking.language_overrides:
            return self.simple_chunking.language_overrides[lang_code]

        # Fallback to main chunking config
        return self.chunking.chars_per_token.get(
            lang_code,
            self.chunking.chars_per_token.get('default', 4.0)
        )

    # Helper methods for async configuration
    def get_shutdown_timeout(self) -> int:
        """Get graceful shutdown timeout in seconds"""
        return self.async_config.shutdown_timeout

    def get_client_pool_settings(self) -> Dict[str, int]:
        """Get client pool configuration"""
        return {
            'limit': self.async_config.client_pool_limit,
            'limit_per_host': self.async_config.client_pool_per_host,
            'keepalive_timeout': self.async_config.connection_keepalive_timeout,
            'ttl_dns_cache': self.async_config.connection_ttl_dns_cache
        }

    def is_debugging_enabled(self, feature: str) -> bool:
        """Check if specific debugging feature is enabled"""
        return getattr(self.debugging, feature, False)

    def should_log_slow_requests(self) -> bool:
        """Check if slow request logging is enabled"""
        return (self.debugging.log_slow_requests and
                self.debugging.track_request_timing)

    def get_slow_request_threshold(self) -> float:
        """Get threshold for slow request logging"""
        return self.debugging.slow_request_threshold

    def get_simple_chunking_config(self) -> SimpleChunkingConfig:
        """Get simple chunking configuration"""
        return self.simple_chunking

    def should_warn_on_legacy_usage(self) -> bool:
        """Check if warnings should be shown for legacy component usage"""
        return self.backwards_compatibility.warn_on_legacy_usage

    def get_performance_logging_config(self) -> PerformanceLoggingConfig:
        """Get performance logging configuration"""
        return self.logging.performance_logging


def _read_yaml(path: Path, label: str) -> Dict[str, Any]:
    """Read a YAML mapping; a missing file yields an empty mapping."""
    if not path.exists():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        logger.info(f"{label} loaded from {path}")
        return data
    except Exception as e:
        logger.warning(f"Failed to load {label} from {path}: {e}")
        return {}


def load_config(
    config_path: Optional[Path] = None,
    languages_path: Optional[Path] = None,
    local_config_path: Optional[Path] = None
) -> Config:
    """
    Load the configuration from the YAML files.

    Args:
        config_path: path of the main configuration file
        languages_path: path of the language configuration file
        local_config_path: Optional installation-specific overrides, merged
            over the main configuration (default: config/config.local.yaml)

    Returns:
        Config: loaded and validated configuration
    """
    # Default paths are anchored at the project root, not the working directory.
    config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    languages_path = Path(languages_path) if languages_path else DEFAULT_LANGUAGES_PATH
    local_config_path = Path(local_config_path) if local_config_path else DEFAULT_LOCAL_CONFIG_PATH

    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}, using defaults")
    config_data = _read_yaml(config_path, "Configuration")

    if not languages_path.exists():
        logger.warning(f"Languages file not found: {languages_path}")
    config_data.update(_read_yaml(languages_path, "Languages configuration"))

    # Installation-specific overrides (optional, not part of the repository).
    local_data = _read_yaml(local_config_path, "Local configuration overrides")
    if local_data:
        config_data = deep_merge(config_data, local_data)

    # Create the config object
    try:
        config = Config(**config_data)
        config.setup_logging()
        logger.info("Configuration successfully validated and loaded")
        return config
    except Exception as e:
        logger.error(f"Configuration validation failed: {e}")
        raise


def get_default_config() -> Config:
    """Get the default configuration"""
    config = Config()
    config.setup_logging()
    return config


# Singleton instance for global access
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global config instance"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: Config):
    """Set the global config instance (for tests)"""
    global _config
    _config = config
# Configuration

## Files and precedence

| Source | Purpose |
|---|---|
| `config/config.yaml` | Defaults shipped with the repository |
| `config/languages.yaml` | Languages offered in the interface |
| `config/config.local.yaml` | Installation-specific values; optional, not in the repository, merged over `config.yaml` key by key |
| `.env` in the project directory | `TRANSLATION_...` variables (optional); `OPENAI_*`, `DEBUG` and `TEMP_DIR` are only read from the real environment |
| Environment variables | Override all files |

Precedence, highest first: `OPENAI_API_BASE` / `OPENAI_API_KEY` / `DEBUG`,
`TRANSLATION_...` environment variables, `TRANSLATION_...` entries in `.env`,
`config.local.yaml`, `config.yaml` and `languages.yaml`, built-in defaults.

Relative paths in the configuration are resolved against the project
directory, not against the working directory.

## Environment variables

| Variable | Effect |
|---|---|
| `OPENAI_API_BASE` | Sets `llm.api_base` |
| `OPENAI_API_KEY` | Sets `llm.api_key` |
| `DEBUG` | `true`/`1`/`yes` sets `interface.debug_mode` |
| `TEMP_DIR` | Overrides `document_processing.temp_dir` |
| `TRANSLATION_<SECTION>__<KEY>` | Any configuration key, e.g. `TRANSLATION_LOGGING__LEVEL=DEBUG`, `TRANSLATION_INTERFACE__AUTH_PASSWORD=...` (double underscore between section and key) |
| `GRADIO_TEMP_DIR` | Read by Gradio: directory for uploaded files (default `<system temp>/gradio`) |

## Keys

### `llm`

| Key | Default | Meaning |
|---|---|---|
| `api_base` | `http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `api_key` | `local-llm-key` | Sent as bearer token |
| `model` | `your-model-name` | Model name as exposed by the server (placeholder) |
| `temperature` | `null` | `null` = not sent, the server default applies |
| `max_tokens` | `auto` | Answer length limit: `auto` sizes it per request (see below), a number fixes it |
| `reasoning_reserve_tokens` | `0` | Extra answer tokens for models that reason before answering |
| `timeout` | `120` | Seconds per request, including generation |
| `max_retries` | `3` | Attempts per request in total (connection errors, timeouts, HTTP 429/5xx) |
| `retry_delay`, `max_retry_delay` | `5`, `30` | Exponential backoff, capped |
| `context_window` | `32000` | Context window of the model; prompt and answer must fit into it. Used for the answer limit and to shorten the glossary if the prompt would not fit |

With `max_tokens: auto` every request gets a limit derived from the length
of the text it sends (with room for a longer target language), capped at
what remains of `context_window` after the prompt. An answer that still
reaches the limit is requested once more with the whole remaining window; if
it is cut off again, the section is split and translated in parts.

### `parallel_processing` and `async_config`

| Key | Default | Meaning |
|---|---|---|
| `parallel_processing.max_workers` | `3` | Sections of one job translated at the same time |
| `parallel_processing.rate_limit` | `20` | Simultaneous model requests of one job |
| `parallel_processing.max_concurrent_requests` | `null` | Simultaneous model requests of the whole application; `null` = no global limit |
| `async_config.max_concurrent_tasks` | `5` | Simultaneous translation jobs (Gradio queue) |

Without `max_concurrent_requests`, up to `max_concurrent_tasks × rate_limit`
requests can reach the model server at the same time. All other keys of
`async_config` are ignored.

### `simple_chunking` and `chunking`

| Key | Default | Meaning |
|---|---|---|
| `simple_chunking.max_tokens` | `700` | Section size in (estimated) tokens |
| `simple_chunking.language_overrides` | | Characters per token per language |
| `chunking.chars_per_token` | | Fallback characters per token |

Characters per token are taken from `languages.yaml`, then
`simple_chunking.language_overrides`, then `chunking.chars_per_token`.
`chunking.max_tokens`, `chunking.max_table_tokens`,
`simple_chunking.min_sentence_length` and `fallback_to_char_based` are ignored.

### `document_processing`

| Key | Default | Meaning |
|---|---|---|
| `max_file_size_mb` | `30` | Upload limit, enforced by the server |
| `supported_formats` | `pdf, docx, txt, md` | Accepted file extensions |
| `temp_dir` | `./temp` | Working directory for uploads during processing |
| `pdf_ocr` | `true` | OCR for PDF pages without text layer (needs Tesseract) |
| `pdf_ocr_language` | `eng` | Tesseract languages, e.g. `deu+eng` |
| `upload_retention_minutes` | `60` | Uploads older than this are deleted from Gradio's cache; `null` = never |
| `docx_mode` | `inplace` | Word input: `inplace` translates inside the original file, `markdown` builds a new document |

### `interface`

| Key | Default | Meaning |
|---|---|---|
| `host` | `127.0.0.1` | Listen address; `0.0.0.0` = all interfaces |
| `port` | `7867` | Listen port |
| `root_path` | `/translate` | URL prefix behind a reverse proxy |
| `language` | `auto` | `auto` (browser), `en` or `de` |
| `fallback_language` | `en` | When the browser asks for neither |
| `title`, `description` | English and German texts | One text, or one per language code |
| `auth_username`, `auth_password` | `null` | HTTP basic authentication, both required |
| `footer_links` | `[]` | List of `{label, url}` shown below the interface |
| `debug_mode` | `false` | Gradio debug mode |
| `theme` | | Ignored |

### `translation`

| Key | Default | Meaning |
|---|---|---|
| `default_source_lang`, `default_target_lang` | `de`, `en` | Preselected languages (codes) |
| `available_styles` | five styles | Style keys offered in the interface |
| `default_style` | `professionell_sie` | Preselected style key |
| `auto_glossary` | `true` | Collect and translate recurring terms first |
| `auto_glossary_max_terms` | `100` | Upper limit of collected terms |
| `context_mode` | `two_wave` | `two_wave`, `sequential` or `parallel`, see [design](design/translation-pipeline.md) |
| `style_reference_chars` | `600` | Length of the style reference; `0` disables it |
| `prompt_language` | `de` | Language of the instructions to the model: `de` or `en` |
| `table_mode` | `cell` | `cell` (each cell alone) or `row` (one request per row) |
| `auto_glossary_method` | `model` | `model` (one request) or `heuristic` (one request per term) |
| `show_partial_results` | `true` | Show translated sections while the job runs |
| `structural_check` | `true` | Local comparison of source and translation |
| `quality_check` | `false` | Additional review by the model (one call per section) |

The style keys (`professionell_sie`, `professionell_du`, `akademisch`,
`einfach`, `technisch`) are identifiers; their labels come from the interface
text catalog.

### `glossary`

| Key | Default | Meaning |
|---|---|---|
| `max_entries` | `1000` | Maximum entries of the user glossary |
| `csv_delimiter` | `,` | Separator between term and translation |
| `case_sensitive_default` | `false` | Case-sensitive matching of glossary terms |
| `organization_file` | `null` | Glossary of the organisation (CSV), relative to the project directory; see [operations](operations.md#maintain-the-organisation-glossary) |

### `logging`

| Key | Default | Meaning |
|---|---|---|
| `level` | `INFO` | Log level |
| `format` | | Python `logging` format string |
| `file` | `translation_app.log` | Log file (relative to the project directory); empty = console only |
| `max_size_mb`, `backup_count` | `10`, `5` | Rotation |
| `performance_logging.*` | | Slow-translation logging; `log_memory_usage` needs psutil |

### `export`

`export.word.include_header` (default `false`) prepends a block with
languages, style and date to Word exports, in the interface language.
`export.word.template`, `font`, `font_size` and `export.markdown` are ignored.

### Ignored sections

`development`, `debugging` and `backwards_compatibility` are accepted so
that existing configuration files keep validating; they have no effect.

## `languages.yaml`

Each language has `code`, `name`, `native_name` (shown in the interface),
`available_as_source`, `available_as_target` and `chars_per_token`. The
sections `language_pairs`, `style_adjustments` and `auto_detection`, and the
key `sentence_endings`, are ignored.

## Limitations

- Configuration is read at start; changes require a restart.
- An invalid configuration is reported in the log and the application starts
  with the built-in defaults (listening on `127.0.0.1`).
- Unknown keys are ignored silently; a misspelt key therefore has no effect.

# Architecture

## Overview

```
Browser ──HTTP──> Gradio interface (src/app.py)
                     │
                     ├─ document_processing/  file → MarkdownDocument
                     │     word_processor.py, pdf_processor.py, text
                     ├─ markdown_converter.py  MarkdownDocument → Markdown text
                     ├─ chunking/simple_chunker.py  Markdown → sections
                     ├─ translation/translation_engine.py
                     │     glossary pass, context, style reference,
                     │     tables cell by cell, checks
                     │        └─ llm_client.py ──HTTP──> model endpoint
                     ├─ reassembly (src/app.py)  sections → Markdown
                     └─ ui/export_handlers.py  Markdown → Word / Markdown / HTML
```

PDF and Word files are converted to Markdown; pasted text, Markdown and
text files are used as they are. Word documents are, by default, translated
inside the original file (`docx_inplace.py`); their Markdown text is then
used for the glossary pass and the original view only.

## Components

| Module | Responsibility |
|---|---|
| `main.py` | Entry point; starts `src.app.main()` |
| `src/app.py` | Interface, request handling, status texts, reassembly, startup and shutdown |
| `src/config.py` | Configuration model (pydantic), loading and merging of the YAML files, environment variables, logging setup |
| `src/i18n.py` | Interface texts (English, German) and choice of the interface language |
| `src/languages.py` | Compatibility names for the German prompt language names (see `prompt_texts.py`) |
| `src/organization_glossary.py` | Glossary of the organisation: CSV file, language pairs, matching of inflected forms |
| `src/markdown_utils.py` | The single definition of what a Markdown table is |
| `src/document_processing/` | Format-specific import (Word, PDF, text) into a common document model |
| `src/models/document.py` | Document model and processing options |
| `src/chunking/simple_chunker.py` | Splitting into sections at paragraph and sentence boundaries; tables are kept whole or split by rows with the header repeated |
| `src/translation/translation_engine.py` | Glossary pass, prompt context, context modes, table and code handling, output cleaning, checks |
| `src/translation/docx_inplace.py` | Translation inside the original Word file (segments, markers, write-back) |
| `src/prompt_texts.py` | Texts sent to the model, per prompt language |
| `src/llm_client.py` | HTTP client for the chat completions API; the only place with retries |
| `src/ui/export_handlers.py` | Export to Word, Markdown, HTML (and JSON, not offered in the interface) |
| `prompts/base_translation.txt`, `base_translation_en.txt` | Base prompt (German, English) |
| `tools/prompt_eval.py` | Compares the prompt languages with your model on a reference set |

## Request flow

1. The browser sends text or a file. A file is copied into a per-request
   directory under `temp/`, converted in a worker thread (PDF: in the single
   PDF thread), and the copy is deleted.
2. The glossary is composed: entries typed by the user, then the entries of
   the organisation glossary whose terms occur in the document.
3. The Markdown text is split into sections.
4. A new `LLMClient` and `TableAwareTranslationEngine` are created for the job.
5. If enabled, the automatic glossary is built (one request) and merged
   below the other two.
6. Text path: sections are translated according to
   `translation.context_mode`; each prompt contains the style instruction,
   glossary, section heading, neighbouring source text and, if available,
   the style reference. Sections are reassembled; split tables are joined.
   Word in place: paragraphs are grouped into requests of the same size,
   translated with formatting markers and written back into the original
   file; the result is read back for display.
7. Answers cut off at the length limit are requested again or split
   (see [translation pipeline](design/translation-pipeline.md#answer-length)).
8. Source and translation are compared section by section.
9. The client is closed; the result goes into the session state of the
   browser tab and is shown.

## State

- **Shared:** one application object per process (configuration, language
  list, chunker, converter, export handler, performance counters). It holds
  no user data.
- **Per session** (browser tab): `gr.State` values for the active input tab,
  the interface language and the last translation (for export).
- **Per job:** client and engine, discarded afterwards.
- **Partial results:** finished sections are passed from the engine
  (`on_chunk_done`) or the in-place translator to the interface handler,
  which streams them to the browser while the job runs.

## Concurrency

The Gradio queue runs up to `async_config.max_concurrent_tasks` jobs at the
same time. Within a job, `parallel_processing.max_workers` sections run in
parallel and `parallel_processing.rate_limit` limits the simultaneous model
requests (sections, table cells, glossary terms). An optional
`parallel_processing.max_concurrent_requests` limits all requests of the
process together. Requests, interface events and streaming run on one
asyncio event loop; document conversion and the parsing and saving of Word
files run in worker threads, so that a large file does not block other
sessions.

## Languages

The interface language (labels, status texts, the optional Word export
block) comes from `src/i18n.py`. The instructions to the model come from
`prompts/` and `src/prompt_texts.py` in the configured prompt language,
regardless of the interface language.

## Limitations

- One process; there is no shared state between several processes (e.g.
  the global request limit applies per process).
- No persistence: translations exist only in the browser session and in the
  temporary export files.
- The JSON exporter exists but is not offered in the interface.
- `LLMClient.detect_language()` exists but is not used; automatic detection
  is left to the model.

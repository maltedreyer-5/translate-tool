# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-11

First public release.

### Features

- Web interface (Gradio) to translate pasted text and documents: PDF, Word
  (`.docx`), plain text, Markdown.
- Word documents are translated inside the original file: styles, page
  layout, headers and footers, footnotes and endnotes, text boxes, images,
  tables and character formatting are kept; headers, footers, footnotes,
  endnotes and text boxes are translated as well.
- Any OpenAI-compatible chat completions endpoint as translation backend.
- Translation in sections with section heading, neighbouring text and a
  style reference from already translated text (`two_wave`, `sequential` or
  `parallel` mode).
- User glossary, glossary of the organisation (CSV file, re-read on change)
  and automatic document glossary, selected and translated by the model in
  one request (heuristic alternative).
- Translation modes: keep the original style, or adapt it (professional
  formal/informal, academic, plain language, technical).
- Table-aware processing: tables are kept intact and translated cell by
  cell or row by row; identical cells are translated once.
- Answer length limit sized per request; answers cut off at the limit are
  requested again or the section is split, so no text is lost silently. Long
  Word paragraphs are split into pieces of the regular section size.
- Local structural check of every section; optional review by the model.
- Translated sections are shown while the job is running.
- Export as Word, Markdown and HTML.
- Instructions to the model in German or English, and a tool to compare both
  on a reference set with your model (`tools/prompt_eval.py`).
- Interface in English and German, chosen from the browser language,
  the configuration or a URL parameter.
- Configuration via YAML with an optional local override file and environment
  variables; limits for parallel jobs, sections and model requests.
- Optional HTTP basic authentication; loopback-only by default.
- Cancellation of running translations; working files and cached uploads are
  removed automatically.

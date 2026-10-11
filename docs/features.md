# Features

## Input

- **Files:** PDF, Word (`.docx`), plain text (`.txt`) and Markdown (`.md`),
  up to `document_processing.max_file_size_mb` (default 30 MB). The list of
  accepted extensions is `document_processing.supported_formats`.
- **Text:** pasted directly into the text tab.

**Word documents are translated inside the original file**
(`document_processing.docx_mode: inplace`): the result keeps styles, page
layout, headers and footers, footnotes, text boxes, images, tables and
character formatting; body text, tables, headers, footers, footnotes,
endnotes and text boxes are translated. See
[Word documents](design/word-documents.md).

Markdown and text files are used exactly as they are, like pasted text;
text files may be UTF-8 (with or without BOM), UTF-16 with BOM or
Windows-1252/Latin-1. PDFs are converted to Markdown (headings, paragraphs,
emphasis, links, lists, tables) and translated as text. PDFs are converted with
pymupdf4llm; pages without a text layer are recognised with OCR if Tesseract
is installed (`document_processing.pdf_ocr`).

## Languages

Source and target languages are defined in `config/languages.yaml`. The
supplied file contains German, English, French, Spanish, Italian, Portuguese,
Dutch, Polish, Russian, Chinese (simplified), Japanese and Korean; the source
can also be "detect automatically", in which case the model is asked to
determine the source language itself.

## Translation modes

- **Translate directly:** the style of the original is kept.
- **Translate and adapt style:** one of the configured styles is applied:
  professional with formal address (German "Sie"), professional with informal
  address ("Du"), academic, plain language, technical. The formal/informal
  distinction matters for target languages that have one (German in
  particular); for other languages a formal or informal register is requested.

## Consistency across a document

Documents are translated in sections of about `simple_chunking.max_tokens`
tokens (default 700), cut at paragraph and sentence boundaries. Each section
is sent with:

- the heading of the section and about 300 characters of the source text
  before and after it, for reference;
- a **style reference**: the translation of the first text section
  (`translation.context_mode: two_wave`, default) or of the previous section
  (`sequential`);
- the **glossary**: entries entered by the user (one `term,translation` pair
  per line, up to `glossary.max_entries`), the **glossary of the
  organisation** (fixed translations of bodies, degrees, units and other
  names, maintained by the operator in a CSV file), and terms collected
  automatically from the document (`translation.auto_glossary`). User
  entries take precedence over the organisation glossary, which takes
  precedence over the automatic terms. By default the model
  selects and translates these terms in one request
  (`auto_glossary_method: model`); the alternative is a capitalisation-based
  heuristic with one request per term.

Tables are translated cell by cell (`translation.table_mode: cell`) or row by
row (`row`, one request per row, cell by cell as fallback), with the column
heading as context; identical cells are translated once. Numbers, dates and other cells without
letters are passed through unchanged. Code blocks are sent with the
instruction to translate only comments and texts.

## Checks

After the translation, every section is compared with its source without a
model call (`translation.structural_check`, default on): numbers, list items,
table rows, length and glossary terms. Deviations are reported in the status
line. An additional review by the model (omissions, additions, changed
meaning) can be switched on with `translation.quality_check`; it costs one
model call per section.

## Output

The translation is shown next to the original; while the job runs, the
sections translated so far are shown (`translation.show_partial_results`).
It can be copied or exported as Word, Markdown or HTML; for Word input
translated in place, the Word export is the translated original file. Sections that could not be translated keep their
original text and are reported in the status line. A running translation can
be cancelled.

## Interface

The interface is available in English and German. The language follows the
browser setting (`interface.language: auto`), can be fixed in the
configuration, and can be chosen per link with `?lang=en` or `?lang=de`.
`?input=text` opens the text tab.

## Limitations

- **Word:** comments, field codes, document properties and text in charts
  or embedded objects are not translated. Character formatting inside a
  paragraph depends on the model keeping the formatting markers; if it does
  not, that paragraph loses it (reported). Exports of *other* formats to Word
  build a new document without the original layout.
- **PDF layout is not preserved.** The output is Word, Markdown or HTML, not
  PDF. Multi-column layouts, footnotes and figure captions depend on the
  conversion by pymupdf4llm.
- **`.doc` (the binary Word format) is not supported.** Convert to `.docx` first.
- **The automatic glossary is a heuristic.** It looks for recurring
  capitalised words and phrases; it finds little in languages without
  capitalisation (Chinese, Japanese, Korean), mostly proper names in English,
  and may include general nouns in German.
- **Automatic language detection is done by the model**, not by a separate
  detector.
- **The instructions to the model are in German** by default
  (`translation.prompt_language: de`); English is available. Which works
  better depends on the model; `tools/prompt_eval.py` compares both.
- **The checks are indicators, not proof.** The structural check compares
  counts; the model review depends on the model's judgement.
- **Throughput depends entirely on the model server.** Long documents can take
  minutes.
- The application translates; it does not evaluate translation quality
  against references and does not store translations.

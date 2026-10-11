# Design note: translation pipeline

## Goal

Translate whole documents with a language model so that the result reads as
one text: consistent style, consistent terminology, nothing left out, and the
document structure (headings, lists, tables) intact.

## Non-goals

- Reproducing the visual layout of the original (see [Word documents](word-documents.md)).
- Evaluating translation quality against reference translations.
- Storing translations or translation memories.

## Small sections

Language models translate long inputs less reliably: passages are skipped,
summarised or end early when the answer limit is reached. The document is
therefore split into sections of about `simple_chunking.max_tokens` tokens,
cut at headings, paragraphs and, if necessary, sentences. Splitting happens
*after* sentence-ending punctuation so the punctuation stays with its
sentence; a split paragraph is joined again without a paragraph break.

Tables are never cut through a row. A table that is too large is split by
rows, and every part repeats the header row so that each part is a valid
table on its own; the parts are joined into one table on reassembly. What
counts as a table is defined once, in `src/markdown_utils.py`: if chunker,
engine and exporter disagreed, a table would be translated as running text or
torn apart.

## Consistency across sections

Splitting creates the problem it is meant to solve: each section is
translated without knowing how the others were translated. Three mechanisms
counter this.

**Context.** Each section is sent with its heading and about 300 characters
of the source text before and after it, marked as reference only.

**Style reference.** Already translated text is handed on as an example of
the established tone, register, form of address and terminology
(`translation.context_mode`):

| Mode | Behaviour | Cost |
|---|---|---|
| `two_wave` (default) | The first text section is translated alone; its translation is the reference for all other sections, which then run in parallel | The time of one section |
| `sequential` | Every section receives the translation of the previous one | No parallelism; slowest |
| `parallel` | No reference | Fastest; least consistent |

`two_wave` is the default because it fixes the decisions made at the start
of a document (form of address, register, renderings of the first terms) for
all sections while keeping parallel processing. `sequential` gives the
strongest continuity for documents whose style develops, at the price of
throughput. The reference is limited to `style_reference_chars` characters
and is marked as not to be translated or repeated.

**Glossary.** Before the sections are translated, the terms that must be
rendered the same way everywhere are determined and translated once. By
default (`auto_glossary_method: model`) this is one request: the model
receives as much of the document as fits, plus a list of candidates from the
whole document, and answers with the selected terms and their translations as
JSON. Terms that do not occur in the document are discarded. If the answer
cannot be read, the heuristic is used instead: recurring capitalised words and
phrases (sentence-initial words in running text are not counted, words of
neighbouring table cells are never joined), each translated in a request of
its own with an example sentence. All sections then receive the same
renderings. Precedence: entries the user types in the interface, then the
glossary of the organisation (`glossary.organization_file`), then the
automatically collected terms. From the organisation glossary only the
entries whose term occurs in the document are used (inflected forms
included), so a large glossary does not enlarge every request.
If prompt and glossary would exceed `llm.context_window`, only glossary
entries that occur in the section are sent.

## Tables

Tables are translated cell by cell by default, with the column heading as
context. A whole table sent as Markdown is answered with shifted or merged
columns often enough that the structure cannot be relied on; per cell, the
structure is fixed by construction. `table_mode: row` sends one row per
request with every cell in a `<cN>` marker, which gives the model the context
of the row and needs fewer requests; if the answer does not contain exactly
the cells sent, that row is translated cell by cell. Identical cells are
translated once per table, cells without letters (numbers, dates, codes) are
not sent at all, and cells are translated in parallel within the request
limits.

## Retries

A failed request (connection error, timeout, HTTP 429 or 5xx) is repeated
in exactly one place, `LLMClient.complete()`, with capped exponential
backoff. A second retry layer would multiply attempts and waiting time. A
section that still fails keeps its original text and is reported; the rest
of the document is not lost.

`llm.timeout` is the total time of a request including generation. A
non-streamed answer sends nothing until it is complete, so a shorter
per-read timeout would abort every slow generation.

## Answer length

A fixed limit on the length of answers would either be too large for a
table cell or too small for a long section, and an answer cut off at the
limit would silently lose the end of the text. With `llm.max_tokens: auto`
the limit is set per request: the length of the text sent, with room for a
longer target language, plus `reasoning_reserve_tokens` for models that
reason first, capped at what remains of `context_window` after the prompt.

If the server reports that an answer stopped at the limit
(`finish_reason: length`), the request is repeated once with the whole
remaining window. If it is cut off again, nothing of it is used: the section
is split near its middle (at a paragraph break, else a line break, else a
sentence end, else a space) and both halves are translated, down to four
levels. Only if a section cannot be split further does it keep its original
text and get reported.

## What the model sees

The instructions are German by default (`prompts/base_translation.txt`)
and English with `prompt_language: en` (`prompts/base_translation_en.txt`);
the style and formality instructions and the language names follow
(`src/prompt_texts.py`). The prompt language is independent of the interface
language: switching the interface must not change the translation. German is
the default because the application was developed and used with it; whether
English instructions work better depends on the model and can be measured
with `tools/prompt_eval.py` (chrF against reference translations and the
structural check, for both prompt languages on the same texts). Document metadata (author,
title, dates) is not content; it is neither sent to the model nor written into
exports. Answers are cleaned of inline reasoning blocks, introductory phrases
("Here is the translation:") and a code fence around the whole answer.

## Checks

The structural check compares numbers, list items, table rows, length and
glossary terms without a model call and runs by default. The model review is
optional because it doubles the number of calls; it is asked to report
problems, not to translate again.

## Limitations

- The style reference describes the beginning of the document (`two_wave`) or
  the previous section (`sequential`); changes of style within a document are
  not modelled further.
- The glossary heuristic depends on capitalisation (see [features](../features.md)).
- Per-cell translation loses context between cells of the same row.
- The checks can miss errors and can report differences that are correct
  (e.g. numbers written as words in the translation).

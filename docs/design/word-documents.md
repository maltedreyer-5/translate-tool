# Design note: Word documents

## Goal

A translated Word document should be the original document in another
language: same styles, page layout, headers and footers, footnotes, images,
tables and character formatting.

## Approach: translation inside the original file

With `document_processing.docx_mode: inplace` (default) the application
opens the original `.docx`, replaces the text of every paragraph with its
translation and saves the result as a new file. Everything that is not text
stays untouched, because it is never rebuilt.

Translated: body text, tables, headers and footers (all variants), footnotes
and endnotes, text boxes. Not translated: comments, field codes (tables of
contents, page numbers, cross references are kept and can be updated in
Word), document properties.

### Paragraphs and formatting

A paragraph consists of runs, pieces of text with one character formatting.
Consecutive runs with the same formatting form a *segment*; hyperlinks,
tracked insertions, content controls and simple fields are segments of their
own. Runs that are not text (images, footnote references, complex fields,
page breaks) are *atoms*. The model receives the paragraph with markers:

```
Das ist <s2>sehr</s2> wichtig<x3/>.
```

`<sN>…</sN>` encloses the text of a segment, `<xN/>` stands for an atom,
`<br/>` and `<tab/>` for line breaks and tabs. The model translates the text
and keeps the markers, moving them with their words where the word order of
the target language requires it. The translated text of each segment is
written into the first run of that segment, so its formatting is kept; if the
markers come back in a different order, the runs are moved into that order.

Several paragraphs are sent in one request, each wrapped in `<pN>…</pN>`,
so that the model sees connected text. A request holds about as much text as
a section of the text pipeline (`simple_chunking.max_tokens`, converted with
the characters per token of the source language). A paragraph longer than
that is split into pieces at segment boundaries, and inside a segment at
sentence ends; the pieces are translated in separate requests and joined
into the paragraph again, a split segment becoming one segment. The same context, style reference and
glossary as in the text pipeline are used (see
[translation pipeline](translation-pipeline.md)). Table cells are sent per
cell or per row (`translation.table_mode`), with the column heading as context;
identical paragraphs are translated once.

### When the model damages the markers

If a paragraph comes back without its `<pN>` marker, it is requested again
on its own; if that fails too, it keeps its original text and is reported.
If the markers inside a paragraph do not match (missing, duplicated,
invented), the paragraph still gets its translation: the whole text goes into
its first segment, paragraph formatting and atoms stay, but the character
formatting inside that paragraph is lost. The status line reports how many
paragraphs this concerned.

## Other formats and the other exports

PDF, Markdown and text input, and Word input with `docx_mode: markdown`,
take the text path: the document is converted to Markdown, translated in
sections and exported as a new document. For Word input translated in place,
the text shown in the interface and the Markdown and HTML exports are read
back from the translated file.

## Limitations

- The quality of the formatting transfer depends on the model keeping the
  markers. Small models damage them more often; the fallback keeps the text
  but loses formatting inside the paragraph.
- Text that changes length can change pagination; manual line breaks and
  fixed-size text boxes are not adjusted.
- Comments, field codes and document properties are not translated.
- Text inside charts, SmartArt and embedded objects is not translated.
- Text boxes stored twice in a file (modern and compatibility copy) are
  translated twice; identical texts are answered from the cache.

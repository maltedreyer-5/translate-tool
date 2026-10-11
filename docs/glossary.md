# Glossary

| Term | Meaning |
|---|---|
| OpenAI-compatible endpoint | A server that accepts `POST /chat/completions` in the format of the OpenAI API (Ollama, vLLM, LM Studio and others) |
| Job | One translation request from the interface |
| Session | One browser tab; holds its own interface language and last translation |
| Section (chunk) | Part of a document translated in one model request, about `simple_chunking.max_tokens` tokens |
| Token | Unit of text for language models; estimated here from characters per token |
| Style reference | Already translated text handed to further sections so that tone and terminology stay consistent |
| Two-wave mode | First text section first, then the remaining sections in parallel with its translation as style reference |
| Sequential mode | Each section receives the translation of the previous one; no parallelism |
| User glossary | Term/translation pairs entered in the interface |
| Organisation glossary | Term/translation pairs per language pair maintained by the operator in a CSV file (`glossary.organization_file`) |
| Automatic glossary | Recurring terms collected from the document and translated once before the sections |
| Structural check | Local comparison of source and translation (numbers, list items, table rows, length, glossary terms) |
| Quality check | Optional review of each section by the model |
| Formality (Sie/Du) | Formal or informal address; mainly relevant for German target texts |
| Native name | The name of a language in that language, e.g. "Français" |
| Fallback language | Interface language used when the browser asks for none of the supported ones |
| Root path | URL prefix under which a reverse proxy serves the application |
| In-place translation | Translation of a Word document inside the original file; only the text is replaced |
| Segment, atom, marker | Parts of a paragraph in in-place translation: text with one formatting, a non-text object, and the `<sN>`/`<xN/>` tags that represent them for the model |
| Prompt language | Language of the instructions sent to the model (`de` or `en`) |
| Gradio cache | Directory where Gradio stores uploaded and downloadable files (`GRADIO_TEMP_DIR`) |

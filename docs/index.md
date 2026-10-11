# Documentation

Document Translator is a web application that translates texts and documents
with a language model behind an OpenAI-compatible API. This directory
describes what it does, how to install and operate it, and why it is built
the way it is.

| Chapter | Content |
|---|---|
| [Features](features.md) | What the application does, input and output formats, limitations |
| [Installation](installation.md) | Requirements, installation, running as a service, reverse proxy |
| [Configuration](configuration.md) | All configuration keys, environment variables, precedence |
| [Architecture](architecture.md) | Components, data flow, concurrency, session state |
| [System impact](system-impact.md) | Files, network, processes; how to undo every change |
| [Operations](operations.md) | Recurring tasks with commands to carry them out and to check them |
| [Glossary](glossary.md) | Terms used in this documentation |

Design notes explain decisions that are not obvious from the code:

- [Translation pipeline](design/translation-pipeline.md): sections, style reference, glossary
- [Interface language](design/interface-language.md): text catalog and language choice
- [Word documents](design/word-documents.md): the Markdown round trip and its limits

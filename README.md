# Document Translator

A web application that translates texts and documents (PDF, Word, Markdown,
plain text) with a language model behind an OpenAI-compatible API, for
example a local Ollama or vLLM server. Documents are translated in small
sections; already translated text, a glossary of the organisation and an
automatically built glossary are handed on between sections to keep style
and terminology consistent. Word
documents are translated inside the original file, so layout, styles,
headers, footers, footnotes and formatting are kept. Results can be exported
as Word, Markdown or HTML.

The interface is available in English and German and follows the browser
language.

## Quick start

Requirements: Python 3.10 or newer, and a reachable OpenAI-compatible
endpoint (`POST <api_base>/chat/completions`).

```bash
pip install -r requirements.txt
```

Create `config/config.local.yaml` with your endpoint and model:

```yaml
llm:
  api_base: "http://localhost:11434/v1"   # Ollama, vLLM, LM Studio, ...
  model: "your-model-name"
```

Start the application and open <http://127.0.0.1:7867>:

```bash
python main.py
```

Installation as a service, the configuration reference and day-to-day
operation are described in the [documentation](docs/index.md).

## Security notice

The application has **no user management**. Everyone who can reach the port
can use the translation service and the model behind it. By default it only
listens on `127.0.0.1`. Make it reachable from other machines only inside a
protected network (VPN, firewall) or behind a reverse proxy with
authentication; optional HTTP basic authentication is available. Uploaded
documents and their text are sent to the configured model endpoint. See
[SECURITY.md](SECURITY.md) for the threat model.

## Documentation

- [Overview](docs/index.md)
- [Features and limitations](docs/features.md)
- [Installation](docs/installation.md) and [configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [System impact](docs/system-impact.md): what the application changes on a host, and how to undo it
- [Operations guide](docs/operations.md)
- [Glossary](docs/glossary.md)
- Design notes: [translation pipeline](docs/design/translation-pipeline.md),
  [interface language](docs/design/interface-language.md),
  [Word documents](docs/design/word-documents.md)

## Third-party components

The application depends on the following packages (see `requirements.txt`);
each is installed separately and keeps its own license.

| Component | Purpose | License |
|---|---|---|
| Gradio | web interface | Apache-2.0 |
| aiohttp | HTTP client | Apache-2.0 AND MIT |
| aiofiles | file I/O | Apache-2.0 |
| pydantic, pydantic-settings | configuration | MIT |
| PyYAML | configuration files | MIT |
| python-docx | Word import and export | MIT |
| PyMuPDF, pymupdf4llm | PDF import | **AGPL-3.0** or commercial license (Artifex) |
| markdown2 | HTML export | MIT |
| Beautiful Soup | HTML export | MIT |
| psutil (optional) | memory figures in the performance log | BSD-3-Clause |

The favicon is the "languages" icon of [Lucide](https://lucide.dev)
(ISC license, see `static/LICENSE-lucide-icons.txt`).

**Note on AGPL-3.0:** this repository is MIT-licensed, but an installation
with PDF support combines it with PyMuPDF/pymupdf4llm. Whoever operates such
an installation as a network service has to meet the AGPL-3.0 obligations of
these components (in particular, offering the corresponding source code to
the users). This is a notice, not legal advice.

## License

MIT, see [LICENSE](LICENSE). Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md).

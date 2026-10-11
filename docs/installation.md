# Installation

## Requirements

- Python 3.10 or newer (tested with 3.10 and 3.12 on Linux).
- An OpenAI-compatible chat completions endpoint, e.g. Ollama, vLLM or
  LM Studio. The application sends `POST <api_base>/chat/completions` and
  reads `choices[0].message.content`.
- Optional, for scanned PDFs: Tesseract with the language data of your
  documents (e.g. `tesseract-ocr` and `tesseract-ocr-deu` on Debian/Ubuntu).
  If PyMuPDF does not find the language data, set `TESSDATA_PREFIX` as
  described in the PyMuPDF documentation.

## Install

```bash
git clone https://github.com/maltedreyer-5/translate-tool.git
cd translate-tool
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` gives the allowed version ranges. To install exactly the
versions the tests ran with (Python 3.12), add the constraints file:

```bash
pip install -r requirements.txt -c constraints.txt
```

## Configure

Installation-specific values go into `config/config.local.yaml`. The file is
not part of the repository (it is listed in `.gitignore`) and is merged over
`config/config.yaml`, so updates of the repository do not overwrite it.

```yaml
llm:
  api_base: "http://localhost:11434/v1"
  model: "your-model-name"
```

All keys are described in [configuration](configuration.md).

## Start

```bash
python main.py
```

The application listens on `http://127.0.0.1:7867`. It can be started from
any directory; configuration, prompts and static files are found relative to
the project directory.

Check that it is up:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7867/   # expect 200
```

## Running as a service (example)

A systemd unit, as an example to adapt; it is not part of the repository and
not tested by it. The application treats `SIGTERM` like Ctrl+C and removes
its working files on shutdown.

```ini
[Unit]
Description=Document Translator
After=network-online.target

[Service]
User=translator
WorkingDirectory=/opt/translate-tool
ExecStart=/opt/translate-tool/.venv/bin/python main.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Reachability from other machines

By default the application only accepts connections from the same machine
(`interface.host: 127.0.0.1`). It has no user management of its own (see
[SECURITY.md](../SECURITY.md)). Options, from preferred to least preferred:

1. Keep `127.0.0.1` and put a reverse proxy with authentication in front.
2. Set `interface.host: "0.0.0.0"` in `config.local.yaml` and restrict access
   at network level (VPN, firewall).
3. Additionally or alternatively, enable HTTP basic authentication with
   `interface.auth_username` and `interface.auth_password` (prefer the
   environment variable `TRANSLATION_INTERFACE__AUTH_PASSWORD`).

### Reverse proxy (example)

`interface.root_path` (default `/translate`) is the URL prefix under which the
proxy serves the application. An nginx example to adapt (not tested in this
repository):

```nginx
location /translate/ {
    proxy_pass http://127.0.0.1:7867/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_buffering off;              # progress updates are streamed
    client_max_body_size 30m;         # document_processing.max_file_size_mb
}
```

## Limitations

- No container image or installation package is provided.
- The service and proxy configurations above are examples; they are not
  tested by this repository.
- `constraints.txt` applies to Python 3.12; on other versions some transitive
  packages (e.g. numpy) need other versions, so use `requirements.txt` alone.

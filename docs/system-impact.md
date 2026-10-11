# System impact

What the application changes on the host it runs on, and how to undo each
change. Everything was checked against the code; the network behaviour of
the web page was observed with a headless browser.

## Files

| What | Where | Lifetime | Contains user data |
|---|---|---|---|
| Log file | `<project>/translation_app.log` and rotations `.1` to `.5` (10 MB each) | Permanent | No document text (lengths, counts and error types); error answers of the model server are logged with their first 200 characters |
| Working directory of an instance | `<project>/temp/tmp*/` with `owner.pid` | Until the instance stops (also on `SIGTERM`); leftovers of killed instances are removed at the next start | Briefly, see next row |
| Copy of an uploaded file | `<project>/temp/tmp*/tmp*/<file name>` | Deleted right after conversion | Yes |
| Uploaded files (Gradio) | `$GRADIO_TEMP_DIR`, default `<system temp>/gradio/` | Deleted after `document_processing.upload_retention_minutes` (default 60); the cache is cleared at server start. With `null`: until removed by other means | Yes |
| Translated Word files and export files | `<system temp>/tmp*/` and, for the download, Gradio's cache | Until the instance stops / as above | Yes (the translation) |

`<system temp>` is the directory Python's `tempfile` uses (usually `/tmp`,
or `$TMPDIR`). In addition, Gradio creates `hash_seed.txt` in its own package
directory on first start (a random value used to name cached files). The
application does not write anywhere else. It reads
`config/*.yaml`, `prompts/`, `static/`, an optional `.env` and, if
configured, the organisation glossary.

## Who can see uploads and downloads

Each browser tab has its own session: the uploaded document, the translation
and the export links are only delivered to that tab, and another session
never sees them (tested with two simultaneous sessions). Files are served by
Gradio under a link that contains a hash of a random server secret and the
file content; the link cannot be guessed or listed. Whoever *has* the exact
link can download the file without further checks until it is deleted
(`upload_retention_minutes`), so links should not be passed on. Paths outside
Gradio's cache and `static/` (configuration, log, working directory, export
directory) are refused with HTTP 403.

## Network

- **Inbound:** HTTP on `interface.host:interface.port` (default
  `127.0.0.1:7867`). Gradio's upload endpoint accepts files from every client
  that reaches the port (within `max_file_size_mb`); they expire like all
  uploads.
- **Outbound:** HTTP(S) requests to `llm.api_base` only. The request
  contains the text to be translated, the glossary and context.
- **No telemetry:** Gradio's analytics and version check are switched off
  in the code (`analytics_enabled=False`); no share tunnel is opened
  (`share=False`). The web page loads no resources from other hosts.

## Processes and system configuration

One Python process with worker threads for document conversion. The
application installs no services, scheduled jobs or
system configuration; it does not need root privileges. PDF OCR uses the
Tesseract installation through PyMuPDF.

## Undoing

| Change | Undo |
|---|---|
| Running instance | Stop it (`Ctrl+C`, `systemctl stop <unit>`); working directory and export files are removed on shutdown |
| Log files | `rm <project>/translation_app.log*` |
| Leftover working directories | `rm -rf <project>/temp/tmp*` (only while no instance is running) |
| Uploaded files in Gradio's cache | `rm -rf "${GRADIO_TEMP_DIR:-/tmp/gradio}"` (only while no instance is running) |
| Python environment | `rm -rf <project>/.venv` |
| Service unit, proxy configuration | Remove the files you created, reload systemd / the proxy |
| Everything | Remove the project directory, then the Gradio cache as above |

Check that nothing is left:

```bash
ls <project>/temp                      # only .gitkeep
ls "${GRADIO_TEMP_DIR:-/tmp/gradio}"   # absent or empty
ss -ltnp | grep 7867                   # no listener
```

## Limitations

- Gradio's cache directory is shared by all Gradio applications of the same
  user on the host unless `GRADIO_TEMP_DIR` is set per application.
- If the process is killed with `SIGKILL` or the host crashes, the working
  directory remains until the next start; files in Gradio's cache remain
  until the next start (cache cleared) or until the retention expires.
- The model endpoint receives the full text of every document; what it
  stores or logs is outside the control of this application.

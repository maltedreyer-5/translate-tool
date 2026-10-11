# Operations guide

Recurring tasks, each with the commands to carry it out and to check the
result. `<project>` is the project directory, `<unit>` the name of your
service unit if you run one.

## Start, stop, status

```bash
python main.py                       # foreground; stop with Ctrl+C
systemctl start <unit>               # as a service
systemctl stop <unit>                # SIGTERM; working files are removed
```

Check:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7867/   # 200
ss -ltnp | grep 7867                 # listen address as configured
ls <project>/temp                    # one tmp* directory per running instance
```

At start the console shows `Local configuration overrides loaded from ...`
(if `config.local.yaml` exists) and `Configuration successfully validated
and loaded`.

## Check the model endpoint

```bash
curl -s <api_base>/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <api_key>" \
  -d '{"model": "<model>", "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 20}'
```

Expected: a JSON object with `choices[0].message.content`. An error here
explains sections that are reported as "not translated".

## Change the configuration

1. Edit `config/config.local.yaml` (not `config.yaml`, which belongs to the
   repository).
2. Restart the application.
3. Check the start messages (see above); an invalid configuration is logged
   as `Config loading failed` and the built-in defaults are used.

Temporary changes without editing files, e.g. more detailed logging:

```bash
TRANSLATION_LOGGING__LEVEL=DEBUG python main.py
```

## Update

```bash
cd <project>
git pull
. .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q                  # optional, needs requirements-dev.txt
systemctl restart <unit>
```

Check: HTTP 200 as above, and one test translation. Read `CHANGELOG.md` for
changed defaults; local values in `config.local.yaml` are kept.

## Logs

- File: `<project>/translation_app.log`, rotated at 10 MB, five old files.
- Translations that took longer than
  `logging.performance_logging.slow_translation_threshold` seconds are
  logged as slow.
- Failed sections appear as `Chunk <n> failed, keeping original text: ...`
  with the error (timeout, connection error, server error).

```bash
tail -f <project>/translation_app.log
grep -c "keeping original text" <project>/translation_app.log
```

## Clean up

Normally not necessary: working files are removed at shutdown and at the next
start, uploads after `upload_retention_minutes`. Manual clean-up while the
application is stopped:

```bash
rm -rf <project>/temp/tmp*
rm -rf "${GRADIO_TEMP_DIR:-/tmp/gradio}"
```

## Maintain the organisation glossary

1. Copy `config/organization_glossary.example.csv` to
   `config/organization_glossary.csv` (this name is ignored by git) and
   enter your terms: one row per term and language pair, columns
   `source_lang,target_lang,term,translation`, UTF-8 (Windows-1252 from
   Excel is accepted too). With `glossary.csv_delimiter: ";"` the file can
   use semicolons.
2. In `config/config.local.yaml`:

   ```yaml
   glossary:
     organization_file: "config/organization_glossary.csv"
   ```

3. Restart once. Later edits of the CSV take effect with the next
   translation, without a restart.

Check: the log shows `Organisation glossary loaded: N entries in M language
pair(s)` and warns about incomplete or duplicate rows; a translation that
uses entries shows "📚 N from the organisation glossary" in the status line.

Only entries whose term occurs in the document are sent to the model;
inflected forms count ("des Akademischen Senats" for "Akademischer Senat").

## Choose the prompt language

```bash
python tools/prompt_eval.py --runs 3 --json eval.json
```

Compares `prompt_language: de` and `en` on `tools/prompt_eval_data.yaml`
with the model configured in `config.local.yaml` (add typical texts of your
own to the file). Switch only if `en` is clearly better; set
`translation.prompt_language` in `config.local.yaml` and restart.

## Limit the load on the model server

Set `parallel_processing.max_concurrent_requests` in `config.local.yaml` to
the number of requests the server should handle at the same time, then
restart. Without it, up to `max_concurrent_tasks × rate_limit` requests
(default 5 × 20) can arrive at once.

## Troubleshooting

| Symptom | Check |
|---|---|
| "N of M sections not translated" | Log entries `keeping original text`; endpoint check above; increase `llm.timeout` for slow models |
| Answers contain reasoning text | The server returns reasoning inline in an unknown format; `<think>` blocks are removed automatically |
| Interface in the wrong language | Browser language settings; `interface.language`; try `?lang=en` |
| Upload rejected | `document_processing.max_file_size_mb` and, behind a proxy, its body size limit |
| Log entries `Answer cut off; translating the section in two parts` | Normal: an answer reached its limit and the section was split. Frequent entries with a reasoning model: set `llm.reasoning_reserve_tokens` (e.g. 2000) |
| "paragraph(s) without character formatting" | The model did not keep the formatting markers of a Word paragraph; try a larger model or `prompt_language: en` |
| Scanned PDF yields no text | Tesseract and its language data installed? `document_processing.pdf_ocr_language` |

## Limitations

- There is no health endpoint besides the page itself and no metrics export.
- The application cannot be reconfigured at runtime.

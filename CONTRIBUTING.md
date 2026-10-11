# Contributing

This file describes the language rules, what "checked" means for a change,
and the architecture rules whose violation has silent consequences.

## Language

- Code, comments, docstrings, documentation, commit messages, issues and pull
  requests: **English**.
- Interface texts: in the text catalog `src/i18n.py`, **in English and German**.
  A new text needs both entries; the tests reject a catalog in which the
  languages differ in keys or placeholders.
- Instructions to the model (`prompts/base_translation.txt`, style and
  formality instructions in `src/llm_client.py`, `src/languages.py`) are
  German. Changing them changes translations; propose such changes with
  examples of their effect.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
```

## Definition of "checked"

A change is checked when all of the following pass. CI runs exactly these
commands (`.github/workflows/ci.yml`) on Python 3.10 and 3.12.

```bash
python -m compileall -q main.py src tests tools   # syntax
python -c "import src.app"                         # import of the application
python -m pytest -q                                # tests (no model server needed)
python tools/check_repo.py                         # links, runtime state, version, SPDX, wording
```

Changes to the interface should additionally be tried in a browser in both
languages (`?lang=en`, `?lang=de`).

## Architecture invariants

Each rule exists because breaking it produces wrong results without an error
message.

1. **Behaviour never depends on displayed text.** Drop-downs pass codes and
   keys (language code, style key, mode value); labels come from the catalog.
   Comparing against a label breaks as soon as the label is translated.
2. **Session data lives in `gr.State`.** The application object is shared by
   all users; storing a document, translation or language on it leaks data
   between sessions.
3. **Requests are retried in one place only**, `LLMClient.complete()`.
   Another retry layer multiplies attempts and waiting time.
4. **What a Markdown table is, is defined once**, in `src/markdown_utils.py`.
   If two components disagree, tables are translated as text or torn apart.
5. **Texts for the model are not interface texts.** They stay out of the
   catalog and do not depend on the interface language.
6. **No document content in logs.** Log lengths, counts and error types.
7. **Document metadata is not content.** Author, title and dates are neither
   sent to the model nor exported (`include_frontmatter=False` in the pipeline).
8. **Cancellation propagates.** Code on the translation path must not swallow
   `asyncio.CancelledError`; otherwise a cancelled job continues and reports
   a failure.
9. **Per-job objects are not registered with `atexit`.** Client and engine
   are created per job; an `atexit` registration keeps each of them alive
   until the process ends.
10. **Paths are resolved against the project directory** (`resolve_path`,
    `PROJECT_ROOT`), never against the working directory.
11. **In-place Word translation only replaces text.** Code in
    `docx_inplace.py` must not rebuild paragraphs, styles or parts; it
    writes text into existing runs and moves existing elements. Markers sent
    to the model are validated before use; a mismatch falls back to plain
    text for that paragraph, never to dropping it.
12. **A cut-off answer is never used.** `translate_text()` raises
    `LLMTruncatedError` when the server reports `finish_reason: length`;
    callers split the text or keep the original, they never accept the
    partial answer.
13. **Identifiers used by existing installations stay stable:** configuration
    keys, style keys, language codes, the URL parameters `input` and `lang`,
    `root_path`. Old configuration keys remain accepted even when ignored.

## Pull requests

- One topic per pull request; describe what changes for users and operators.
- Add or adjust tests for changed behaviour.
- Update the documentation in `docs/` and the `CHANGELOG.md` entry
  "Unreleased" when behaviour or configuration changes.
- By contributing you agree that your contribution is licensed under the MIT
  license of this project.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

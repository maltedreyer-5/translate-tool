# Security policy

## Supported versions

Security fixes are made for the latest release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting ("Report a
vulnerability" on the Security tab of this repository). Do not open a public
issue for security problems. Include the version (or commit), your
configuration (without credentials), the steps to reproduce and the expected
and observed behaviour.

## Threat model

### Intended deployment

The application is meant to be run by an organisation or an individual for
a known group of users, on a host they control, within a protected network
(VPN or internal network) or behind a reverse proxy that authenticates
users. The model endpoint is operated by the same party or a party it trusts
with the document content.

### What the application protects

- **Exposure:** by default it listens on `127.0.0.1` only; listening on other
  interfaces is an explicit configuration decision.
- **Programmatic access:** the interface's event handlers are not offered as a
  public API, and Gradio's monitoring endpoint is disabled.
- **Uploads:** the size limit is enforced by the server before a file is
  stored; uploaded files are removed from Gradio's cache after
  `upload_retention_minutes`; working copies are deleted after conversion and
  at shutdown.
- **Separation of sessions:** documents and translations are kept in the
  session of the browser tab, not in state shared between users; uploads and
  exports use separate directories per request. Download links are
  unguessable (hash of a server secret and the content) and expire with
  `upload_retention_minutes`; files outside Gradio's cache and `static/` are
  not served.
- **Responsiveness:** document conversion runs outside the event loop, so a
  large document of one user does not stall the other sessions.
- **Logs:** document text and file names are not written to the log
  (lengths and counts only); error messages of file converters may contain
  the file name, and error answers of the model server are truncated to 200
  characters.
- **Metadata:** document properties such as author and title are not sent to
  the model and not written into exports.

### Out of scope

The following are deliberate properties of the application or assumptions
about its environment, not vulnerabilities:

- **No user management and no roles.** Everyone who can reach the port can
  translate documents and thereby use the model endpoint. The optional HTTP
  basic authentication is a single shared account. Controlling who reaches
  the application is the job of the network or the reverse proxy.
- **No transport encryption of its own.** The application speaks plain HTTP;
  TLS is terminated by a reverse proxy.
- **The model endpoint sees all content.** Every document is sent to
  `llm.api_base`. Choosing an endpoint that may receive this content, and
  securing the connection to it, is the operator's responsibility.
- **Prompt injection.** Document text is passed to the model as text to be
  translated. A document can contain instructions that influence the model's
  answer, and thereby its own translation. Only the submitting user receives
  that answer; the model has no tools and no access to other data.
- **Download links are bearer links.** Whoever has the exact link of an
  upload or export can fetch it until it expires, without being in the
  session that created it. Links are only shown to that session; do not
  share them. With HTTP basic authentication enabled, a login is required
  in addition.
- **Anonymous uploads.** Gradio accepts uploads from every client that
  reaches the port (size-limited, expiring). Restrict access to the port.
- **Resource consumption by permitted users.** Users can submit large
  documents and many jobs within the configured limits
  (`max_file_size_mb`, `max_concurrent_tasks`, `rate_limit`,
  `max_concurrent_requests`). There is no per-user quota.
- **Operating mistakes:** listening on `0.0.0.0` without network protection,
  committing `config.local.yaml` or `.env` with credentials, disabling the
  upload retention, running with a log level that a third party can read.
- **Vulnerabilities of dependencies** (Gradio, PyMuPDF, python-docx, ...)
  are reported upstream; reports about how this application uses them are
  welcome.

## Hardening checklist for operators

- Keep `interface.host: 127.0.0.1` behind a reverse proxy, or restrict the
  port at network level.
- Set `parallel_processing.max_concurrent_requests` to protect the model
  server.
- Store credentials (`llm.api_key`, `interface.auth_password`) in
  environment variables, not in files under version control.
- Set `GRADIO_TEMP_DIR` to a directory only the service user can read.
- Run the application as an unprivileged user.

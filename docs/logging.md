# Logging

Status: rules for the backend (issue 58); the frontend's copy of this file has the Next.js rules. The format comes from issue 43 and
matches our other product.

When anything goes wrong, one search by correlation ID shows the whole story: which Consultancy,
which request, what the code was doing, and the full traceback with file, function and line.

## The format

One JSON object per line, on stdout. A log viewer added later (CloudWatch Logs, Grafana Loki,
Better Stack or similar) reads these lines unchanged.

**Django** (`consultancy-crm-backend/config/logging.py`, `logging_utils.py`):

```json
{"level": "ERROR", "timestamp": "2026-11-05 14:02:11 +0545",
 "source": "config/exceptions.py: handle_exception, line 41", "tenant": "acme.crm.com",
 "thread": "MainThread", "thread_id": 1407, "correlation_id": "3f1c…",
 "message": "unexpected error: method=PUT path=/api/configuration/dates/",
 "exception": "Traceback (most recent call last): …"}
```

`tenant` is the Consultancy's host (`N/A` on the Platform, the Central login, or outside a
request). `exception` is there only when a traceback was logged.

**Next.js server** (`consultancy-crm-frontend/src/lib/logger/server.ts`, pino): `level` (30 info, 40 warn,
50 error), `time`, `msg`, `type` (`api`, `warn` or `error`) and the entry's fields, always with
`correlationId` when there is one.

The Next.js server creates the correlation ID for each API call and sends it to Django as
`X-Correlation-ID`; Django uses it on every line of that request and returns it. A management
command gets a fresh one per run.

## Levels

`LOG_LEVEL` picks the level, in any case. Anything else stops the server at start-up.

| Level | What you see | Backend | Frontend |
| --- | --- | --- | --- |
| DEBUG | Everything, for local troubleshooting only | `DEBUG` | `debug` |
| INFO (default) | Normal operation | `INFO` | `info` |
| WARNING | Only problems, plus security refusals | `WARNING` | `warn` |
| ERROR | Only failures | `ERROR` | `error` |

- **DEBUG:** detail useful only while debugging, such as which branch was taken or counts. Never
  on by default.
  `logger.debug("validation failed: fields=%s", "email,phone")`
- **INFO:** the one request line, plus business events with ids only: Student created, Stage
  changed, Payment recorded, Document uploaded, message sent or failed, login and logout, and
  every call to an outside service.
  `logger.info("outside call done: provider=%s operation=%s duration_ms=%s", "s3", "put_object", 84)`
- **WARNING:** something went wrong but we recovered, or a security refusal:
  - a token for another Consultancy, a missing `tenant_schema`, a bad internal token, a refused
    origin (Next.js)
  - too many Codes sent, or a Code out of tries
  - a clock-in from outside an Office Network
  - a file not removed
  - a request slower than `LOG_SLOW_REQUEST_MS` (default 1000)
  - a failure we recover from, with its traceback: `logger.warning("central login email not sent", exc_info=True)`
  `logger.warning("token refused: reason=%s", "wrong_consultancy")`
- **ERROR:** an unexpected failure. It always carries the traceback (`logger.exception` inside
  `except`, or `exc_info=`).
  `logger.exception("outside call failed: provider=%s operation=%s duration_ms=%s", "sparrow", "send_sms", 10012)`

## Message style

- Lower case, saying what happened, then the ids, with %-style arguments (never f-strings in a
  log call; ruff's `G` rules refuse them):
  `logger.exception("could not send SMS: provider=%s message_id=%s", provider, message.pk)`.
- Older business-event messages written before this ticket start with a capital (e.g.
  "Payment recorded: …"); tests and searches rely on them, so they stay until they are next
  changed.

## Never logged

- passwords, Codes, tokens, cookies, keys
- authenticator secrets, setup URIs, numbers, recovery codes and two-step challenges (issue 61)
- request or response bodies, document contents, message text
- full emails or phone numbers
- the client's IP address (ADR 0004)

The redaction filter (`RedactionFilter`) is the safety net, not the plan: it blanks
secret-looking `key=value` pairs, email addresses and Nepali mobile numbers in every message and
traceback. Next.js logs only an error's name and stack frames, never its message, which can name
Django's internal address.

## Every `except` block

Each one does one of these:

- re-raises (including turning it into another exception, or `self.fail(...)` in a serializer)
- logs at the right level, with the traceback for ERROR
- carries a one-line comment saying why it is silent

The usual silent ones: a view turning an expected domain refusal into a 4xx answer ("expected
refusal, answered with its status; the request line logs it"), a value that can't be parsed
falling back to a default, and a storage or provider failure that the outside-call wrapper has
already logged at ERROR. ruff enforces the rest: `BLE001` (no bare `except Exception` unless it
logs with the traceback) and `TRY400` (`logger.exception`, not `logger.error`, inside `except`).

## Where each part logs

**Backend**

- `config/middleware.py`: one line per request, `"<METHOD> <path> <status> <n>ms"`; INFO, or
  WARNING when slower than `LOG_SLOW_REQUEST_MS`. Django's own "Bad Request: …" lines are
  dropped (the request line already says it); its "Internal Server Error" line is kept only when
  it has a traceback.
- `config/exceptions.py` (DRF `EXCEPTION_HANDLER`): an unexpected exception is one ERROR line
  with the traceback, answered `500 {"detail": "Something went wrong.", "reference": "<correlation
  id>"}`; a refused login token is a line with its reason code (`expired` at INFO, everything
  else at WARNING); a 400 is one DEBUG line with the field names, never the values. An error
  outside any view (in a middleware) is logged by Django with its traceback and answered the
  same way (`handler500`).
- `auth_tokens/refusals.py`: reason codes `malformed`, `invalid`, `expired`,
  `wrong_consultancy`, `wrong_kind`, `missing_claim`, `out_of_date`, `revoked`.
- `config/outside_calls.py`: `outside_call(provider, operation)` wraps every call to S3/MinIO,
  the product's email server (`email`), an office's own email server (`smtp`) and the SMS
  companies (`sparrow`, `aakash`): INFO on success, ERROR with the traceback on failure, both
  with the duration. Credentials, message text and recipients are never passed to it.
- `config/commands.py`: `LoggedCommand` gives each run of a management command
  (`migrate_schemas`, `create_documents_bucket`, `rebuild_staff_directory`,
  `ensure_platform_admin`, `migrate_on_deploy`) its own correlation ID and logs its start, end (with the duration)
  and any failure. A command that loops over Consultancies uses `tenancy.context.office_context`,
  so each line carries that Consultancy's host as `tenant`.
- `ensure_platform_admin` (issue 59) logs exactly one outcome per run and never a value:
  INFO "platform admin values not set; skipped", ERROR "platform admin values incomplete or
  invalid; skipped: <variable names>", INFO "platform admin already exists; skipped: id=…" or
  INFO "platform admin created: id=…". The Account page logs "platform admin changed: id=…
  fields=…" (field names only) and, at WARNING, "platform admin current password refused" and
  "platform admin password tries limit reached" (ids only).
- Two-step login (`two_factor`, issue 61; kind and id only): INFO "platform admin password
  passed; two-step next", "two-step login set up", "two-step login passed", "new recovery codes
  made", "two-step login switched on/off"; WARNING "two-step number refused", "two-step number
  replayed", "two-step recovery code refused", "recovery code used", "two-step wrong numbers
  limit reached", "two-step challenge used on the wrong host" and "two-step login switch-on
  refused: no secrets key set".
- Staff two-step login (issue 62; ids only): INFO "Staff password passed; two-step next",
  "two-step login switched on/off by an Admin", "two-step login reset", "two-step login switched
  on/off by themselves", "two-step login switch-off refused, wrong password"; the second step logs
  as above with kind `staff`.
- Platform forgot password (issue 63; ids only, never the email, Code or password): INFO
  "Platform password reset code sent: platform_admin_id=…", "Platform password reset asked for no
  active Platform Admin", "Platform password reset: platform_admin_id=…" and the Code service's
  "code accepted: platform_admin_id=… purpose=reset"; WARNING "Platform password reset code
  refused", "Platform password reset limit reached" (per email hash), "code limit reached:
  platform_admin_id=…" and "Platform password reset code not sent" (with the traceback, after
  the outside call's own ERROR). The Next.js server answers 429 for an address over its limit.
- gunicorn's own error log (a worker timeout, a worker killed for memory) goes through the same
  JSON formatter (`logconfig_dict` in `config/gunicorn.py`).

## Reading the logs today

Every line of one request, from both servers:

```sh
docker compose logs backend frontend --no-log-prefix \
  | jq -c 'select(.correlation_id == "3f1c…" or .correlationId == "3f1c…")'
```

Only the backend's errors with their tracebacks:

```sh
docker compose logs backend --no-log-prefix | jq -r 'select(.level == "ERROR") | .message, .exception'
```

A reference a member of staff quoted is a correlation ID (from an API error) or a Next.js digest
(from the error page): search for it as `correlation_id`/`correlationId` or as `digest`.

---
status: accepted
date: 2026-11-17
---

# Background jobs run from a PostgreSQL table, not Redis or Celery

Recorded 2026-11-17 for the decision made in issue 60.

## Context

- Building a schema inside a web request held one of two gunicorn workers and could hit Cloudflare's ~100-second limit, leaving the Platform Admin unsure whether the office was created.
- Redis and Celery would cost memory the micro instance does not have (ADR 0006). PostgreSQL is already there and gives us locking for free.

## Decision

- Slow work runs in a **`Job` table in the public schema** (`kind`, `payload`, `state`, `attempts`, times, a short safe `error`, `correlation_id`).
- One small **`worker` container**, built from the backend image, runs `manage.py run_jobs`. It claims one job at a time with `SELECT … FOR UPDATE SKIP LOCKED`, so two workers can never take the same job, and checks the queue every `JOB_POLL_SECONDS` (default 5).
- A job left running longer than `JOB_TIMEOUT_SECONDS` (default 900), for example because the worker was killed for memory, goes back to the queue if attempts remain, and otherwise fails.
- Job steps are safe to run twice: each checks what is already done.
- A payload holds only ids and plain values, never a password, token or Code.
- The first job kind is **creating a Consultancy**: the Platform answers 202 at once, and the worker builds the schema, runs its migrations and invites the first Admin. A failure drops the half-made schema and keeps the domain switched off, so a half-built office is never reachable.

## Consequences

- The table is general, so other slow work (bulk Email & SMS, large exports, notification emails) can move to it later.
- Throughput is one job at a time. That is enough for our volume; a second worker can be added if needed.

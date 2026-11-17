---
status: accepted
date: 2026-11-17
---

# The first Platform Admin is created from deploy values, with no console step

Recorded 2026-11-17 for the decision made in issue 59.

## Context

- No SSH or `createsuperuser` step on the production server, which has no open port (ADR 0006).
- One repeatable path for the first setup and for recovery.

## Decision

- On every deploy, `ensure_platform_admin` reads `PLATFORM_ADMIN_NAME`, `PLATFORM_ADMIN_EMAIL` and `PLATFORM_ADMIN_PASSWORD` and creates that Platform Admin **only if no Platform Admin has that email yet**. It never changes an existing one.
- It logs one line saying what it did (never the values) and always exits 0, so a missing or bad value never stops a deploy.
- After that, name, email and password are changed only on the Platform's Account page.
- **Locked out** (lost password and phone, or every recovery code): set the three values with a new email, deploy, and log in as the new Platform Admin.

## Consequences

- The values come from Secrets Manager or SSM, like every other secret (ADR 0016).
- Two-step login starts on or off from `PLATFORM_ADMIN_TWO_FACTOR_DEFAULT` (ADR 0009).

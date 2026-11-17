---
status: accepted
date: 2026-11-17
---

# Stored secrets are sealed with their own keys; one-time Codes are only kept as hashes

Recorded 2026-11-17 for decisions made in issues 07, 08, 25, 44 and 61.

## Context

- A database leak or backup alone must not reveal provider passwords, login secrets or Codes.
- Separate keys limit the damage of any one key leaking, and let each be rotated on its own.

## Decision

- Each kind of secret has **its own key from the environment**, never `DJANGO_SECRET_KEY`:
  - `JWT_SIGNING_KEY` signs login tokens (ADR 0003).
  - `PROVIDER_SECRETS_KEY` seals an office's SMS token and email password (Fernet) before they are stored. They are opened only to send and never returned by the API.
  - `TWO_FACTOR_SECRETS_KEY` (a Fernet key) seals TOTP secrets (ADR 0009).
  - `STAFF_DIRECTORY_KEY` keys the HMAC of emails in the Central login directory (ADR 0010).
  - `INTERNAL_API_TOKEN` is the shared secret between Next.js and Django (ADR 0002).
- **One-time Codes** (invites, password resets) are 6 digits, valid 30 minutes, limited attempts, single use and bound to one Consultancy (or the Platform). Only a salted HMAC-SHA256 of the Code is stored, and it is compared in constant time. Recovery codes are stored as hashes too.
- A missing or too-short key stops the app at start-up instead of running insecurely.

## Consequences

- Production keys live in AWS Secrets Manager or SSM Parameter Store and reach the containers as environment values.
- Rotating `PROVIDER_SECRETS_KEY` or `TWO_FACTOR_SECRETS_KEY` needs re-sealing (or re-entering) what was sealed with the old one.

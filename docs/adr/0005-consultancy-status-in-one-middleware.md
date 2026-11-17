---
status: accepted
date: 2026-11-17
---

# Consultancy Status is enforced by one middleware

Recorded 2026-11-17 for a decision built in issue 42.

## Context

- One place to get right. If each view checked the Status, one forgotten check would let a Suspended office keep writing.
- Read-only for unpaid offices lets the client still look up a Student while payment is sorted out, which is kinder and avoids support calls.
- Staff see a banner that explains a restricted Status, so a refused save is never a mystery.

## Decision

Every Consultancy has one Status: **Evaluation, Active, Past due, Suspended or Terminated**. One middleware, run on every request to a Consultancy right after the host is resolved, decides what that Status lets through:

| Status | Reads | Changes | Login |
| --- | --- | --- | --- |
| Active | yes | yes | yes |
| Evaluation | yes, until the evaluation end date (14 days by default, a Platform Admin can extend it) | yes, until then | yes, until then |
| Past due, Suspended | yes (GET, HEAD, OPTIONS) | refused with a short message | yes |
| Terminated | no: the office's domains are switched off and answer 404 | no | no |

Allowed moves: Evaluation → Active; Active ↔ Past due ↔ Suspended; any → Terminated. Terminated is final. Every change needs a reason and is kept in an append-only Consultancy Status Change record. No Status deletes data.

## Consequences

- Every new endpoint is covered automatically; tests check each Status against every endpoint group.
- Some writes must still work in a read-only office (logging in, refreshing a token, logging out). They are listed in the middleware, and the list is tested.
- The Inbox follows the same rule: a Past due or Suspended office can read but not send.

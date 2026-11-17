---
status: accepted
date: 2026-11-17
---

# One permission policy, with four fixed Roles

Recorded 2026-11-17 for decisions made in issues 02, 07 and 11 and kept since.

## Context

- Permission rules scattered over views are how one forgotten check exposes data. One policy can be read, reviewed and tested as a whole.
- Fixed Roles keep the product simple; configurable permissions were one of the old CRM's unused, confusing features.

## Decision

- Staff have one of four **fixed Roles: Admin, Counsellor, Front Desk, Teacher.** Roles are not configurable per Consultancy.
- **One permission policy** answers "can this person do this action on this record?" Every rule lives there: the Assigned Counsellor (or an Admin) edits a Student and others see it view-only; Front Desk edits only unassigned Leads; only Admins see and change money; Teachers see only their own Batches and their Students; Transfers; Offer Letters, the Chosen Offer and visa letters; the Bin; Staff Progress; Work From Home; two-step login; and the Inbox (only its two people, never an Admin or Platform Admin).
- Views stay thin and call the policy; services enforce it again for anything that changes data.
- A refusal is **403 with a short reason** ("not your Student"). A switched-off Module is 404 (ADR 0011).
- A Platform Admin inside an office (ADR 0017) gets every Admin permission plus a few platform-only ones, through the same policy.

## Consequences

- Every rule is tested for every Role.
- A new kind of record must add its rules to the policy, not to its view.

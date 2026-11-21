---
status: accepted
date: 2026-11-17
---

# History is append-only and written by services only

Recorded 2026-11-17 for decisions made in issues 10, 19, 79 and 83.

## Context

- Money, visa results and documents are disputed in real life. The office needs to see who changed what, even after that person has left.
- Writing History in the service that makes the change means it can never be forgotten by a new screen.

## Decision

- Each Student has a **History**: an append-only list of entries written by the service that made the change, never by hand and never by a view. Covered: Stage changes (with what caused an automatic move), Note edits (with the previous text), Payment created, edited or deleted, Transfers, Offer Letters and the Chosen Offer, the Bin, Application and Visa Status changes, Documents checked and Visa dates booked or moved.
- Each entry keeps **who did it, by name as it was at the time**, when, and what changed (before and after). An action by a Platform Admin in Support Access is shown under their own name marked "Platform Admin", never as a Staff member.
- Other records follow the same pattern with their own append-only tables: Consultancy Status changes, Export Records, Work Session changes, Office Network changes, two-step login events.
- The append-only tables refuse updates and deletes at the query-set level.
- Recording that started late is honest: nothing is guessed for the past, and reports say "Counted from <date>".

## Consequences

- The Activity timeline and Staff Progress are built from History, so they need no counters of their own.
- Deactivating Staff never removes their name from past entries.

---
status: accepted
date: 2026-11-17
---

# The Student's Stage is moved by a pure function, from the real work

Recorded 2026-11-17 for decisions made in issues 14, 15, 74 and 75.

## Context

- A Stage typed by hand drifts from the truth. Deriving it from Applications and Visas keeps the pipeline and reports honest.
- A pure function can be unit-tested for every rule without a database.

## Decision

- A Student's journey is **Lead → Counselling → Applying → Offer → Visa**, or **Lost**.
- Staff set **Lead, Counselling and Lost by hand** (Lost needs a reason). **Applying, Offer and Visa follow from the work**:
  - the first Application → Applying
  - the first Offer Letter → Offer
  - a Visa applied for → Visa
  - a Visa Granted → stays at Visa, journey finished ("Visa ✓ Granted")
  - a Visa Refused (no other Visa waiting) → Offer, or Counselling if no Application at Offer is left
  - every Application Rejected → stays in Applying
  - the engine never moves a Lost Student
- The rules are **one pure function** (current Stage plus Applications, Offer Letters and Visas in, new Stage out), called by the services whenever one of those changes. History records each automatic move with its cause.
- The "Next step" shown on Home and on the Student's page is also one shared rule.

## Consequences

- Changing the journey (as on 2026-11-14, when Onboarded and Completed were removed) is one function plus a light data conversion.
- The open edge case (an Application changing while the Student is Lost) is decided in the function: Lost stays Lost.

---
status: accepted
---

# Schema per Consultancy, using django-tenants, resolved by subdomain

Each Consultancy gets its own PostgreSQL schema, managed by `django-tenants`, and is identified by its own subdomain (e.g. `abcedu.<product-domain>`). Platform-level data (the list of Consultancies, their domains, Platform Admins and which Modules each Consultancy has) lives in the shared `public` schema, and Platform Admins work from a separate subdomain. We chose this over shared tables with a `consultancy_id` column because we expect only a handful of Consultancies, the team already knows Django, and keeping each Consultancy in its own schema means one forgotten filter cannot show one client's Students to another.

## Considered Options

- **Shared tables + `consultancy_id` column (optionally enforced with Postgres Row-Level Security)**: cheaper migrations and easy cross-Consultancy reporting, but isolation depends on every query (or RLS policy) being right. Rejected because we value strong isolation more than either of those at our scale.
- **Database per Consultancy**: strongest isolation, but costs the most to run and is the most work to operate for no real gain over schemas at our scale.

## Consequences

- Migrations run once per Consultancy schema, so deploys slow down as the number of Consultancies grows; this becomes painful in the hundreds.
- Reporting across all Consultancies (for the Platform) has to loop over schemas.
- Every Consultancy needs a subdomain and a DNS/TLS setup that covers it (a wildcard certificate).
- Moving to shared tables later would mean rewriting every model and migrating every schema's data, so this is effectively permanent.

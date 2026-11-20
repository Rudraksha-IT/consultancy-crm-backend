# Architecture Decision Records (backend)

Each file records one architecture decision: its context, the decision, and its consequences. This folder holds the decisions that concern the backend. A decision that concerns both apps is kept in both repositories, with the same number and the same text. When you change one copy, change the other copy too.

Numbers are shared by both repositories, so a number missing here is a decision that only concerns the other app. Here, that means 0022, which live in `consultancy-crm-frontend/docs/adr/`.

ADRs 0001 to 0004 were written when the decisions were made. ADRs 0005 to 0022 were recorded on 17 November 2026 for decisions already built; 0023 on, when decided. A later ADR that changes an earlier one says so at the top, and a replaced decision stays here with its status changed.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-schema-per-consultancy-with-django-tenants.md) | One PostgreSQL schema per Consultancy (django-tenants), chosen by subdomain | Accepted |
| [0002](0002-next-server-forwards-the-consultancy-host.md) | The Next.js server calls Django and forwards the host; Django is internal only; no Caddy | Accepted |
| [0003](0003-jwt-login-held-by-the-next-server.md) | JWT login, held by the Next.js server, bound to one Consultancy | Accepted |
| [0004](0004-client-address-from-cloudflare-through-next-server.md) | Client address from Cloudflare, used only to check Office Networks | Accepted |
| [0005](0005-consultancy-status-in-one-middleware.md) | Consultancy Status enforced by one middleware | Accepted |
| [0006](0006-one-micro-ec2-behind-cloudflare-tunnel.md) | Production on one micro EC2 instance behind a Cloudflare Tunnel (images and deploys changed by 0024) | Accepted |
| [0007](0007-json-logs-with-correlation-ids.md) | One JSON log format with correlation IDs | Accepted |
| [0008](0008-background-jobs-in-a-postgres-table.md) | Background jobs from a PostgreSQL table, not Redis or Celery | Accepted |
| [0009](0009-two-step-login-with-totp.md) | Two-step login with TOTP and recovery codes | Accepted |
| [0010](0010-central-login-by-email.md) | Central login emails a person their offices | Accepted |
| [0011](0011-modules-hide-never-delete.md) | Modules switched per Consultancy; switching off hides, never deletes | Accepted |
| [0012](0012-one-permission-policy-fixed-roles.md) | One permission policy with four fixed Roles | Accepted |
| [0013](0013-append-only-history-written-by-services.md) | Append-only History written by services | Accepted |
| [0014](0014-stage-engine-is-a-pure-function.md) | The Stage engine is a pure function | Accepted |
| [0015](0015-private-documents-and-the-bin.md) | Private S3 documents, short signed links, and the Bin | Accepted (storage paths use UUIDs: 0027) |
| [0016](0016-secrets-sealed-and-codes-hashed.md) | Secrets sealed with their own keys; Codes stored as hashes | Accepted |
| [0017](0017-support-access-as-the-platform-admin.md) | Support Access as the Platform Admin, never as Staff | Accepted |
| [0018](0018-platform-admin-from-deploy-values.md) | First Platform Admin created from deploy values | Accepted |
| [0019](0019-edit-details-in-one-transaction-with-a-version.md) | Edit details saved in one transaction with a version check | Accepted |
| [0020](0020-inbox-by-polling.md) | Inbox checks for messages every 30 seconds | Accepted |
| [0021](0021-dates-stored-in-ad.md) | Dates stored in AD; BS for display | Accepted |
| [0023](0023-all-email-through-the-product-smtp.md) | All email through the product's own SMTP account; no SMS | Accepted |
| [0024](0024-safe-deploys-from-github.md) | Deploys keep logins, switch only when healthy, roll back by themselves, run from GitHub with a locked SSH key (no AWS) | Accepted |
| [0027](0027-records-known-only-by-uuid.md) | Records are known outside the server only by a UUID | Accepted |

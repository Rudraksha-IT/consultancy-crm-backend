# Consultancy CRM — backend

The rules and decisions this repository follows (ADRs, logging, design system, glossary) are in [`docs/`](docs/README.md).

Django + Django REST Framework. One PostgreSQL schema per Consultancy via `django-tenants`,
resolved from the request's subdomain (ADR 0001: one schema per Consultancy).

Only the Next.js server calls Django (ADR 0002). It sends the browser's host in
`X-Forwarded-Host` (`USE_X_FORWARDED_HOST = True`) and the shared secret in `X-Internal-Token`.
`tenancy.middleware.InternalTokenMiddleware` runs first and answers 403 to any call without the
right token; Django refuses to start if `INTERNAL_API_TOKEN` is shorter than 32 characters.
`ALLOWED_HOSTS` is `.$PRODUCT_DOMAIN`, never `*`.

## Layout

```
backend
├─ config                 ► settings.py · urls.py (Consultancy hosts) · platform_urls.py (Platform host) · wsgi.py
├─ tenancy                ► models.py · services.py · subdomains.py · middleware.py (internal token + host) · context.py · types.py · exceptions.py · views.py · urls.py
├─ platform_admin         ► models.py · services.py · auth.py · permissions.py · types.py · exceptions.py · serializers.py · views.py · urls.py · management/commands/ensure_platform_admin.py
├─ staff                  ► models.py · services.py · context.py · types.py · exceptions.py · serializers.py · views.py · urls.py
├─ modules                ► services.py · types.py
├─ tests
│  ├─ unit                ► test_passwords.py · test_context.py · test_staff_context.py · test_subdomains.py · test_platform_permissions.py
│  ├─ integration         ► test_internal_routing_integration.py · test_current_consultancy_integration.py · test_auth_integration.py · test_staff_context_integration.py · test_platform_auth_integration.py · test_platform_consultancies_integration.py · test_ensure_platform_admin_command_integration.py · test_platform_account_integration.py
│  └─ isolation           ► test_tenant_isolation.py
└─ (root)                 ► manage.py · pyproject.toml · Dockerfile · .pre-commit-config.yaml
```

## Development

```bash
uv sync
uv run pre-commit install
# Tests use the stack's own Postgres (this repo's compose.yaml, published on localhost:5432 only).
# pytest-django creates a separate `test_crm` database there and drops it at the end.
docker compose up -d postgres

uv run ruff format . && uv run ruff check . && uv run mypy . && uv run pytest
```

Running the server itself (`manage.py runserver`, gunicorn) needs `INTERNAL_API_TOKEN` set;
tests set their own.

## Testing pattern

Tests hit the REST API of a real Consultancy. The `make_consultancy(name, subdomain)` fixture
(`tests/conftest.py`) creates the Consultancy's schema and subdomain and returns an `APIClient`
bound to its host that sends the internal token like the Next.js server does (build others with
`api_client(host, HTTP_X_FORWARDED_HOST=...)`); the test transaction rolls the schema back afterwards. Every endpoint also adds
a case to `tests/isolation/` proving Consultancy A's data never reaches Consultancy B.

`ConsultancyContext.add_staff(role)` creates a Staff member (password `TEST_PASSWORD`) and
`ConsultancyContext.log_in_as(role)` also logs the context's client in as them (it issues tokens and
sets the Bearer header with `use_access_token`); `tokens_for(staff)` returns a `TokenPair`.

The `platform_site` fixture returns a client bound to the Platform host;
`PlatformContext.add_admin()` / `.log_in()` / `.tokens_for(admin)` create (and log in) a Platform Admin.

## Auth endpoints (on a Consultancy subdomain)

Login is JWTs (ADR 0003, `auth_tokens` app); Django sets no cookie and has no CSRF check (the
Next.js server holds the tokens and checks `Origin`).

- `POST /api/auth/login/` `{email, password}` -> `{access, refresh, context}`.
- `POST /api/auth/refresh/` `{refresh}` -> `{access, refresh}`; the old refresh token is refused
  from then on (rotation).
- `POST /api/auth/logout/` `{refresh}` -> 204; that refresh token is refused from then on.
- `GET /api/context/` with `Authorization: Bearer <access>` returns
  `{staff, consultancy, modules, date_display, theme}`; 401 without a valid token.

Every token carries `tenant_schema`, `kind` (`staff` | `platform_admin`), `auth_version`,
`user_id` and `jti`. `auth_tokens.middleware.TokenConsultancyMiddleware` answers 401 for a token
from another schema, without `tenant_schema`, or of the wrong kind for the host;
`BearerTokenAuthentication` also refuses inactive people and an old `auth_version`. Refresh
tokens are recorded in `IssuedRefreshToken`, in the schema they were issued on.
`auth_tokens.services.sign_out_everywhere(person)` ends every login of a person at once.
Access tokens last 15 minutes (`JWT_ACCESS_TOKEN_MINUTES` can shorten it), refresh tokens 7 days.

## Employees and joining (issue 07)

Staff have `status` (`invited` | `active` | `deactivated`); `is_active` is a database column
generated from it, so only active Staff can log in or use a token. Services are in
`staff/employees.py` (Admin actions) and `staff/joining.py`.

- `GET /api/staff/` (Admin) -> `[{id, name, email, mobile, job_title, role, joined_on, status}]`.
- `POST /api/staff/` (Admin) `{name, mobile, email, job_title, role, joined_on}` -> 201, invited,
  with a join Code emailed (link to `/join` on the request's host). Teacher only while Batches is on.
- `PATCH /api/staff/<id>/` (Admin) `{role}`; `POST /api/staff/<id>/deactivate/` (Admin) signs them
  out everywhere. An Admin can't change or deactivate themselves.
- `POST /api/staff/<id>/invite/` (Admin) sends a new Code (429 at the hourly limit);
  `DELETE` cancels the invite (removes the person and their Codes).
- `GET /api/staff/counsellors/` (Admin, Counsellor, Front Desk) -> active Counsellors and Admins.
- Open on the Consultancy host: `POST /api/auth/join/` `{email}` (same answer always),
  `POST /api/auth/join/code/` `{email, code}`, `POST /api/auth/join/complete/`
  `{email, code, password}` -> `{access, refresh, context}`.

## My own Settings (issue 38)

Every Staff member, any Role, and only about themselves:

- `GET /api/me/` -> `{name, mobile, email, job_title, role, joined_on, colour_mode, notifications:
{task_given, task_due_tomorrow, morning_summary}}`.
- `PATCH /api/me/` with any of `name` (capitalised on save), `mobile` (may be empty),
  `colour_mode` (`device` | `light` | `dark`) and `notifications` (any of its three booleans).
  Role, email, job title and joining date are unknown fields here (400).
- `POST /api/me/password/` `{current_password, new_password}` -> `{access, refresh}` for this
  device; every other device is signed out at once. 400 `{current_password: [...]}` or
  `{detail, unmet_rules}`.
- `GET /api/me/devices/` -> `[{signed_in_at, last_used_at, is_current}]`, this device first.
- `POST /api/me/devices/sign-out-others/` (no body) -> `{access, refresh}` for this device.

Every token carries a `device` claim; a refresh keeps it. The Next.js server treats the two token
answers as a "renew" (AUTH_ROUTES): it sends the access token and seals the new pair.

## Office logo (issue 39)

Stored on the Consultancy (public schema, beside the Theme): `logo_key`, `logo_content_type`,
`logo_version` and `show_name_beside_logo`. The file lives in the private Documents bucket under
`<schema>/office-logo/`. It is never linked to: Django reads it back and serves it.

- `GET /api/context/` and `GET /api/consultancy/` carry `logo`: `{url, show_name}` or `null`
  (the initials show). `url` is `/api/consultancy/logo/<version>/`.
- `GET /api/consultancy/logo/<version>/` (public, for the login screens) -> the bytes with their
  own type, `nosniff` and a `sandbox` CSP. Only the current version is found (404 otherwise).
- Admin only, `/api/configuration/logo/`: `POST` a form with one `file` (PNG, JPG or WebP by its
  bytes, or an SVG rebuilt from an allowlist by `tenancy.logo.sanitise_svg`; at most 1 MiB) ->
  `{logo}`; `PATCH {show_name: true|false}` -> `{logo}` (400 "Upload a logo first." without
  one); `DELETE` -> 204. Each upload gets a new version; the old file is removed.

## Catalogue (issue 12)

`catalogue` app (each Consultancy's schema): University (name, country, archived), Course
(university, name, level, archived) and Intake (course, month "YYYY-MM", always AD). Names are
unique case-insensitively (a University per Consultancy, a Course per University). Archived
entries are left out of pickers and stay on old records; only an archived entry no Application
(or, for a University, no Commission) uses can be deleted (issue 92). Every Role but Teacher
reads; only Admins change (`catalogue.permissions`). New Intakes can't be in a month gone by;
Intakes a Course already has may stay. Each Course answer carries `applications` (how many use it).

- `GET /api/catalogue/universities/?q=&include_archived=true|false&page=` -> 25 per page, by name,
  each with its Courses and Intakes. `q` matches the University's name or any Course's. Without
  `include_archived=true`, archived Universities and Courses are left out (what pickers use).
- `GET /api/catalogue/universities/<id>/` -> one University with every Course (archived too).
- `PUT /api/catalogue/universities/<id>/` (the edit page, issue 92) `{name, country, archived,
courses: [{id?, name, level, intakes, archived, documents: [type ids]}], deleted_courses: [ids]}`
  saves everything in one transaction or nothing. Courses left out stay as they are; a row without
  an id is a new Course; an active Course needs an Intake; a deleted Course must already be saved
  as archived and unused. Row errors come back keyed by the row's place: `{courses: {"1": {...}}}`.
- `DELETE /api/catalogue/universities/<id>/` -> 204; removes an archived, unused University with
  its Courses. 400 `{detail}` otherwise.
- `GET /api/catalogue/intakes/` -> `{intakes: ["2027-02", ...]}`: each month from this month on
  that an active Course of an active University offers. A Student's `preferred_intake` must be one
  of them, empty, or the one already stored.
- `POST /api/catalogue/universities/` `{name, country}`; `PATCH /api/catalogue/universities/<id>/`
  `{name?, country?, archived?}`.
- `POST /api/catalogue/universities/<id>/courses/` `{name, level, intakes: ["2027-02", ...],
documents?: [type ids]}`;
  `PATCH /api/catalogue/courses/<id>/` `{name?, level?, intakes?, archived?}`. 1 to 24 Intakes,
  stored once each, oldest first.

### Catalogue import (issue 13)

`POST /api/catalogue/import/` `{csv: "<the file's text>"}` (Admins only; the browser reads the
file and sends its text, at most 500,000 characters) -> 200
`{imported, already_there, failed, problems: [{row, problem}]}`, or 400 `{csv: [reason]}` when the
file as a whole can't be read (empty, not CSV, wrong column names, more than 2,000 rows).

The format, one row per Course:

```csv
university,country,course,level,intakes
Deakin University,Australia,Master of Data Science,Master,"Mar 2027;Jul 2027"
```

- The first row names the five columns, in any order and any case.
- `country`: Australia, UK, Canada, USA, New Zealand, Japan or Other. `level`: Bachelor, Master,
  Diploma, Graduate Certificate or Foundation (any case; the API values work too).
- `intakes`: 1 to 24, separated by semicolons (or commas inside quotes), each "Mar 2027",
  "March 2027" or "2027-03", from 2000 on. Always AD.
- Names are matched whatever their case, so importing a file again adds nothing twice. A Course
  already there is counted as "already there" and left as it is (archived or not; change it on
  the Catalogue page). A row naming a University already there in another country is refused.
- Rows are numbered as a spreadsheet shows them (the column names are row 1). Blank rows are
  skipped. Every problem of a bad row is reported; good rows are still imported, each saved on
  its own. Parsing is the pure `catalogue.csv_import.parse_catalogue_csv`; saving is
  `catalogue.services.import_catalogue`. The log line has counts only, never names.

## Edit details (issue 81)

`PUT /api/students/<id>/details/` saves the whole Edit details page in one transaction, or
nothing. Only the Assigned Counsellor or an Admin (`StudentAction.EDIT_DETAILS`; a Platform Admin
in Support Access counts as an Admin). Body: `version` (the profile's `details_version` when the
page opened), any of the detail fields `PATCH /api/students/<id>/` takes, and the three lists
whole: `academic_records`, `test_scores`, `work_experience` (at most 20 rows each). A row with an
`id` changes that stored row, a row without one is added, and a stored row left out is removed;
an id that isn't one of this Student's rows (or sent twice) is refused. Refusals are per field,
and per row as `{"academic_records": {"1": {"institution": [...]}}}`. If anything on the page was
changed meanwhile (this endpoint, the details `PATCH` or the row endpoints all move
`details_version` on) the answer is 409 and nothing is saved. Nothing is written to History, as
the per-section edits never were. The profile answer carries `details_version` and
`can_edit_details`.

## Activity timeline (issue 82)

`GET /api/students/<id>/` carries `stage_changes`: every `stage_changed` History entry of the
Student, oldest first (`history.services.stage_changes_of`, one query however many), each shaped
exactly like a History entry (`id, kind, at, by {name, is_platform_admin}, before, after,
detail`). The screen works out the time spent in each Stage, the timeline dots and the Lost ⓘ
from these and `created_at`; nothing new is stored. The paged History endpoint still lists the
same entries; the screen shows them once, as dots.

## Who did each action (issue 83)

Recorded from 2026-11-15 on; nothing is filled in for the past. The services add History entries
(who, when, name as it was; a Platform Admin as themselves, never as Staff), shown in Activity:

| Kind                                     | Written by                                                   | before / after / detail                                                  |
| ---------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------ |
| `application_status_changed`             | `change_application_status` (by hand)                        | Statuses / University                                                    |
| `visa_status_changed`                    | `change_visa_status` (by hand or a visa letter)              | Statuses / University                                                    |
| `document_verified`, `document_rejected` | `check_document`                                             | after: type; detail: reason                                              |
| `visa_date_changed`                      | `change_visa_dates`, one per appointment that really changed | UTC ISO moments, empty for none / `biometrics`, `medical` or `interview` |
| `task_done`                              | `set_task_done`, only for a Task about a Student             | after: title                                                             |

A Task also keeps `done_by` (None for a Platform Admin) and `done_by_name` (marked "(Platform
Admin)"); opening it again clears them and `done_at`, and the History entry stays. An Offer
Letter already has its own entry (`offer_letter_added`), and Applications and Visas keep
`created_by`, so neither is recorded twice. Not in the Task answer; Staff Progress (issue 84)
reads them.

## Staff Progress (issue 84)

`staff_progress` app (no models; always on, part of Staff). `GET /api/staff/<id>/progress/?from=
&to=` (both required, "YYYY-MM-DD", at most one year, nothing else accepted; GET only). An Admin
(or a Platform Admin in Support Access) may read anyone in the office, everyone else only
themselves (403 "You can only see your own progress."); an unknown id is 404 for an Admin.

The counting lives in one place that issue 85 reuses: `staff_progress.counting` (pure: which
cards each Role gets, "Counted from", the one-year rule, Stage moves) and
`staff_progress.services.count_progress(staff_ids, start, end)` (grouped queries, one per
source). Only records linked to a Staff member count, so a Platform Admin's count for nobody.

| Card                                     | Counted from                                                             |
| ---------------------------------------- | ------------------------------------------------------------------------ |
| `moved_forward`, `students_lost`         | their `stage_changed` History (forward along the journey / to Lost)      |
| `applications_made`, `visas_applied_for` | Applications and Visas they added (`created_by`)                         |
| `tasks_done`                             | Tasks they marked done (`done_by`); "Counted from" 2026-11-15 (issue 83) |
| `visitors_registered`, `new_leads`       | Visits they logged / those of kind New                                   |
| `payments_taken`                         | Payments they recorded (`recorded_at`), with the amount                  |
| `classes_taken`, `attendance_marked`     | class days they marked / the marks (Batches on only)                     |

Cards: Counsellor 5, Front Desk 3, Teacher 2 (none while Batches is off); an Admin gets the
Counsellor and Front Desk cards, plus the Teacher cards when they teach a class. The day-by-day
list is their History entries plus Visits, Applications and Visas added and class days marked
(`visit_logged`, `application_made`, `visa_applied_for`, `attendance_marked`), grouped by the
office's day, newest first, at most `MAX_ENTRIES` (500) with `more`. "Only me" Tasks are counted
but never listed.

## Reports → Staff Progress (issue 85)

`GET /api/reports/staff-progress/?from=&to=` replaces `/api/reports/counsellors/` ("Counsellor
performance"). Admin only, Reports Module on (404 otherwise), GET only, the same period rules as
the Progress tab (at most one year). It calls `count_progress` for everyone at once, so every
number matches that person's Progress tab for the same period.

- `counsellors`: Admins and Counsellors, and anyone who still has Students assigned. Columns
  `students` (assigned now), the five Counsellor cards, and `visas_granted` (their Students now
  with a Visa granted in the period; "Completed" before issue 75).
- `front_desk`: Front Desk, with the three Front Desk cards.
- `teachers`: Teachers, with the two Teacher cards; always `[]` while Batches is off.
- `counted_from`: `{count: "YYYY-MM-DD"}` for counts whose recording began after the period's
  start (today only `tasks_done`).

Invited people are never listed; someone who has left is listed only while they have Students or
something counted in the period. Rows are by name.

## Work From Home (issue 87)

Part of the Clock-in Module (404 while it is off). Admins clock in and out from any network;
other Staff only from an Office Network or on Approved Work From Home dates
(`clock_in.services.anywhere_today`, decided by Role and dates, never by the address; ADR 0004
note). `GET /api/clock/` answers `anywhere`: `admin`, `work_from_home` or null. A session
clocked in on such dates is stored with `work_from_home`, and its day answers `home: true`.

- `GET /api/clock/work-from-home/`: my dates (`can_ask` false for an Admin);
  `POST` `{starts_on, ends_on|null, reason ≤ 200}` asks (start from today, within a year, end on
  or after start); every active Admin gets a `work_from_home_asked` notification.
- `POST /api/clock/work-from-home/<id>/cancel/`: the asker takes back a Waiting request.
- `POST .../approve/`, `.../decline/` (Admins, Waiting only, else 409) tell the person;
  `.../end/` (Approved, not over): started dates end today, unstarted ones become Ended.
- `GET`/`POST /api/clock/staff/<id>/work-from-home/` (Admins): a person's dates, or set dates
  directly (Approved at once, `work_from_home_set` notification); never for an Admin.
- `GET /api/home/waiting/` carries `work_from_home_requests: {count, first_staff_id}` for Admins.
- While Clock-in is off, Work From Home notifications are hidden from the bell (kept).

## Inbox (issue 86)

`inbox` app (each Consultancy's schema): `InboxMessage` (sender, recipient, text of 1 to 2,000
characters, sent_at, read_at). Always on, for every Role. Only the two people ever see a
message: every query is filtered by the person asking, and a Platform Admin in Support Access is
refused every Inbox endpoint (403). Messages are never edited or deleted, are left out of the
office data export, and their text is never logged (only ids).

- `GET /api/inbox/`: my conversations, newest first: the other person (`has_left` when
  deactivated), the start of the last message (120 characters), and `unread`.
- `GET /api/inbox/check/`: `{unread, latest}`, the conversations with unread messages and the
  newest message's id. The frontend asks this every 30 seconds while the tab is shown; it is one
  query on an index of unread messages.
- `GET /api/inbox/people/`: everyone active but me, by name, for "New message".
- `GET /api/inbox/<staff id>/`: the newest 200 messages with that person, oldest first (`more`
  when older ones exist), and `can_write`. Reading marks their messages to me as read, which is
  why it is a GET: Past due and Suspended offices can still read.
- `POST /api/inbox/<staff id>/` `{"text"}`: send. 404 for myself, an unknown id or someone
  invited but not joined; 403 when they have left. Past due and Suspended offices get 403 from the
  Consultancy Status middleware.

## Documents (issue 16)

`documents` app (each Consultancy's schema): DocumentType (name, unique in any case; needed by
everyone or asked for only when needed) and Document (Student, type, shown file name, content
type, size, storage key, Verification: pending/verified/rejected, uploaded by/at). A new office
starts with eight types needed by everyone (migration 0002).

Files are kept in private S3 (MinIO locally) behind `documents.storage` (`DOCUMENT_STORAGE`
names the class; tests use `tests.fakes.FakeDocumentStorage`). Keys are
`<schema>/documents/<student id>/<random>.<ext>`: one prefix per Consultancy, never the file's
name. A file is reached only through a signed link that works for 5 minutes, issued after a
permission check. An upload is checked from its bytes (PDF, JPG or PNG; 1 byte to 10 MB); the
shown name loses folders and unsafe characters and always ends in the real extension.

- `GET /api/students/<id>/documents/` -> the checklist `{done, total, can_change, items: [{
document_type: {id, name}, reason, is_required, state, document}]}`. `state` is
  `not_received`, `waiting`, `verified` or `rejected` from the newest Document of that type;
  `done` counts required types whose newest Document isn't Rejected. Every Role but Teacher.
  Each `document` has `files: [{id, file_name, content_type, size, uploaded_at, uploaded_by}]`
  (one or more, oldest first; issue 78) and one `verification` for them all.
- `POST /api/students/<id>/documents/` as `multipart/form-data` `{document_type, file}` -> 201
  checklist; `file` is sent once per file (1 to 10, each at most 10 MB, at most 20 MB together)
  and they become one Document. The Student's counsellor or an Admin only; JSON is 415; 503 if
  storage fails (nothing is kept).
- `POST /api/students/<id>/documents/<doc>/files/` as `multipart/form-data` `{file}` (same file
  rules) -> 201 checklist: adds files to the newest copy of a Document; a Verified one becomes
  Pending. Refused (400) for a Rejected one, an older copy, or more than 20 files in one Document.
- `GET /api/students/<id>/documents/<doc>/files/<file>/link/?action=open|download` ->
  `{url, expires_in}` for that one file.
- `GET /api/documents/students/?scope=mine|everyone&page=` -> the Documents page: Students (not
  Leads or Lost) by attention (rejected x3, waiting x2, missing x1), 25 a page, with `totals` over
  the whole list. Counsellors and Admins only.
- `GET|POST /api/configuration/document-types/` `{name, needed_by_everyone, usual_group?}`
  (`usual_group`: `applying` (default), `visa` or `statements`), `PATCH
/api/configuration/document-types/<id>/` `{needed_by_everyone}` and `DELETE` (only a type not
  needed by everyone, asked for nowhere and with no files; else 400): Admins only. Answers carry
  `usual_group` and `used_by: {countries, courses, students, documents}`.

### Document Requirements (issues 40, 80)

DocumentRequirement: a Document Type asked for by exactly one Country's list (with a Document
Group: `applying`, `visa` or `statements`), Course or Student (DB check; unique per type and
target). University lists were removed (documents 0008 deleted their rows). Each Document Type has
a `usual_group`, used for everyone's types, a Course's and a Student's. The checklist is one pure
function (`documents.checklist.required_types` + `build_checklist`): types needed by everyone;
then, before any Application (Rejected ones don't count), the preferred country's whole list;
while applying, the Applying list of every Country applied to; with a Chosen Offer (at Offer),
the Visa and Statements lists of that offer's Country only; then each Course's types ("For
<Course>"), then the Student's own ("Added for this Student", `added_for_student: true`). Country
rows say "For <Country>". A type asked twice is listed once with its first reason and group; an
uploaded Document no longer asked for stays as an item with `is_required: false` and `group:
null` ("Other documents"). Items carry `group`. The profile, Documents page and Homes all use it
(`checklist_of` / `checklists_of`, a fixed number of queries).

- `GET /api/configuration/document-lists/` -> `{results: [{country, documents: [{document_type:
{id, name}, group}]}]}` for every Country but Other (empty lists too), oldest type first.
- `PUT /api/configuration/document-lists/<country>/` `{documents: [{document_type, group}]}` ->
  that Country's list. Rows of types already there are kept (moved to the new group). An unknown
  Country or `other` is 404. Admins only.
- `PUT /api/catalogue/courses/<id>/documents/` `{document_types: [ids]}` -> the Course. Admins
  only. Course answers carry `documents: [{id, name}]`; University answers no longer do.
- `PUT /api/students/<id>/documents/asked/` `{document_types: [ids]}` and `DELETE
.../asked/<type id>/` ("Not needed") -> the checklist, which now has `asked: [ids]`. The
  Student's counsellor or an Admin; taking off a type that has a file is refused (400), a type not
  asked for this Student is 404.
- Each list: whole-number ids of this office's types, each once, at most 100, none needed by
  everyone ("<name> is already needed by every Student."); a Country row's group is one of the
  three ("Choose Applying, Visa or Statements.").
- `GET|POST /api/documents/types/` `{name}` (at most 80 characters, trimmed): the document
  pickers' list, and adding a name not on it as asked for only when needed. Counsellors and
  Admins. A name already there (any case) is refused.
- `python manage.py create_documents_bucket` creates the bucket if it is missing (local MinIO;
  Compose runs it at start).

## Platform (on the Platform subdomain only)

`tenancy.middleware.ConsultancyHostMiddleware` resolves each host: a switched-on host
(`Domain.is_active`) of a Consultancy that isn't Terminated activates its schema;
`$PLATFORM_SUBDOMAIN.$PRODUCT_DOMAIN` (default `admin`) stays on the public schema and is routed to
`config/platform_urls.py`; `$CENTRAL_LOGIN_SUBDOMAIN.$PRODUCT_DOMAIN` (default `login`) likewise to
`config/central_login_urls.py`; anything else is 404.

### Creating a Consultancy in a background job (issue 60)

`POST /api/platform/consultancies/` validates, reserves the name and subdomain (`setup_state`
`setting_up`, host switched off, no schema) and queues a `create_consultancy` Job, answering 202.
The `worker` container (`python manage.py run_jobs`) takes one Job at a time from the `jobs_job`
table (public schema, `SELECT … FOR UPDATE SKIP LOCKED`): it builds and migrates the schema,
records the starting Status (it invites nobody), then marks the office `ready` and switches its host on. A failure drops the half-made
schema, keeps the host off and marks it `failed`; `POST .../<id>/retry-setup/` (Try again) queues
a new Job. Once ready, a Platform Admin adds Admins with `POST .../<id>/admins/` `{name, email}`
(issue 64): each is added as invited and emailed a Code, as Employees does. A Job running longer than `JOB_TIMEOUT_SECONDS` is queued again (3 attempts in all).
Until an office is ready its host answers 404, and deploy migrations, `rebuild_staff_directory`,
the Central login, Status, Features, Colours and Support Access all leave it out or refuse it.
`python manage.py run_jobs --until-empty` runs what is queued, then stops.

### Central login (issue 44)

`central_login` (public schema): `StaffDirectoryEntry(email_hash, consultancy)` holds an
HMAC-SHA256 of each active or invited Staff email, keyed with `STAFF_DIRECTORY_KEY`, never a raw
email. The Staff services keep it in step (`create_staff`, `invite_employee`, `cancel_invite`,
`deactivate_employee`, `change_email`); `rebuild_staff_directory` builds it again from every schema
(run by the migrate step). `POST /api/central-login/ {email}` answers 202 with the same message
for every valid email; the lookup and the email happen after the answer is sent
(`AnswerThenRun.close`). At most 3 emails per email hash per hour (`CentralLoginRequest`); Next.js
limits each browser address. Terminated offices are never listed.

### Consultancy Status (issue 42)

`Consultancy.status` is Evaluation, Active, Past due, Suspended or Terminated. The rules are pure
and in one place, `tenancy/status.py`; changes go only through `tenancy.services`
(`start_evaluation`, `start_active`, `extend_evaluation`, `activate`, `mark_past_due`, `suspend`,
`terminate`, or `change_status` to pick one). Refused moves raise `InvalidStatusChangeError` with a
short message. Allowed: Evaluation -> Active; Active, Past due and Suspended between each other
(Active -> Active renews the paid-until date); anything -> Terminated, which is final and switches
off every Domain. A reason is required for Past due, Suspended and Terminated (at most 500
characters); dates must be after today and within 10 years. Every change writes an append-only
`ConsultancyStatusChange` (who, from, to, reason, dates, when) in the public schema.

`tenancy.middleware.ConsultancyAccessMiddleware` runs after the token check on every request to a
Consultancy: Active and a running Evaluation let everything through; Past due and Suspended let
GET/HEAD/OPTIONS through plus `POST /api/auth/login|refresh|logout/`, and refuse everything else
with 403 `{"detail": "<why>", "status": "<status>"}`; an ended Evaluation lets through only
`GET /api/consultancy/...` (the login screen) and logout. `GET /api/context/` (under
`consultancy`) and `GET /api/consultancy/` carry `status` and `status_message` (null unless Staff
are limited); the context also has `evaluation_ends_at`. Support Access (issue 27) must be let
through this middleware.

Platform Admins (`platform_admin.PlatformAdmin`) live in the public schema and are not Staff.
Their tokens carry `tenant_schema: "public"` and `kind: "platform_admin"`, so they only work on
the Platform host; their refresh tokens are recorded in the public schema.

- `POST /api/platform/auth/login/` `{email, password}` -> `{access, refresh, platform_admin}`;
  `POST /api/platform/auth/refresh/` and `POST /api/platform/auth/logout/` as on Consultancy hosts;
  `GET /api/platform/me/` -> `{platform_admin: {id, name, email}}`.
- `GET /api/platform/consultancies/` -> `[{id, name, subdomain, host, status, status_reason,
evaluation_ends_at, paid_until, modules, theme}]`.
- `POST /api/platform/consultancies/` `{name, subdomain, admin_name, admin_email, admin_password,
theme?, status?: "evaluation"|"active", paid_until?}` creates the schema, the domain and the first
  Admin Staff member (201) in Evaluation for 14 days, or Active when `paid_until` is given; 400
  with field errors otherwise. Unknown fields are refused.
- `POST /api/platform/consultancies/<id>/status/` `{status, reason?, paid_until?,
evaluation_ends_at?}` (dates `YYYY-MM-DD`; `evaluation` extends it to the end of that day, in
  Asia/Kathmandu) -> the updated Consultancy, or 400 `{"detail": ...}` for a refused move.

- `GET /api/platform/me/` -> `{platform_admin: {id, name, email}}`.
- `PATCH /api/platform/me/` `{name?, email?, current_password}` -> `{access, refresh,
platform_admin}` (the Next.js server keeps the tokens). A new email signs every other session
  out. 400 with field errors, or 429 after 5 wrong current passwords in 15 minutes.
- `POST /api/platform/me/password/` `{current_password, new_password}` -> `{access, refresh}`;
  every other session is signed out. 400 `{detail, unmet_rules}` for a weak password,
  `{new_password}` for the current one, `{current_password}` for a wrong one; 429 as above.

Platform Admins are created only by `manage.py ensure_platform_admin` (issue 59), run by the
`migrate` step's `manage.py migrate_on_deploy` after `migrate_schemas --shared` and before
`migrate_schemas --tenant`. It reads
`PLATFORM_ADMIN_NAME`, `PLATFORM_ADMIN_EMAIL` and `PLATFORM_ADMIN_PASSWORD`, creates a Platform
Admin only if none has that email (ignoring case), never changes one, logs one outcome and
exits 0. Every creation or change goes through `platform_admin.services.save_platform_admin`
and is recorded in `PlatformAdminChange` (which fields, by whom, when; never a password).

## Commits

Every commit message follows [Conventional Commits](https://www.conventionalcommits.org):

```
type(scope): summary in small letters, no full stop, first line at most 72 characters

Optional body after a blank line: what changed and why.
```

`type` is one of `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`,
`chore` or `revert`; add `!` after it for a breaking change (`feat(clock)!: ...`). Examples:
`feat(clock): add weekly days off`, `fix(holidays): keep a range inside one year`.

No commit may credit an AI tool as co-author (`Co-authored-by: Claude ...`, `... Copilot ...`,
`Generated with ...`). A human co-author is fine.

`scripts/check_commit_message.py` enforces both rules as the `commit-msg` hook. Install the
hooks once after cloning: `uv run pre-commit install`. The `commit-messages` job in
`.github/workflows/ci.yml` checks every pushed commit again, so skipping the hook doesn't help;
`main` and `dev` take changes only through pull requests, with `commit-messages`, `checks`
and `pr-title` required (the branch ruleset). Branch from `dev`; merge into `dev` with **Squash
and merge**, so the pull request title becomes the commit (`.github/workflows/pr-title.yml` checks
it by the same rules). Release with a pull request from `dev` into `main`, merged with **Create a
merge commit**.

**Checks on your machine.** Every commit runs ruff format, ruff check and mypy; every push runs
`scripts/check-all.sh` (format, lint, types and missing migrations). Tests run on CI for every
pull request; run them here with `uv run pytest -n auto`. Install the hooks once:
`uv run pre-commit install`. Never skip them with `--no-verify`.

## Run the whole app

The app is two repositories: **consultancy-crm-backend** (Django) and **consultancy-crm-frontend**
(Next.js). Clone both side by side in one folder. Each has the same `compose.yaml`, which builds
its own code and the other repository from the sibling folder, so either can start everything:
Next.js, Django, PostgreSQL, MinIO and MailHog. There is no reverse proxy (ADR 0002).

```bash
mkdir consultancy-crm && cd consultancy-crm
git clone <org>/consultancy-crm-backend.git
git clone <org>/consultancy-crm-frontend.git
cd consultancy-crm-backend   # or consultancy-crm-frontend
docker compose up -d --build
```

Then open <http://acme.localhost:8080> (the demo office, ready after about a minute; see
"Demo office" below) or the Platform at <http://admin.localhost:8080/platform>.

### How a request flows

```
browser ── acme.localhost:8080 ──► Next.js (frontend, the only published port)
                                        │ pages: rendered here
                                        │ /api/...: src/app/api/[...path]/route.ts
                                        ▼
                          http://backend:8000/api/... (private Compose network only)
                          X-Forwarded-Host: acme.localhost:8080  ◄ set by Next.js, never by the browser
                          X-Internal-Token: $INTERNAL_API_TOKEN      ◄ Django refuses any call without it
                          X-Correlation-ID: <new UUID per call>
                                        ▼
                          Django resolves the Consultancy (schema) from the forwarded host
```

- The browser only ever talks to Next.js. Django publishes no port, so `curl localhost:8000`
  from this machine fails; inside the network it still answers 403 without the internal token.
- Next.js drops any `X-Forwarded-*`, `X-Internal-Token`, `Host`, `Authorization`, cookie or
  hop-by-hop header the browser sends, refuses bodies over 1 MiB (413) and calls Django takes
  over 15 s to answer (504).
- Login (ADR 0003): Django answers a login with JWTs; Next.js keeps them in one sealed (AES-GCM),
  HTTP-only, `SameSite=Lax`, host-only cookie for `/api`, sends `Authorization: Bearer <access>`
  to Django, refreshes an expired access token once and retries, and refuses any POST, PUT, PATCH
  or DELETE whose `Origin` isn't the page's own host. Browser JavaScript never sees a token.
- The services refuse to start without `INTERNAL_API_TOKEN` (both, 32+ characters),
  `JWT_SIGNING_KEY` (Django, 32+ bytes) and `AUTH_COOKIE_KEY` (Next.js, 32 bytes base64).
  No env file needs to be shared or copied: compose reads the safe local defaults in
  `sample.env.backend` and `sample.env.frontend`, then an optional `.env.backend` or
  `.env.frontend` beside `compose.yaml` (never committed) for any value you want to change. The
  examples hold placeholders only, never a real secret; production sets its own values.

A one-off `migrate` container runs the migrations (and creates the local MinIO bucket), then
exits; the backend starts only after it succeeds. The app is served on port 8080.

### Subdomains

Consultancies live at `<subdomain>.localhost`. Browsers (Chrome, Firefox) and
systemd-resolved send every `*.localhost` name to `127.0.0.1`, so no `/etc/hosts` edits are
needed. With other resolvers (e.g. macOS + `curl`), add a line per subdomain to `/etc/hosts`:
`127.0.0.1 acme.localhost`.

### Demo office (ready on the first start)

Nothing to create by hand: on a fresh stack the one-off `seed` service runs
`python manage.py seed_demo`, which creates the **Acme Education Consultancy** office through the
Platform's own setup (the worker builds its schema), then fills it with Staff, Students,
Applications, Visas, Documents, payments, tasks and two weeks of Clock-in, dated relative to today.
It takes about a minute after the app is up; `docker compose logs -f seed` shows it working. Once
the office is there the service exits at once, so later starts keep your changes. For a fresh
demo again: `docker compose down -v && docker compose up -d`.

Log in at <http://acme.localhost:8080> with password `Demo-Acme-2026!` as any of:

| Email                  | Role                       |
| ---------------------- | -------------------------- |
| `admin@acme.com`       | Admin (Ramesh Bhandari)    |
| `counsellor@acme.com`  | Counsellor (Sita Karki)    |
| `counsellor2@acme.com` | Counsellor (Bikash Thapa)  |
| `frontdesk@acme.com`   | Front Desk (Anita Gurung)  |
| `teacher@acme.com`     | Teacher (Prakash Shrestha) |

It only runs when `SEED_DEMO_DATA=true` (set in `sample.env.backend`; put
`SEED_DEMO_DATA=false` in `.env.backend` to start empty). Production never sets it and has no
`seed` service. Clock-in works in the demo because `DEMO_OFFICE_NETWORK` (backend) and
`DEV_CLIENT_IP` (frontend) hold the same public address. The demo itself is `demo/acme_seed.py`
in the backend repo: a plain script kept outside ruff and mypy; the command around it is tested.

### Platform and dev Consultancies

Platform Admins work on `admin.localhost` (override the subdomain with
`PLATFORM_SUBDOMAIN`). There is no sign-up and no command to type: the `migrate` step runs
`ensure_platform_admin`, which creates the Platform Admin from `PLATFORM_ADMIN_NAME`,
`PLATFORM_ADMIN_EMAIL` and `PLATFORM_ADMIN_PASSWORD` in `.env.backend` if no Platform Admin has
that email yet (issue 59). With the sample values, log in as `admin@platform.com` /
`Local-Platform-2026!`, then change your name, email or password on the Platform's **Account**
page.

Then open <http://admin.localhost:8080/platform>, log in, and use **New Consultancy** to
create a Consultancy (name, the front part of its address, and its starting Status: Evaluation
for 14 days, or Active with a paid-until date). The rest of the address is `PRODUCT_DOMAIN` from
the backend's environment. Once it is set up, use **Add admin** on its row to invite its Admins by
Code; they log in at `http://<subdomain>.localhost:8080/`. **Change status** moves it between Evaluation, Active,
Past due, Suspended and Terminated (issue 42). Past due and Suspended are read-only for its Staff;
Terminated is final and switches off its addresses (they answer 404). No data is ever deleted.

### Central login (issue 44)

Staff who don't remember their office's address open <http://login.localhost:8080/> (override
the subdomain with `CENTRAL_LOGIN_SUBDOMAIN` in both env files), type their email, and get an email
(MailHog locally) with a link to each office they belong to; the link fills in their email. The
page answers the same for every email. Django keeps an index of keyed hashes of Staff emails
(`STAFF_DIRECTORY_KEY`, 32+ characters, never `DJANGO_SECRET_KEY`); the `migrate` container
rebuilds it with `python manage.py rebuild_staff_directory`.

A subdomain with no Consultancy, or a Terminated one, shows a "couldn't find this Consultancy" message.

### Other services

- MinIO (S3 stand-in for Documents): API on `:9000`, console on <http://localhost:9001>
  (`crm-minio` / `crm-minio-secret` by default). MinIO stopped publishing images to Docker Hub,
  so the stack uses Chainguard's `chainguard/minio` build.
- MailHog catches every email Django sends (Codes for password resets and invites): open
  <http://localhost:8025>. Nothing leaves this machine.
- Stop with `docker compose down`; add `-v` to also delete the database and MinIO volumes.

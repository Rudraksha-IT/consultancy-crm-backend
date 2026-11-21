<!--
Write for a reviewer reading this BEFORE the diff. Delete any heading that doesn't apply; keep it
proportional to the change.
Title: type(scope): summary, e.g. feat(clock): add weekly days off (checked by pr-title).
Branch from dev, merge into dev with "Squash and merge". Release dev -> main with a merge commit.
-->

#### What task is this?

<!-- The ticket or request, and the problem it solves for the office. -->

#### What changed?

<!-- The changes, as a short list: models, services, endpoints, permissions, commands. -->

-

#### How to verify it?

<!-- What you ran and what the reviewer should try (an example request/response helps). -->

#### Reviewer notes

<!-- Where to look first, key decisions, anything you're unsure about. -->

#### Checklist

**Tests**

- [ ] Tests written first, then the code (red, then green)
- [ ] New or changed test files listed here:
  -
- [ ] Bad input is refused (400/403/404/409), with tests
- [ ] One Consultancy can never reach another's data (`tests/isolation/`)
- [ ] Ran the affected test files: `uv run pytest <files>`

**Standards**

- [ ] `uv run ruff format --check .` and `uv run ruff check .` pass
- [ ] `uv run mypy .` passes (strict)
- [ ] Model changes ship their migration (`makemigrations --check` passes); migrations stay light
- [ ] Logs use the standard JSON format; no personal data, secrets or client address in logs
- [ ] Words on screen and in code follow the glossary (Consultancy, Staff, Student, ...)

**Safety**

- [ ] No secrets, keys or real `.env` values committed (`sample.env.*` holds placeholders only)
- [ ] No real client name (use ACME) and no real people's names or emails
- [ ] Fits the shared server's budget (memory, image size, no heavy new dependency)
- [ ] Backend first: when this needs a change in the other app, the backend change is merged and
      deployed before the frontend one (ADR 0024)
- [ ] Migrations work with the code that is live now (add first, remove in a later deploy)
- [ ] README or settings samples updated if behaviour or a setting changed

#### Related to / Depends on

<!-- Optional. The frontend PR that goes with this one, out-of-diff steps (new env value,
     ruleset change, data fix after deploy). -->

#### How does this PR make you feel? [:link:](https://giphy.com/)

<!-- Optional, just for fun. -->

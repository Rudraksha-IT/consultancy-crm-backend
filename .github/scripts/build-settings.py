#!/usr/bin/env python3
r"""Build the settings bundle for the server from GitHub's secrets and variables (ticket 15).

    SECRETS_JSON='${{ toJSON(secrets) }}' VARS_JSON='${{ toJSON(vars) }}' \
        python3 .github/scripts/build-settings.py .github/settings-map.txt > bundle

Every name in the map is looked up as an environment secret (KIND secret) or variable (KIND var)
of `production-settings`. The bundle (see deploy/settings.sh) goes to standard output; problems
and a summary go to standard error, naming settings but never showing a value. It refuses:
a required name that is missing or empty; a value with a line break, a NUL, another control
character or a single quote, or longer than 4096 bytes; a secret saved as a variable (it would be
readable) or a variable saved as a secret; a name that isn't in the map. The SSH route's own
secrets (DEPLOY_*, CF_ACCESS_*) and GitHub's token are not settings and are left out.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

FILES = (".env", ".env.postgres", ".env.backend", ".env.frontend")
NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
MAX_VALUE_BYTES = 4096
MAP_COLUMNS = 5
FIRST_PRINTABLE = 0x20
DELETE = 0x7F
ARGUMENTS = 2
ROUTE = frozenset(
    {
        "DEPLOY_SSH_KEY",
        "DEPLOY_KNOWN_HOSTS",
        "DEPLOY_HOST",
        "DEPLOY_PORT",
        "CF_ACCESS_CLIENT_ID",
        "CF_ACCESS_CLIENT_SECRET",
        "GITHUB_TOKEN",
    }
)


@dataclass(frozen=True)
class Entry:
    """One line of the map: a setting, its files, secret or variable, required or not."""

    name: str
    files: tuple[str, ...]
    kind: str
    need: str


def read_map(path: str) -> list[Entry]:
    """Return the map's settings; stop on a line that isn't five well-formed columns."""
    entries = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        files = tuple(words[1].split(",")) if len(words) == MAP_COLUMNS else ()
        if (
            len(words) != MAP_COLUMNS
            or not NAME.fullmatch(words[0])
            or any(file not in FILES for file in files)
            or words[2] not in ("secret", "var")
        ):
            raise SystemExit(f"settings-map.txt has a bad line: {words[0]}")
        entries.append(Entry(words[0], files, words[2], words[3]))
    return entries


def problem_with(value: str) -> str | None:
    """Why the server would refuse `value`, or None."""
    if any(ord(c) < FIRST_PRINTABLE or ord(c) == DELETE for c in value):
        return "has a line break or another control character"
    if "'" in value:
        return "has a single quote, which isn't allowed"
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        return "is longer than 4096 bytes"
    return None


def misplaced(
    entries: list[Entry], secrets: dict[str, str], variables: dict[str, str]
) -> list[str]:
    """Names GitHub holds that aren't settings, or are kept as the wrong kind."""
    known = {entry.name: entry for entry in entries}
    problems = []
    for name in sorted((set(secrets) | set(variables)) - ROUTE):
        entry = known.get(name)
        if entry is None:
            problems.append(f"{name} isn't a setting (not in settings-map.txt); remove or map it")
        elif entry.kind == "secret" and name in variables:
            problems.append(f"{name} is saved as a variable but must be a secret (variables show)")
        elif entry.kind == "var" and name in secrets:
            problems.append(f"{name} is saved as a secret but is a variable in settings-map.txt")
    return problems


def value_problem(entry: Entry, value: object) -> str | None:
    """Why one setting's value can't be sent; None when it can (or is an empty optional one)."""
    if not isinstance(value, str):
        return f"{entry.name} isn't text"
    if value == "":
        if entry.need == "required":
            return f"{entry.name} is missing (add it as an environment {entry.kind})"
        return None
    problem = problem_with(value)
    return f"{entry.name} {problem}" if problem else None


def chosen_values(
    entries: list[Entry], secrets: dict[str, str], variables: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Each setting's value (empty optional ones left out), and what is wrong with the rest."""
    values: dict[str, str] = {}
    problems = []
    for entry in entries:
        value = (secrets if entry.kind == "secret" else variables).get(entry.name, "")
        problem = value_problem(entry, value)
        if problem:
            problems.append(problem)
        elif value:
            values[entry.name] = value
    return values, problems


def bundle(entries: list[Entry], values: dict[str, str]) -> str:
    """The text settings.sh reads: every file once, with its names, then END."""
    out = ["CRM-SETTINGS 1"]
    for file in FILES:
        out.append(f"FILE {file}")
        out.extend(
            f"{e.name}={values[e.name]}" for e in entries if file in e.files and e.name in values
        )
    out.append("END")
    return "\n".join(out) + "\n"


def upper_keys(text: str | None) -> dict[str, str]:
    """GitHub's toJSON(...) object with upper-case names (its own token is `github_token`)."""
    loaded = json.loads(text or "{}")
    return {str(key).upper(): value for key, value in loaded.items()}


def main() -> int:
    """Print the bundle, or the problems (names only) and fail."""
    if len(sys.argv) != ARGUMENTS:
        print("usage: build-settings.py <settings-map.txt>", file=sys.stderr)
        return 2
    entries = read_map(sys.argv[1])
    secrets = upper_keys(os.environ.get("SECRETS_JSON"))
    variables = upper_keys(os.environ.get("VARS_JSON"))
    values, problems = chosen_values(entries, secrets, variables)
    problems = misplaced(entries, secrets, variables) + problems
    if problems:
        print("The settings can't be sent:", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    sys.stdout.write(bundle(entries, values))
    print(
        f"Built {len(values)} settings for {len(FILES)} files (values not shown).", file=sys.stderr
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

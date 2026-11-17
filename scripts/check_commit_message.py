"""Refuse a commit message that breaks the team's rules.

1. Conventional Commits: ``type(scope)!: summary``. The summary starts with a small letter, has
   no full stop, the header is at most 72 characters, and a blank line comes before any body.
2. No line may credit an AI as co-author ("Co-authored-by: Claude ...", "Generated with ...").
   A human co-author is fine.

Used as the commit-msg hook (pre-commit) and in CI over every pushed commit:
``python scripts/check_commit_message.py <file holding the message>``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TYPES = "feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert"
HEADER = re.compile(rf"^(?:{TYPES})(?:\([a-z0-9][a-z0-9._/-]*\))?!?: (?P<summary>\S.*)$")
GIT_MADE = re.compile(r"^(?:Merge |Revert \"|fixup! |squash! )")
MAX_HEADER = 72
SCISSORS = "# ------------------------ >8 ------------------------"

AI_NAMES = (
    r"claude|anthropic|copilot|chatgpt|openai|gpt-?\d|gemini|bard|cursor|codeium|windsurf"
    r"|tabnine|codex|devin|aider|amazon q|codewhisperer|\bai\b|\bllm\b|\bbot\b"
)
CO_AUTHOR = re.compile(r"^co-authored-by:", re.IGNORECASE)
AI = re.compile(AI_NAMES, re.IGNORECASE)
GENERATED = re.compile(r"generated (?:with|by)\b", re.IGNORECASE)

USAGE = "Usage: check_commit_message.py <commit message file>"


def _lines(message: str) -> list[str]:
    """The message as git will keep it: no comment lines, nothing below the scissors."""
    kept = message.split(SCISSORS, 1)[0]
    return [line.rstrip() for line in kept.splitlines() if not line.startswith("#")]


def _header_problems(lines: list[str]) -> list[str]:
    header = lines[0]
    if GIT_MADE.match(header):
        return []
    match = HEADER.match(header)
    if match is None:
        return [f'Write the first line as "type(scope): summary" with type one of {TYPES}.']
    summary = match["summary"]
    rules = (
        (summary[0].isupper(), "The summary must start with a small letter."),
        (summary.endswith("."), "The summary must not end with a full stop."),
        (len(header) > MAX_HEADER, f"The first line must be at most {MAX_HEADER} characters."),
    )
    return [say for broken, say in rules if broken]


def _ai_credits(lines: list[str]) -> list[str]:
    credits = []
    for line in lines:
        text = line.strip()
        if (CO_AUTHOR.match(text) and AI.search(text)) or GENERATED.search(text):
            credits.append(f"Remove the AI credit: {text}")
    return credits


def problems(message: str) -> list[str]:
    """Every rule the message breaks; empty when it may be committed."""
    lines = _lines(message)
    while lines and not lines[-1]:
        lines.pop()
    if not lines or not lines[0].strip():
        return ["The commit message is empty."]
    found = _header_problems(lines)
    if len(lines) > 1 and lines[1]:
        found.append("Leave a blank line between the first line and the body.")
    return found + _ai_credits(lines)


def main(argv: list[str]) -> int:
    """Print every problem with the message in the file; 1 when there is one."""
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    found = problems(Path(argv[0]).read_text(encoding="utf-8"))
    for problem in found:
        print(f"commit message: {problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

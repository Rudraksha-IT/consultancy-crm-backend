"""The commit check (asked 2026-11-17): Conventional Commits, and no AI credited as co-author.

It runs as the commit-msg hook (pre-commit) and in CI over every pushed commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_commit_message import main, problems

GOOD_HEADERS = [
    "feat: add weekly days off",
    "feat(clock): add weekly days off",
    "fix(holidays)!: drop the date dropdowns",
    "docs: explain the compose files",
    "chore(deps): bump django to 5.2.18",
    "revert: feat(clock): add weekly days off",
    "test(clock-in): cover a working saturday",
    "Merge pull request #12 from Rudraksha-IT/feature",
    "Merge branch 'main' into feature",
]


@pytest.mark.parametrize("header", GOOD_HEADERS)
def test_accepts_a_conventional_header(header: str) -> None:
    """A Conventional Commits header, or one git writes itself, is accepted."""
    assert problems(f"{header}\n") == []


@pytest.mark.parametrize(
    ("message", "why"),
    [
        ("Add weekly days off\n", "type(scope): summary"),
        ("feature: add weekly days off\n", "type(scope): summary"),
        ("feat:add weekly days off\n", "type(scope): summary"),
        ("feat(Clock): add weekly days off\n", "type(scope): summary"),
        ("feat: Add weekly days off\n", "start with a small letter"),
        ("feat: add weekly days off.\n", "full stop"),
        ("feat: " + "a" * 70 + "\n", "72 characters"),
        ("feat: add it\nsecond line straight away\n", "blank line"),
        ("\n", "empty"),
        ("# only a comment\n", "empty"),
    ],
)
def test_refuses_a_header_that_breaks_the_standard(message: str, why: str) -> None:
    """A header off the standard is refused, saying which rule it breaks."""
    found = problems(message)

    assert found
    assert why in " ".join(found)


def test_the_pull_request_number_a_squash_adds_isnt_counted() -> None:
    """GitHub ends a squash merge's header with " (#N)"; only the PR title's own length counts.

    A 69-character PR title, squashed into dev, broke the release PR's check (2026-11-24).
    """
    title = "fix(home): leave finished Students out, count waiting from checklists"

    assert problems(f"{title} (#9)\n") == []
    assert "72 characters" in " ".join(problems("feat: " + "a" * 67 + " (#123)\n"))


AI_LINES = [
    "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
    "co-authored-by: Claude <claude@example.com>",
    "Co-authored-by: GitHub Copilot <copilot@github.com>",
    "Co-authored-by: ChatGPT <bot@openai.com>",
    "Co-authored-by: Gemini <gemini@google.com>",
    "Co-authored-by: Cursor Agent <cursoragent@cursor.com>",
    "Co-authored-by: Someone <noreply@anthropic.com>",
    "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
    "Generated with ChatGPT",
    "  Co-Authored-By:   Codeium <x@codeium.com>",
]


@pytest.mark.parametrize("line", AI_LINES)
def test_refuses_a_line_crediting_an_ai(line: str) -> None:
    """A line crediting an AI is refused, quoting the line."""
    found = problems(f"feat: add weekly days off\n\nBody text.\n\n{line}\n")

    assert found == [f"Remove the AI credit: {line.strip()}"]


@pytest.mark.parametrize(
    "message",
    [
        "fix: tidy the holiday calendar\n\nCo-authored-by: Sita Karki <sita@example.com>\n",
        "docs: explain how claude-style prompts were removed\n",
        "docs: update the ai section of the readme\n",
        "fix: tidy\n\n# Co-Authored-By: Claude <noreply@anthropic.com>\n",
    ],
)
def test_a_human_co_author_or_a_comment_is_fine(message: str) -> None:
    """A human co-author, a mention of AI, or a comment line is fine."""
    assert problems(message) == []


def test_ignores_what_git_cuts_off_below_the_scissors() -> None:
    """The diff git shows below the scissors line is not checked."""
    message = (
        "feat: add it\n"
        "# ------------------------ >8 ------------------------\n"
        "diff --git a/x b/x\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    )

    assert problems(message) == []


def test_the_hook_fails_and_says_why(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The hook exits 1 and prints every problem."""
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text("Add it\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n")

    assert main([str(message_file)]) == 1
    printed = capsys.readouterr().err
    assert "type(scope): summary" in printed
    assert "Co-Authored-By: Claude <noreply@anthropic.com>" in printed


def test_the_hook_passes_a_good_message(tmp_path: Path) -> None:
    """The hook exits 0 for a good message."""
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text("feat(clock): add weekly days off\n\nSaturday stays the default.\n")

    assert main([str(message_file)]) == 0


def test_the_hook_needs_exactly_one_file(capsys: pytest.CaptureFixture[str]) -> None:
    """The hook exits 2 with usage when not given one file."""
    assert main([]) == 2
    assert "Usage" in capsys.readouterr().err

#!/bin/sh
# Format, lint, types and missing migrations over the whole repo, run by hand when wanted (commits check the same; CI runs it all).
# Tests run on CI for every pull request; run them here with: uv run pytest -n auto
set -eu
echo "== format";      uv run ruff format --check .
echo "== lint";        uv run ruff check .
echo "== types";       uv run mypy .
echo "== migrations";  uv run python manage.py makemigrations --check --dry-run
echo "== all checks passed"

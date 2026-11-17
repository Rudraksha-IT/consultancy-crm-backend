# syntax=docker/dockerfile:1
# Two stages: uv builds the environment, and only that environment and the code are shipped.
# The final image has no uv, no build cache and no translation files (see below), so it stays
# small enough to pull quickly onto one micro server.

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
# The interface is English only (USE_I18N = False), so translation catalogues are never read.
# Each language's formats.py is kept.
RUN find /opt/venv -path "*/locale/*" \( -name "*.mo" -o -name "*.po" \) -delete

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
# gunicorn keeps its control socket under $HOME.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
# The code stays owned by root: the app user can read it but never change it.
COPY . .
USER app
EXPOSE 8000
# Migrations are not run here: a deploy runs them once, as their own step (see compose files).
CMD ["gunicorn", "--config", "python:config.gunicorn", "config.wsgi:application"]

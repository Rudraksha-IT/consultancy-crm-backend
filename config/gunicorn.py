"""gunicorn's settings (`gunicorn -c python:config.gunicorn`), sized for one 1 GiB server.

A fixed number of workers and threads keeps memory predictable; each worker is replaced after
`max_requests` so a slow leak can never grow without end. Django logs every request itself
(issue 43), so gunicorn writes no access log of its own; its error log (a worker timeout, or a
worker killed for memory) uses the same JSON lines (issue 58).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from config.logging import build_logging, read_log_format, read_log_level

# The log filters read the Consultancy from Django's connection, even before the app loads.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

DEFAULT_WORKERS = 2
DEFAULT_THREADS = 2
_MAX_WORKERS = 8
_MAX_THREADS = 16


def read_concurrency(env: Mapping[str, str]) -> tuple[int, int]:
    """Return (workers, threads) from GUNICORN_WORKERS and GUNICORN_THREADS, or the defaults.

    Raises:
        ValueError: A value isn't a whole number in range, so the server refuses to start.
    """
    workers = _read_count(env, "GUNICORN_WORKERS", DEFAULT_WORKERS, _MAX_WORKERS)
    threads = _read_count(env, "GUNICORN_THREADS", DEFAULT_THREADS, _MAX_THREADS)
    return workers, threads


def _read_count(env: Mapping[str, str], name: str, default: int, most: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    if not raw.isdigit() or not 1 <= int(raw) <= most:
        message = f"{name} must be a whole number from 1 to {most}."
        raise ValueError(message)
    return int(raw)


bind = "0.0.0.0:8000"
workers, threads = read_concurrency(os.environ)
# 90 s: a bulk message (issue 26) may take up to BULK_SEND_SECONDS (60) while the request waits.
timeout = 90
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
# Load Django once before forking, so the workers share its memory.
preload_app = True
# Heartbeat files in memory, not on the container's disk.
worker_tmp_dir = "/dev/shm"  # noqa: S108  # gunicorn's advice for Docker; one user in the container
accesslog = None
errorlog = "-"
logconfig_dict = build_logging(read_log_level(os.environ), read_log_format(os.environ))

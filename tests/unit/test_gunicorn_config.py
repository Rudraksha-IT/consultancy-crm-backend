"""How gunicorn runs in production: bounded memory on a 1 GiB server (micro EC2)."""

from __future__ import annotations

import pytest

from config.gunicorn import DEFAULT_THREADS, DEFAULT_WORKERS, read_concurrency


class TestReadConcurrency:
    def test_defaults_fit_a_micro_server(self) -> None:
        assert read_concurrency({}) == (DEFAULT_WORKERS, DEFAULT_THREADS) == (2, 2)

    def test_the_environment_can_change_them(self) -> None:
        env = {"GUNICORN_WORKERS": "3", "GUNICORN_THREADS": "4"}

        assert read_concurrency(env) == (3, 4)

    @pytest.mark.parametrize(
        "env",
        [
            {"GUNICORN_WORKERS": "0"},
            {"GUNICORN_WORKERS": "-1"},
            {"GUNICORN_WORKERS": "two"},
            {"GUNICORN_WORKERS": "9"},
            {"GUNICORN_THREADS": "0"},
            {"GUNICORN_THREADS": "17"},
            {"GUNICORN_THREADS": "2.5"},
        ],
    )
    def test_a_bad_value_stops_the_server_starting(self, env: dict[str, str]) -> None:
        with pytest.raises(ValueError, match="GUNICORN_"):
            read_concurrency(env)

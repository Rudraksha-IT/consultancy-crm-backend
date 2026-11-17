"""The standard log format (ai-backend's): keys, shapes, levels, redaction and correlation IDs."""

from __future__ import annotations

import json
import logging
import logging.config
import re
import sys
import uuid
from collections.abc import Iterator

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from config.correlation import (
    accept_correlation_id,
    get_correlation_id,
    reset_correlation_id,
    set_correlation_id,
)
from config.logging import DATE_FORMAT, build_logging, read_log_format, read_log_level
from config.logging_utils import (
    REDACTED,
    CorrelationFilter,
    DirectoryFilter,
    JsonFormatter,
    RedactionFilter,
)

EXPECTED_KEYS = [
    "level",
    "timestamp",
    "source",
    "tenant",
    "thread",
    "thread_id",
    "correlation_id",
    "message",
]
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}$")
CONSOLE_LINE = re.compile(
    r"^\[INFO\] \(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}\) "
    r"\[unit/test_logging_config\.py: test_console_format_matches_ai_backend, line \d+\] "
    r"\[Tenant: N/A, MainThread: \d+, Correlation ID: N/A\] hello$"
)
SECRET = "hunter2-Secret!"  # noqa: S105  # test-only value that must never be logged


def _record(message: str, *args: object, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=7,
        msg=message,
        args=args or None,
        exc_info=None,
        func="some_function",
    )


def _filtered(record: logging.LogRecord) -> logging.LogRecord:
    for log_filter in (DirectoryFilter(), CorrelationFilter(), RedactionFilter()):
        log_filter.filter(record)
    return record


def _json(record: logging.LogRecord) -> dict[str, object]:
    line = JsonFormatter(datefmt=DATE_FORMAT).format(_filtered(record))
    parsed: dict[str, object] = json.loads(line)
    return parsed


@pytest.fixture
def stdout_logging(capsys: pytest.CaptureFixture[str]) -> Iterator[None]:
    """Let a test apply its own LOGGING; Django's is put back afterwards."""
    yield
    logging.config.dictConfig(settings.LOGGING)


class TestJsonFormatter:
    def test_keys_are_exactly_the_standard_ones_in_order(self) -> None:
        assert list(_json(_record("hello"))) == EXPECTED_KEYS

    def test_values_have_the_standard_shapes(self) -> None:
        entry = _json(_record("hello %s", "world"))
        assert entry["level"] == "INFO"
        assert TIMESTAMP.match(str(entry["timestamp"]))
        assert entry["source"] == "unit/test_logging_config.py: some_function, line 7"
        assert entry["thread"] == "MainThread"
        assert isinstance(entry["thread_id"], int)
        assert entry["message"] == "hello world"

    def test_outside_a_request_and_a_consultancy_both_are_n_a(self) -> None:
        entry = _json(_record("hello"))
        assert entry["tenant"] == "N/A"
        assert entry["correlation_id"] == "N/A"

    def test_an_exception_adds_its_traceback(self) -> None:
        record = _record("failed", level=logging.ERROR)
        try:
            raise ValueError("boom")
        except ValueError:
            record.exc_info = sys.exc_info()
        entry = _json(record)
        assert list(entry) == [*EXPECTED_KEYS, "exception"]
        assert "ValueError: boom" in str(entry["exception"])

    def test_one_line_even_when_the_message_has_newlines(self) -> None:
        line = JsonFormatter(datefmt=DATE_FORMAT).format(_filtered(_record("a\nb")))
        assert "\n" not in line


class TestCorrelationId:
    def test_a_uuid_is_kept(self) -> None:
        sent = str(uuid.uuid4())
        assert accept_correlation_id(sent) == sent

    def test_an_upper_case_uuid_is_kept_in_lower_case(self) -> None:
        sent = str(uuid.uuid4())
        assert accept_correlation_id(sent.upper()) == sent

    @pytest.mark.parametrize(
        "sent",
        [
            None,
            "",
            "not-a-uuid",
            f"{uuid.uuid4()}\nINFO forged line",
            f"{uuid.uuid4()} ",
            "<script>alert(1)</script>",
            "'; DROP TABLE staff_staff; --",
            uuid.uuid4().hex,
            "x" * 10_000,
        ],
    )
    def test_anything_else_is_replaced_by_a_new_uuid(self, sent: str | None) -> None:
        accepted = accept_correlation_id(sent)
        assert accepted != sent
        assert str(uuid.UUID(accepted)) == accepted

    def test_the_id_lives_in_a_context_var_and_is_reset(self) -> None:
        token = set_correlation_id("3f1c0000-0000-4000-8000-000000000000")
        assert get_correlation_id() == "3f1c0000-0000-4000-8000-000000000000"
        assert _json(_record("x"))["correlation_id"] == "3f1c0000-0000-4000-8000-000000000000"
        reset_correlation_id(token)
        assert get_correlation_id() is None


class TestRedaction:
    @pytest.mark.parametrize(
        "message",
        [
            f"password={SECRET}",
            f"new_password: {SECRET}",
            f'{{"password": "{SECRET}"}}',
            f"{{'refresh': '{SECRET}'}}",
            f"code={SECRET}",
            f"token={SECRET}",
            f"X-Internal-Token: {SECRET}",
            f"secret={SECRET}",
            f"cookie={SECRET}",
            f"Authorization={SECRET}",
            f"access={SECRET}",
            f"refresh_token={SECRET}",
        ],
    )
    def test_secret_values_are_blanked(self, message: str) -> None:
        entry = _json(_record(message))
        assert SECRET not in str(entry["message"])
        assert "[redacted]" in str(entry["message"])

    def test_secret_values_in_format_arguments_are_blanked(self) -> None:
        entry = _json(_record("login with password=%s", SECRET))
        assert SECRET not in str(entry["message"])

    def test_secret_extra_fields_are_blanked(self) -> None:
        record = _record("hello")
        vars(record)["password"] = SECRET
        _filtered(record)
        assert vars(record)["password"] == REDACTED

    def test_secret_values_in_an_exception_are_blanked(self) -> None:
        record = _record("failed", level=logging.ERROR)
        try:
            raise ValueError(f"token={SECRET}")
        except ValueError:
            record.exc_info = sys.exc_info()
        assert SECRET not in str(_json(record)["exception"])

    def test_secret_values_in_stack_info_are_blanked(self) -> None:
        record = _record("hello")
        record.stack_info = f"Stack (most recent call last):\n  password={SECRET}"
        _filtered(record)
        assert SECRET not in record.stack_info

    def test_a_malformed_log_call_never_raises(self) -> None:
        entry = _json(_record("needs two %s %s", "one"))
        assert entry["message"] == "needs two %s %s"

    def test_other_values_are_left_alone(self) -> None:
        message = "POST /api/auth/login/ 200 staff_id=4"
        assert _json(_record(message))["message"] == message


class TestLogLevel:
    def test_info_by_default(self) -> None:
        assert read_log_level({}) == "INFO"

    @pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "debug"])
    def test_a_known_level_is_used(self, level: str) -> None:
        assert read_log_level({"LOG_LEVEL": level}) == level.upper()

    @pytest.mark.parametrize("level", ["", "TRACE", "CRITICAL", "INFO\n", "1", "<b>"])
    def test_an_unknown_level_is_refused(self, level: str) -> None:
        with pytest.raises(ImproperlyConfigured, match="LOG_LEVEL"):
            read_log_level({"LOG_LEVEL": level})

    def test_the_refusal_does_not_echo_the_value(self) -> None:
        with pytest.raises(ImproperlyConfigured) as refused:
            read_log_level({"LOG_LEVEL": "<script>"})
        assert "<script>" not in str(refused.value)

    def test_the_root_logger_uses_the_level(self) -> None:
        root = build_logging("WARNING", "json")["root"]
        assert root == {"handlers": ["json_handler"], "level": "WARNING"}

    @pytest.mark.usefixtures("stdout_logging")
    def test_the_level_is_honoured(self, capsys: pytest.CaptureFixture[str]) -> None:
        logging.config.dictConfig(build_logging("WARNING", "json"))
        logging.getLogger("tests").info("quiet")
        logging.getLogger("tests").warning("loud")
        lines = capsys.readouterr().out.splitlines()
        assert [json.loads(line)["message"] for line in lines] == ["loud"]

    @pytest.mark.usefixtures("stdout_logging")
    def test_debug_is_written_only_when_asked_for(self, capsys: pytest.CaptureFixture[str]) -> None:
        logging.config.dictConfig(build_logging("DEBUG", "json"))
        logging.getLogger("tests").debug("details")
        assert json.loads(capsys.readouterr().out)["level"] == "DEBUG"


class TestLogFormat:
    def test_json_by_default(self) -> None:
        assert read_log_format({}) == "json"

    def test_console_when_asked_for(self) -> None:
        assert read_log_format({"LOG_FORMAT": "console"}) == "console"

    def test_an_unknown_format_is_refused(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="LOG_FORMAT"):
            read_log_format({"LOG_FORMAT": "xml"})

    def test_settings_have_ai_backends_filters_and_formatters(self) -> None:
        config = build_logging("INFO", "json")
        assert list(config["filters"]) == [
            "directory_filter",
            "correlation_filter",
            "tenant_context",
            "redaction_filter",
            "traceback_only",
        ]
        assert list(config["formatters"]) == ["json_formatter", "console_formatter"]

    @pytest.mark.usefixtures("stdout_logging")
    def test_console_format_matches_ai_backend(self, capsys: pytest.CaptureFixture[str]) -> None:
        logging.config.dictConfig(build_logging("INFO", "console"))
        logging.getLogger("tests").info("hello")
        assert CONSOLE_LINE.match(capsys.readouterr().out.rstrip("\n"))

    @pytest.mark.usefixtures("stdout_logging")
    def test_writes_one_json_object_per_line_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        logging.config.dictConfig(build_logging("INFO", "json"))
        logging.getLogger("django").warning("first")
        logging.getLogger("tests").info("second")
        captured = capsys.readouterr()
        assert captured.err == ""
        messages = [json.loads(line)["message"] for line in captured.out.splitlines()]
        assert messages == ["first", "second"]

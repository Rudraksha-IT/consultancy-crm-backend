"""Exporting a list as a CSV file, and recording it (spec 31b, 31c).

Call inside the Consultancy's schema. Who may export is the views' job, through `access.policy`.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from exports.exceptions import TooManyRowsError
from exports.lists import LIST_EXPORTS, describe
from exports.models import ExportList, ExportRecord
from staff.actors import actor_name, is_platform_visitor, stored_staff

if TYPE_CHECKING:
    from config.serializers import StrictSerializer
    from staff.models import Staff

MAX_ROWS = 10_000
CSV_CONTENT_TYPE = "text/csv; charset=utf-8"
# Excel reads a file as UTF-8 (Nepali names) only when it starts with the byte order mark.
BYTE_ORDER_MARK = "﻿"
# A cell starting with one of these could run as a formula in a spreadsheet (CSV injection).
FORMULA_START = re.compile(r"^[=+\-@\t\r]")

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExportFile:
    """A finished export: the file's name and bytes, and how many rows it holds."""

    name: str
    content: bytes
    row_count: int


def export_list(
    which: ExportList,
    query: StrictSerializer[None],
    filters: Mapping[str, Any],
    by: Staff,
) -> ExportFile:
    """Build the CSV of one list, filtered by `query`, and record the export under `by`.

    `query` is the list's own query serializer, already checked; `filters` are the values it was
    given, kept on the Export Record. The file and the record are made in one transaction.

    Raises:
        TooManyRowsError: The filtered list has more than `MAX_ROWS` rows; nothing is recorded.
    """
    export = LIST_EXPORTS[which]
    found = export.find(query, by)
    if found.count() > MAX_ROWS:
        raise TooManyRowsError
    with transaction.atomic():
        content, row_count = _write_rows(export.columns, export.rows(found, query))
        record = ExportRecord.objects.create(
            list=which,
            filters=dict(filters),
            filters_text=describe(export, filters, by),
            row_count=row_count,
            by_staff=stored_staff(by),
            by_name=actor_name(by),
            by_platform_admin=is_platform_visitor(by),
        )
    logger.info("List exported: list=%s, rows=%s, export_id=%s", which, row_count, record.pk)
    today = timezone.localdate().isoformat()
    return ExportFile(name=f"{which}-{today}.csv", content=content, row_count=row_count)


def export_records() -> QuerySet[ExportRecord]:
    """Every Export Record, newest first."""
    return ExportRecord.objects.order_by("-at", "-pk")


def safe_cell(value: str) -> str:
    """The value, with a `'` in front when a spreadsheet would read it as a formula."""
    return f"'{value}" if FORMULA_START.match(value) else value


def _write_rows(columns: Iterable[str], rows: Iterable[list[str]]) -> tuple[bytes, int]:
    """The CSV file (UTF-8 with its byte order mark) and how many rows it holds."""
    out = io.StringIO()
    out.write(BYTE_ORDER_MARK)
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(columns)
    count = 0
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
        count += 1
    return out.getvalue().encode("utf-8"), count

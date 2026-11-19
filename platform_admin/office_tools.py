"""What only a Platform Admin can do inside a Consultancy (spec 11a, issue 27).

Send any active Staff member (Admins included) a password reset Code, sign anyone out of every
device, and download all of the office's data. Call inside the Consultancy's schema.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from decimal import Decimal

from django.apps import apps
from django.db import models

from auth_tokens.services import sign_out_everywhere
from codes.emails import send_code_email
from codes.services import issue_code
from codes.types import CodePurpose
from staff.exceptions import EmployeeChangeRefusedError
from staff.models import PlatformVisitor, Staff, StaffStatus

# Each Consultancy's own apps whose data the download holds (spec Implementation: Platform).
EXPORTED_APPS = (
    "staff",
    "students",
    "history",
    "catalogue",
    "applications",
    "documents",
    "payments",
    "fees",
    "expenses",
    "commission",
    "tasks",
    "notifications",
    "batches",
    "communication",
    "visits",
)
# Secrets never leave the server, not even in the office's own download.
SECRET_FIELDS = frozenset({"password"})
# A cell starting with one of these could run as a formula in a spreadsheet.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

NOT_JOINED_MESSAGE = (
    "Only someone who has joined can get a reset code. Send their invite again instead."
)

logger = logging.getLogger(__name__)


def send_reset_code_to(staff: Staff, office: str, by: PlatformVisitor) -> None:
    """Email `staff` a password reset Code, whatever their Role (Admins included).

    Raises:
        EmployeeChangeRefusedError: They haven't joined yet, or have left.
        CodeLimitReachedError: Too many Codes were sent to them lately.
    """
    if staff.status != StaffStatus.ACTIVE:
        raise EmployeeChangeRefusedError(NOT_JOINED_MESSAGE)
    code = issue_code(staff, CodePurpose.RESET)
    send_code_email(staff, code, CodePurpose.RESET, office)
    logger.info(
        "Reset code sent by Platform Admin: staff_id=%s, visit_id=%s",
        staff.pk,
        by.support_visit_id,
    )


def sign_out_staff(staff: Staff, by: PlatformVisitor) -> None:
    """End every login of `staff` at once, access tokens too (Admins included)."""
    sign_out_everywhere(staff)
    logger.info(
        "Signed out by Platform Admin: staff_id=%s, visit_id=%s", staff.pk, by.support_visit_id
    )


def office_data_zip() -> bytes:
    """All of the office's data as a ZIP of CSV files, one per kind of record.

    Every row and column except secrets (passwords, provider credentials). Document files stay
    in storage; their details are included. Cells that a spreadsheet could run start with `'`.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for model in exported_models():
            meta = model._meta
            archive.writestr(f"{meta.app_label}_{meta.model_name}.csv", _csv_of(model))
    return buffer.getvalue()


def exported_models() -> Iterator[type[models.Model]]:
    """Every stored kind of record in the exported apps, in a fixed order."""
    for label in EXPORTED_APPS:
        for model in apps.get_app_config(label).get_models():
            if not model._meta.proxy:
                yield model


def spreadsheet_safe(text: str) -> str:
    """Return `text` so a spreadsheet shows it as text and never runs it as a formula."""
    return f"'{text}" if text.startswith(FORMULA_STARTS) else text


def _csv_of(model: type[models.Model]) -> str:
    columns = [
        field.attname for field in model._meta.concrete_fields if field.name not in SECRET_FIELDS
    ]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    rows: Iterable[tuple[object, ...]] = (
        model._default_manager.order_by("pk").values_list(*columns).iterator()
    )
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return out.getvalue()


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool | int | Decimal | float):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict | list):
        return spreadsheet_safe(json.dumps(value, ensure_ascii=False))
    return spreadsheet_safe(str(value))

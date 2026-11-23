"""The product's own logo (issue 65) and its saved logos (first live fixes, ticket 07).

Every upload is kept as a saved logo, at most `MAX_SAVED_LOGOS`; one of them, or none, is in
use. None means the tab shows the app's own logo and the login screens the initials. Call on the
public schema. Checked like an office logo (`tenancy.logo`) before it reaches here.
"""

from __future__ import annotations

import io
import logging
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django_tenants.utils import get_public_schema_name, schema_context

from documents.exceptions import DocumentStorageError
from documents.storage import document_storage
from platform_admin.exceptions import SavedLogosFullError
from platform_admin.models import (
    DEFAULT_PRODUCT_NAME,
    PRODUCT_NAME_MAX_LENGTH,
    PlatformAdmin,
    ProductBrand,
    ProductNameChange,
    SavedProductLogo,
)
from tenancy.logo import CheckedLogo
from tenancy.types import StoredLogo

LOGO_URL = "/api/platform/brand/logo/{version}/"
SAVED_LOGO_FILE_URL = "/api/platform/brand/logos/{id}/file/"
MAX_SAVED_LOGOS = 10
_BRAND_FIELDS = ["logo", "logo_version"]

logger = logging.getLogger(__name__)


__all__ = ["DEFAULT_PRODUCT_NAME", "PRODUCT_NAME_MAX_LENGTH"]

BLANK_NAME_MESSAGE = "Enter the product's name."
LONG_NAME_MESSAGE = f"Use at most {PRODUCT_NAME_MAX_LENGTH} characters."
NAME_KIND_MESSAGE = "Use letters, numbers, spaces and . - & ' ( ) + only."
# Besides letters, numbers and their marks (any language): nothing that could start markup, a
# template, a quote, a header line or a hidden direction change.
_NAME_MARKS = frozenset(" .-&'()+")


@dataclass(frozen=True)
class ProductLogo:
    """Where the login screens load the product's logo in use from."""

    url: str


@dataclass(frozen=True)
class SavedLogo:
    """One saved logo as Platform → Settings lists it."""

    id: str
    file_url: str
    uploaded_at: datetime
    in_use: bool


def _brand() -> ProductBrand:
    brand, _ = ProductBrand.objects.select_for_update().get_or_create(pk=1)
    return brand


def _shown(brand: ProductBrand) -> ProductLogo | None:
    if brand.logo_id is None:
        return None
    return ProductLogo(url=LOGO_URL.format(version=brand.logo_version))


def _put_in_use(brand: ProductBrand, logo: SavedProductLogo | None) -> ProductLogo | None:
    """Show `logo` (or none) at a new address, so every cache follows."""
    brand.logo = logo
    brand.logo_version += 1
    brand.save(update_fields=_BRAND_FIELDS)
    return _shown(brand)


def get_product_logo() -> ProductLogo | None:
    """The product's logo in use, or None (no uploaded logo is in use)."""
    brand = ProductBrand.objects.filter(pk=1).first()
    return None if brand is None else _shown(brand)


def list_saved_logos() -> list[SavedLogo]:
    """Every saved logo, newest first, with the one in use marked."""
    brand = ProductBrand.objects.filter(pk=1).first()
    in_use = None if brand is None else brand.logo_id
    return [
        SavedLogo(
            id=str(logo.public_id),
            file_url=SAVED_LOGO_FILE_URL.format(id=logo.public_id),
            uploaded_at=logo.uploaded_at,
            in_use=logo.pk == in_use,
        )
        for logo in SavedProductLogo.objects.order_by("-uploaded_at", "-pk")
    ]


def set_product_logo(logo: CheckedLogo) -> ProductLogo:
    """Store a checked logo privately as a saved logo and put it in use.

    Raises:
        SavedLogosFullError: `MAX_SAVED_LOGOS` are already saved; nothing changes.
        DocumentStorageError: The file couldn't be stored; nothing changes.
    """
    with transaction.atomic():
        brand = _brand()
        if SavedProductLogo.objects.count() >= MAX_SAVED_LOGOS:
            raise SavedLogosFullError
        key = f"platform/product-logo/{uuid.uuid4().hex}{logo.kind.extension}"
        document_storage().save(key, io.BytesIO(logo.content), logo.kind.content_type)
        saved = SavedProductLogo.objects.create(key=key, content_type=logo.kind.content_type)
        _put_in_use(brand, saved)
    return ProductLogo(url=LOGO_URL.format(version=brand.logo_version))


def use_saved_logo(logo_id: int | None) -> ProductLogo | None:
    """Put a saved logo (by number) in use, or none (the app's own logo) for None.

    Raises:
        SavedProductLogo.DoesNotExist: No saved logo has that number.
    """
    with transaction.atomic():
        brand = _brand()
        logo = None if logo_id is None else SavedProductLogo.objects.get(pk=logo_id)
        return _put_in_use(brand, logo)


def remove_saved_logo(logo_id: int) -> None:
    """Remove a saved logo and its file; removing the one in use goes back to the app's own.

    Raises:
        SavedProductLogo.DoesNotExist: No saved logo has that number.
    """
    with transaction.atomic():
        brand = _brand()
        logo = SavedProductLogo.objects.get(pk=logo_id)
        key = logo.key
        if brand.logo_id == logo.pk:
            _put_in_use(brand, None)
        logo.delete()
    _remove_file(key)


def read_product_logo(version: int) -> StoredLogo | None:
    """The logo in use's bytes if `version` is the one shown now, otherwise None.

    Raises:
        DocumentStorageError: The file couldn't be read.
    """
    brand = ProductBrand.objects.select_related("logo").filter(pk=1).first()
    if brand is None or brand.logo is None or version != brand.logo_version:
        return None
    return StoredLogo(document_storage().read(brand.logo.key), brand.logo.content_type)


def read_saved_logo(logo_id: int) -> StoredLogo | None:
    """A saved logo's bytes (for Platform → Settings), or None when there's no such logo.

    Raises:
        DocumentStorageError: The file couldn't be read.
    """
    logo = SavedProductLogo.objects.filter(pk=logo_id).first()
    if logo is None:
        return None
    return StoredLogo(document_storage().read(logo.key), logo.content_type)


def _remove_file(key: str) -> None:
    try:
        document_storage().delete(key)
    except DocumentStorageError:
        logger.warning("Removed product logo's file not deleted")


def tidy_product_name(name: str) -> str:
    """The name as it would be kept: single spaces between words, none at the ends.

    Only plain spaces are tidied; a tab or line break stays, so the check refuses it.
    """
    return " ".join(part for part in name.split(" ") if part)


def product_name_problem(name: str) -> str | None:
    """Why a tidied product name is refused, or None (ticket 15)."""
    if name == "":
        return BLANK_NAME_MESSAGE
    if len(name) > PRODUCT_NAME_MAX_LENGTH:
        return LONG_NAME_MESSAGE
    if not _is_letter_or_number(name[0]) or not all(_is_name_character(c) for c in name):
        return NAME_KIND_MESSAGE
    return None


def _is_letter_or_number(character: str) -> bool:
    return unicodedata.category(character)[0] in {"L", "N"}


def _is_name_character(character: str) -> bool:
    return character in _NAME_MARKS or unicodedata.category(character)[0] in {"L", "N", "M"}


def get_product_name() -> str:
    """The product's name in use: the saved one, or the default before any change."""
    with schema_context(get_public_schema_name()):
        name = ProductBrand.objects.filter(pk=1).values_list("name", flat=True).first()
    return DEFAULT_PRODUCT_NAME if name is None else name


def set_product_name(name: str, by: PlatformAdmin) -> str:
    """Keep a checked, tidied name; record the change (none when it is the same name)."""
    with schema_context(get_public_schema_name()), transaction.atomic():
        brand = _brand()
        if brand.name != name:
            ProductNameChange.objects.create(before=brand.name, after=name, by=by)
            brand.name = name
            brand.save(update_fields=["name"])
    return name

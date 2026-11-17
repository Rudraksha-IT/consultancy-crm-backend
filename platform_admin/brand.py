"""The product's own logo (issue 65): one for the whole product, set on the Platform.

Call on the public schema. Checked like an office logo (`tenancy.logo`) before it reaches here.
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass

from documents.exceptions import DocumentStorageError
from documents.storage import document_storage
from platform_admin.models import ProductBrand
from tenancy.logo import CheckedLogo
from tenancy.types import StoredLogo

LOGO_URL = "/api/platform/brand/logo/{version}/"
_LOGO_FIELDS = ["logo_key", "logo_content_type", "logo_version"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProductLogo:
    """Where the login screens load the product's logo from."""

    url: str


def _brand() -> ProductBrand:
    brand, _ = ProductBrand.objects.get_or_create(pk=1)
    return brand


def get_product_logo() -> ProductLogo | None:
    """The product's logo, or None (the initials show)."""
    brand = ProductBrand.objects.filter(pk=1).first()
    if brand is None or not brand.logo_key:
        return None
    return ProductLogo(url=LOGO_URL.format(version=brand.logo_version))


def set_product_logo(logo: CheckedLogo) -> ProductLogo:
    """Store a checked logo privately and show it; the old file is removed afterwards.

    Raises:
        DocumentStorageError: The file couldn't be stored; the old logo stays.
    """
    brand = _brand()
    old_key = brand.logo_key
    version = brand.logo_version + 1
    key = f"platform/product-logo/{version}-{uuid.uuid4().hex}{logo.kind.extension}"
    document_storage().save(key, io.BytesIO(logo.content), logo.kind.content_type)
    brand.logo_key = key
    brand.logo_content_type = logo.kind.content_type
    brand.logo_version = version
    brand.save(update_fields=_LOGO_FIELDS)
    _remove_file(old_key)
    return ProductLogo(url=LOGO_URL.format(version=version))


def remove_product_logo() -> None:
    """Stop showing the product's logo (the initials show again) and remove its file."""
    brand = _brand()
    old_key = brand.logo_key
    brand.logo_key = ""
    brand.logo_content_type = ""
    brand.save(update_fields=_LOGO_FIELDS)
    _remove_file(old_key)


def read_product_logo(version: int) -> StoredLogo | None:
    """The logo's bytes if `version` is the one shown now, otherwise None.

    Raises:
        DocumentStorageError: The file couldn't be read.
    """
    brand = ProductBrand.objects.filter(pk=1).first()
    if brand is None or not brand.logo_key or version != brand.logo_version:
        return None
    return StoredLogo(document_storage().read(brand.logo_key), brand.logo_content_type)


def _remove_file(key: str) -> None:
    if not key:
        return
    try:
        document_storage().delete(key)
    except DocumentStorageError:
        logger.warning("Old product logo file not removed")

"""The Platform's look for its emails (ADR 0023): the product's name and logo, in emerald.

Not part of the pure builder: it reads the product's logo from private storage.
"""

from __future__ import annotations

import logging

from documents.exceptions import DocumentStorageError
from documents.storage import document_storage
from emails.looks import PRODUCT_NAME
from emails.types import Logo, PlatformLook
from platform_admin.models import ProductBrand

logger = logging.getLogger(__name__)

# Only these are carried inside an email; the builder shows the name for anything else, so
# other logos are never read.
_INLINE_LOGO_TYPES = frozenset({"image/png", "image/jpeg"})


def _logo() -> Logo | None:
    brand = ProductBrand.objects.filter(pk=1).first()
    if brand is None or not brand.logo_key or brand.logo_content_type not in _INLINE_LOGO_TYPES:
        return None
    try:
        content = document_storage().read(brand.logo_key)
    except DocumentStorageError:
        # silent: the email shows the product name instead of the logo
        logger.warning("Product logo not read for an email")
        return None
    return Logo(content=content, content_type=brand.logo_content_type)


def platform_look() -> PlatformLook:
    """The product's name and its logo (PNG or JPEG), for every Platform email."""
    return PlatformLook(name=PRODUCT_NAME, logo=_logo())

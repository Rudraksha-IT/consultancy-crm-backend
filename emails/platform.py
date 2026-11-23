"""The Platform's look for its emails (ADR 0023): the product's saved name and logo, in emerald.

Not part of the pure builder: it reads the product's logo from private storage.
"""

from __future__ import annotations

import logging

from documents.exceptions import DocumentStorageError
from documents.storage import document_storage
from emails.types import Logo, PlatformLook
from platform_admin.brand import get_product_name
from platform_admin.models import ProductBrand

logger = logging.getLogger(__name__)

# Only these are carried inside an email; the builder shows the name for anything else, so
# other logos are never read.
_INLINE_LOGO_TYPES = frozenset({"image/png", "image/jpeg"})


def _logo() -> Logo | None:
    brand = ProductBrand.objects.select_related("logo").filter(pk=1).first()
    logo = None if brand is None else brand.logo
    if logo is None or logo.content_type not in _INLINE_LOGO_TYPES:
        return None
    try:
        content = document_storage().read(logo.key)
    except DocumentStorageError:
        # silent: the email shows the product name instead of the logo
        logger.warning("Product logo not read for an email")
        return None
    return Logo(content=content, content_type=logo.content_type)


def platform_look() -> PlatformLook:
    """The product's name and its logo (PNG or JPEG), for every Platform email."""
    return PlatformLook(name=get_product_name(), logo=_logo())

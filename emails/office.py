"""An office's look for its emails, read from its Consultancy (ADR 0023).

Not part of the pure builder: it reads the Consultancy and the logo file from private storage.
"""

from __future__ import annotations

import logging

from documents.exceptions import DocumentStorageError
from documents.storage import document_storage
from emails.types import Logo, OfficeLook
from tenancy.models import Consultancy
from tenancy.services import get_office_details

logger = logging.getLogger(__name__)

# Only these are carried inside an email; the builder shows the name for anything else, so
# other logos are never read.
_INLINE_LOGO_TYPES = frozenset({"image/png", "image/jpeg"})


def _logo(consultancy: Consultancy) -> Logo | None:
    if not consultancy.logo_key or consultancy.logo_content_type not in _INLINE_LOGO_TYPES:
        return None
    try:
        content = document_storage().read(consultancy.logo_key)
    except DocumentStorageError:
        # silent: the email shows the office name instead of the logo
        logger.warning("Office logo not read for an email: consultancy_id=%s", consultancy.pk)
        return None
    return Logo(content=content, content_type=consultancy.logo_content_type)


def office_look(consultancy: Consultancy) -> OfficeLook:
    """The office's name, logo (PNG or JPEG) and Office details."""
    return OfficeLook(
        name=consultancy.name,
        logo=_logo(consultancy),
        details=get_office_details(consultancy),
    )

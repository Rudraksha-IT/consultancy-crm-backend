"""Where Document files are kept: private S3-compatible storage (R2; MinIO locally).

Nothing is public. A file is reached only through a signed link that expires in minutes, issued
by `documents.services` after a permission check (spec 70). Tests swap the storage for a fake by
pointing the `DOCUMENT_STORAGE` setting at another class.
"""

from __future__ import annotations

import functools
from typing import IO, TYPE_CHECKING, Protocol
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from config.outside_calls import outside_call
from documents.exceptions import DocumentStorageError

if TYPE_CHECKING:
    from botocore.config import _S3Dict
    from mypy_boto3_s3 import S3Client

PROVIDER = "s3"
# How long a signed link works.
SIGNED_LINK_SECONDS = 300
_NOT_ASCII_OR_UNSAFE = str.maketrans(dict.fromkeys('"\\;', "_"))


class DocumentStorage(Protocol):
    """Keeps Document files and hands out short-lived links to them."""

    def save(self, key: str, content: IO[bytes], content_type: str) -> None:
        """Store `content` at `key`.

        Raises:
            DocumentStorageError: The file couldn't be stored.
        """

    def signed_link(
        self, key: str, *, file_name: str, content_type: str, as_attachment: bool
    ) -> str:
        """A link to the file at `key` that works for SIGNED_LINK_SECONDS only."""

    def read(self, key: str) -> bytes:
        """The bytes stored at `key` (the office logo, which the server serves itself).

        Raises:
            DocumentStorageError: The file couldn't be read.
        """

    def delete(self, key: str) -> None:
        """Remove the file at `key`.

        Raises:
            DocumentStorageError: The file couldn't be removed.
        """


def document_storage() -> DocumentStorage:
    """The storage the `DOCUMENT_STORAGE` setting names (a dotted class path)."""
    return _storage_for(settings.DOCUMENT_STORAGE)


@functools.cache
def _storage_for(path: str) -> DocumentStorage:
    storage: DocumentStorage = import_string(path)()
    return storage


def content_disposition(file_name: str, *, as_attachment: bool) -> str:
    """How the browser should treat the file: open it, or save it under `file_name`.

    The plain name keeps ASCII only (anything else becomes "_"); the UTF-8 name (RFC 6266) keeps
    the rest, percent-encoded so nothing can break out of the header.
    """
    disposition = "attachment" if as_attachment else "inline"
    ascii_name = "".join(c if c.isascii() and c.isprintable() else "_" for c in file_name)
    ascii_name = ascii_name.translate(_NOT_ASCII_OR_UNSAFE)
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(file_name, safe='')}"


class S3DocumentStorage:
    """Private S3 storage.

    In production both endpoints are Cloudflare R2 with an R2 token's keys from the env file
    (ADR 0025); locally the endpoint is MinIO, with a second, public endpoint for signing links
    the browser can reach.
    """

    def __init__(self) -> None:
        bucket = settings.DOCUMENTS_BUCKET
        if not bucket:
            message = "DOCUMENTS_BUCKET must name the private bucket for Documents."
            raise ImproperlyConfigured(message)
        self.bucket = bucket
        endpoint = settings.DOCUMENTS_S3_ENDPOINT_URL or None
        public = settings.DOCUMENTS_S3_PUBLIC_ENDPOINT_URL or endpoint
        self._client = _client(endpoint)
        self._signing_client = _client(public)

    def save(self, key: str, content: IO[bytes], content_type: str) -> None:
        """Upload the file privately (no ACL: the bucket blocks public access)."""
        try:
            with outside_call(PROVIDER, "put_object"):
                self._client.put_object(
                    Bucket=self.bucket, Key=key, Body=content, ContentType=content_type
                )
        except (BotoCoreError, ClientError) as error:
            raise DocumentStorageError from error

    def signed_link(
        self, key: str, *, file_name: str, content_type: str, as_attachment: bool
    ) -> str:
        """A GET link signed for SIGNED_LINK_SECONDS, which also fixes the answer's headers."""
        link: str = self._signing_client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": content_disposition(
                    file_name, as_attachment=as_attachment
                ),
            },
            ExpiresIn=SIGNED_LINK_SECONDS,
        )
        return link

    def read(self, key: str) -> bytes:
        """Download the file's bytes."""
        try:
            with outside_call(PROVIDER, "get_object"):
                answer = self._client.get_object(Bucket=self.bucket, Key=key)
                content: bytes = answer["Body"].read()
        except (BotoCoreError, ClientError) as error:
            raise DocumentStorageError from error
        return content

    def delete(self, key: str) -> None:
        """Remove the file (S3 answers the same when it is already gone)."""
        try:
            with outside_call(PROVIDER, "delete_object"):
                self._client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as error:
            raise DocumentStorageError from error

    def ensure_bucket(self) -> bool:
        """Create the bucket if it is missing (local development). Return whether it was made."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:  # silent: a missing bucket is the answer; it is made below
            with outside_call(PROVIDER, "create_bucket"):
                self._client.create_bucket(Bucket=self.bucket)
            return True
        return False


def _client(endpoint: str | None) -> S3Client:
    s3_options: _S3Dict = {"addressing_style": "path" if endpoint else "auto"}
    client: S3Client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.DOCUMENTS_S3_REGION,
        config=Config(signature_version="s3v4", s3=s3_options),
    )
    return client

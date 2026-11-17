"""The S3 storage behind Documents, checked without a network (issue 16)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from pytest_django.fixtures import Settings

from documents.exceptions import DocumentStorageError
from documents.storage import (
    SIGNED_LINK_SECONDS,
    S3DocumentStorage,
    content_disposition,
    document_storage,
)
from tests.fakes import FakeDocumentStorage

BUCKET = "crm-documents-test"


@pytest.fixture
def s3_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings for local MinIO, with test keys (never real ones)."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
    settings.DOCUMENTS_BUCKET = BUCKET
    settings.DOCUMENTS_S3_REGION = "ap-south-1"
    settings.DOCUMENTS_S3_ENDPOINT_URL = "http://minio:9000"
    settings.DOCUMENTS_S3_PUBLIC_ENDPOINT_URL = "http://localhost:9000"
    return settings


@pytest.fixture
def storage(s3_settings: Settings) -> S3DocumentStorage:
    """The real S3 storage, pointed at a pretend MinIO."""
    return S3DocumentStorage()


@pytest.fixture
def stubbed(storage: S3DocumentStorage) -> Iterator[Stubber]:
    """Answers for the storage's S3 calls, so nothing goes over the network."""
    with Stubber(storage._client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


class TestSaving:
    def test_a_file_is_put_privately_with_its_type(
        self, storage: S3DocumentStorage, stubbed: Stubber
    ) -> None:
        content = io.BytesIO(b"%PDF-1.7")
        stubbed.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": "abc/documents/1/x.pdf",
                "Body": content,
                "ContentType": "application/pdf",
            },
        )

        storage.save("abc/documents/1/x.pdf", content, "application/pdf")

    def test_a_refusal_from_s3_is_a_storage_error(
        self, storage: S3DocumentStorage, stubbed: Stubber
    ) -> None:
        stubbed.add_client_error("put_object", "AccessDenied")

        with pytest.raises(DocumentStorageError):
            storage.save("abc/documents/1/x.pdf", io.BytesIO(b"x"), "application/pdf")


class TestReadingAndDeleting:
    """The office logo (issue 39) is read back by the server and replaced, never linked to."""

    def test_a_file_is_read_back(self, storage: S3DocumentStorage, stubbed: Stubber) -> None:
        stubbed.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"\x89PNG"), 4)},
            {"Bucket": BUCKET, "Key": "abc/office-logo/1.png"},
        )

        assert storage.read("abc/office-logo/1.png") == b"\x89PNG"

    def test_a_failed_read_is_a_storage_error(
        self, storage: S3DocumentStorage, stubbed: Stubber
    ) -> None:
        stubbed.add_client_error("get_object", "NoSuchKey")

        with pytest.raises(DocumentStorageError):
            storage.read("abc/office-logo/1.png")

    def test_a_file_is_deleted(self, storage: S3DocumentStorage, stubbed: Stubber) -> None:
        stubbed.add_response(
            "delete_object", {}, {"Bucket": BUCKET, "Key": "abc/office-logo/1.png"}
        )

        storage.delete("abc/office-logo/1.png")

    def test_a_failed_delete_is_a_storage_error(
        self, storage: S3DocumentStorage, stubbed: Stubber
    ) -> None:
        stubbed.add_client_error("delete_object", "AccessDenied")

        with pytest.raises(DocumentStorageError):
            storage.delete("abc/office-logo/1.png")


class TestSignedLinks:
    def test_a_link_is_signed_for_the_public_endpoint_and_expires(
        self, storage: S3DocumentStorage
    ) -> None:
        link = storage.signed_link(
            "abc/documents/1/x.pdf",
            file_name="passport.pdf",
            content_type="application/pdf",
            as_attachment=True,
        )

        parsed = urlparse(link)
        query = parse_qs(parsed.query)
        assert (parsed.scheme, parsed.netloc) == ("http", "localhost:9000")
        assert parsed.path == f"/{BUCKET}/abc/documents/1/x.pdf"
        assert query["X-Amz-Expires"] == [str(SIGNED_LINK_SECONDS)]
        assert query["response-content-type"] == ["application/pdf"]
        assert query["response-content-disposition"] == [
            "attachment; filename=\"passport.pdf\"; filename*=UTF-8''passport.pdf"
        ]
        assert "X-Amz-Signature" in query

    def test_without_a_public_endpoint_links_use_the_main_one(self, s3_settings: Settings) -> None:
        s3_settings.DOCUMENTS_S3_PUBLIC_ENDPOINT_URL = ""

        link = S3DocumentStorage().signed_link(
            "k.pdf", file_name="k.pdf", content_type="application/pdf", as_attachment=False
        )

        assert urlparse(link).netloc == "minio:9000"

    def test_in_aws_links_point_at_s3_itself(self, s3_settings: Settings) -> None:
        s3_settings.DOCUMENTS_S3_ENDPOINT_URL = ""
        s3_settings.DOCUMENTS_S3_PUBLIC_ENDPOINT_URL = ""

        link = S3DocumentStorage().signed_link(
            "k.pdf", file_name="k.pdf", content_type="application/pdf", as_attachment=False
        )

        assert urlparse(link).netloc.endswith("amazonaws.com")


class TestContentDisposition:
    def test_opening_shows_the_file_in_the_browser(self) -> None:
        assert content_disposition("a.pdf", as_attachment=False).startswith("inline; ")

    @pytest.mark.parametrize(
        ("name", "header"),
        [
            (
                "नागरिकता.pdf",
                "attachment; filename=\"________.pdf\"; filename*=UTF-8''"
                "%E0%A4%A8%E0%A4%BE%E0%A4%97%E0%A4%B0%E0%A4%BF%E0%A4%95%E0%A4%A4%E0%A4%BE.pdf",
            ),
            (
                'a"b\\c;d.pdf',
                "attachment; filename=\"a_b_c_d.pdf\"; filename*=UTF-8''a%22b%5Cc%3Bd.pdf",
            ),
        ],
    )
    def test_names_can_never_break_out_of_the_header(self, name: str, header: str) -> None:
        assert content_disposition(name, as_attachment=True) == header


class TestBucket:
    def test_an_existing_bucket_is_left_alone(
        self, storage: S3DocumentStorage, stubbed: Stubber
    ) -> None:
        stubbed.add_response("head_bucket", {}, {"Bucket": BUCKET})

        assert storage.ensure_bucket() is False

    def test_a_missing_bucket_is_created(
        self, storage: S3DocumentStorage, stubbed: Stubber
    ) -> None:
        stubbed.add_client_error("head_bucket", "404")
        stubbed.add_response("create_bucket", {}, {"Bucket": BUCKET})

        assert storage.ensure_bucket() is True

    def test_a_bucket_must_be_named(self, s3_settings: Settings) -> None:
        s3_settings.DOCUMENTS_BUCKET = ""

        with pytest.raises(ImproperlyConfigured):
            S3DocumentStorage()


def test_the_storage_comes_from_the_setting() -> None:
    """Tests point DOCUMENT_STORAGE at the in-memory fake."""
    assert isinstance(document_storage(), FakeDocumentStorage)


def test_the_bucket_command_reports_what_it_did(
    s3_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_documents_bucket` creates the bucket once, then says it exists."""
    answers = iter([True, False])
    monkeypatch.setattr(S3DocumentStorage, "ensure_bucket", lambda _self: next(answers))
    out = io.StringIO()

    call_command("create_documents_bucket", stdout=out)
    call_command("create_documents_bucket", stdout=out)

    assert out.getvalue().splitlines() == ["Documents bucket created.", "Documents bucket exists."]

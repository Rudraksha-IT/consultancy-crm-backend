"""The product's own logo (issue 65): uploaded on the Platform, shown on its login screens.

One logo for the whole product, kept in private storage under `platform/`, checked from its
bytes like an office logo. Anyone can read it on the Platform and Central login hosts (the login
screens show it before anyone logs in); only Platform Admins change it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from staff.models import Role
from tenancy.logo import MAX_LOGO_BYTES
from tenancy.subdomains import get_central_login_host
from tests.conftest import (
    ConsultancyFactory,
    JsonLogs,
    PlatformContext,
    api_client,
    use_access_token,
)
from tests.fakes import FakeDocumentStorage

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

BRAND_URL = "/api/platform/brand/"
LOGO_URL = "/api/platform/brand/logo/"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG_BYTES = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" onload="alert(1)">'
    b'<script>alert(2)</script><rect width="40" height="40" fill="#0b3d5c"/></svg>'
)
KIND_MESSAGE = "Upload a PNG, SVG, JPG or WebP image."


def a_file(
    content: bytes = PNG_BYTES, name: str = "logo.png", kind: str = "image/png"
) -> SimpleUploadedFile:
    """A file as the browser would send it."""
    return SimpleUploadedFile(name, content, content_type=kind)


def upload(client: APIClient, **fields: object) -> _MonkeyPatchedResponse:
    """POST a logo as a form, a PNG unless `fields` say otherwise."""
    body: dict[str, object] = {"file": a_file(), **fields}
    return client.post(LOGO_URL, body, format="multipart")


def brand(client: APIClient) -> Any:  # noqa: ANN401  # JSON
    """What the login screens get."""
    return client.get(BRAND_URL).json()


def stored_keys() -> list[str]:
    """Every file in the fake storage."""
    return sorted(FakeDocumentStorage.files)


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformContext:
    """The Platform, with a Platform Admin logged in on its client."""
    platform_site.log_in()
    return platform_site


class TestNoLogoYet:
    def test_the_login_screens_get_no_logo(self, platform_site: PlatformContext) -> None:
        assert brand(platform_site.new_client()) == {"logo": None}
        assert brand(api_client(get_central_login_host())) == {"logo": None}


class TestUpload:
    def test_a_png_is_stored_privately_and_shown_to_anyone(self, ops: PlatformContext) -> None:
        response = upload(ops.client)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"logo": {"url": "/api/platform/brand/logo/1/"}}
        [key] = stored_keys()
        assert key.startswith("platform/product-logo/1-")
        assert brand(ops.new_client()) == response.json()
        assert brand(api_client(get_central_login_host())) == response.json()
        file = ops.new_client().get("/api/platform/brand/logo/1/")
        assert file.status_code == status.HTTP_200_OK
        assert file.content == PNG_BYTES
        assert file["Content-Type"] == "image/png"
        assert file["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in file["Content-Security-Policy"]

    def test_the_file_is_served_on_the_central_login_host_too(self, ops: PlatformContext) -> None:
        upload(ops.client)

        file = api_client(get_central_login_host()).get("/api/platform/brand/logo/1/")

        assert file.status_code == status.HTTP_200_OK

    def test_a_new_logo_gets_a_new_address_and_the_old_file_goes(
        self, ops: PlatformContext
    ) -> None:
        upload(ops.client)

        response = upload(ops.client)

        assert response.json() == {"logo": {"url": "/api/platform/brand/logo/2/"}}
        assert len(stored_keys()) == 1
        assert ops.new_client().get("/api/platform/brand/logo/1/").status_code == 404

    def test_an_svg_is_cleaned_of_scripts(self, ops: PlatformContext) -> None:
        upload(ops.client, file=a_file(SVG_BYTES, "logo.svg", "image/svg+xml"))

        stored = FakeDocumentStorage.files[stored_keys()[0]].content
        assert b"script" not in stored
        assert b"onload" not in stored

    @pytest.mark.parametrize(
        "file",
        [
            a_file(b"GIF89a" + b"\x00" * 64, "logo.gif", "image/gif"),
            a_file(b"<html><script>alert(1)</script></html>", "logo.png", "image/png"),
            a_file(b"%PDF-1.7", "logo.png", "image/png"),
        ],
    )
    def test_anything_but_a_logo_is_refused(
        self, ops: PlatformContext, file: SimpleUploadedFile
    ) -> None:
        response = upload(ops.client, file=file)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"file": [KIND_MESSAGE]}
        assert stored_keys() == []

    def test_over_1_mb_is_refused(self, ops: PlatformContext) -> None:
        big = a_file(PNG_BYTES + b"\x00" * MAX_LOGO_BYTES)

        response = upload(ops.client, file=big)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_keys() == []

    def test_no_file_or_an_unknown_field_is_refused(self, ops: PlatformContext) -> None:
        assert ops.client.post(LOGO_URL, {}, format="multipart").status_code == 400
        assert upload(ops.client, name="x").status_code == 400
        assert ops.client.post(LOGO_URL, {"file": "x"}, format="json").status_code == 415

    def test_logged_with_ids_only(self, ops: PlatformContext, json_logs: JsonLogs) -> None:
        upload(ops.client)

        assert json_logs.with_message("Product logo changed: platform_admin_id=")


class TestRemove:
    def test_removing_shows_no_logo_and_deletes_the_file(self, ops: PlatformContext) -> None:
        upload(ops.client)

        response = ops.client.delete(LOGO_URL)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert brand(ops.new_client()) == {"logo": None}
        assert stored_keys() == []
        assert ops.new_client().get("/api/platform/brand/logo/1/").status_code == 404


class TestWhoCan:
    def test_nobody_logged_in_cannot_change_it(self, platform_site: PlatformContext) -> None:
        client = platform_site.new_client()

        assert upload(client).status_code == status.HTTP_401_UNAUTHORIZED
        assert client.delete(LOGO_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert stored_keys() == []

    def test_staff_can_never_change_it(
        self, platform_site: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        access = abc.tokens_for(abc.log_in_as(Role.ADMIN)).access
        on_platform = platform_site.new_client()
        use_access_token(on_platform, access)

        for client in (abc.client, on_platform):
            assert upload(client).status_code in {401, 403, 404}
        assert stored_keys() == []

    def test_the_central_login_host_only_reads_it(self, ops: PlatformContext) -> None:
        central = api_client(get_central_login_host())

        assert upload(central).status_code in {401, 403, 404, 405}
        assert central.delete(LOGO_URL).status_code in {401, 403, 404, 405}

    def test_an_office_host_has_none_of_it(
        self, ops: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        assert abc.new_client().get(BRAND_URL).status_code == status.HTTP_404_NOT_FOUND

    def test_only_the_methods_needed(self, ops: PlatformContext) -> None:
        assert ops.client.put(LOGO_URL, {}, format="json").status_code == 405
        assert ops.client.post(BRAND_URL, {}, format="json").status_code == 405
        assert ops.client.get(LOGO_URL).status_code == 405

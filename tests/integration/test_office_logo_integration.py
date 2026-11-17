"""The office logo (spec 99c), uploaded by an Admin in Configuration.

It is sent with the context and shown on the login screens, the sidebar and receipts. Without
one, the initials show.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.utils import schema_context, tenant_context
from rest_framework import status

from staff.models import Role, Staff, StaffStatus
from tenancy.logo import MAX_LOGO_BYTES
from tenancy.models import Consultancy
from tests.conftest import PUBLIC_SCHEMA, ConsultancyContext, ConsultancyFactory, PlatformContext
from tests.fakes import FakeDocumentStorage

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

CONTEXT_URL = "/api/context/"
CURRENT_CONSULTANCY_URL = "/api/consultancy/"
LOGO_URL = "/api/configuration/logo/"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
SVG_BYTES = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" onload="alert(1)">'
    b'<script>alert(2)</script><rect width="40" height="40" fill="#0b3d5c"/></svg>'
)
KIND_MESSAGE = "Upload a PNG, SVG, JPG or WebP image."
ONLY_ADMIN = {"detail": "Only an Admin can change the Configuration."}


def a_file(
    content: bytes = PNG_BYTES, name: str = "logo.png", kind: str = "image/png"
) -> SimpleUploadedFile:
    """A file as the browser would send it, with whatever type the browser claims."""
    return SimpleUploadedFile(name, content, content_type=kind)


def upload(office: ConsultancyContext, **fields: object) -> _MonkeyPatchedResponse:
    """POST a logo as a form, a PNG unless `fields` say otherwise."""
    body: dict[str, object] = {"file": a_file(), **fields}
    return office.client.post(LOGO_URL, body, format="multipart")


def logo_of(office: ConsultancyContext) -> Any:  # noqa: ANN401  # JSON
    """The logo the login screen gets."""
    return office.new_client().get(CURRENT_CONSULTANCY_URL).json()["logo"]


def stored_keys() -> list[str]:
    """Every file in the fake storage."""
    return sorted(FakeDocumentStorage.files)


def refreshed(office: ConsultancyContext) -> Consultancy:
    """The Consultancy as stored now."""
    with schema_context(PUBLIC_SCHEMA):
        consultancy: Consultancy = Consultancy.objects.get(pk=office.consultancy.pk)
        return consultancy


class TestWithoutALogo:
    def test_the_login_screen_and_the_context_have_no_logo(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.TEACHER)

        assert abc.client.get(CONTEXT_URL).json()["consultancy"]["logo"] is None
        assert logo_of(abc) is None

    def test_the_logo_file_is_not_found(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = abc.new_client().get("/api/consultancy/logo/1/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestAdminUploadsALogo:
    @pytest.mark.parametrize(
        ("content", "content_type"),
        [
            (PNG_BYTES, "image/png"),
            (JPEG_BYTES, "image/jpeg"),
            (WEBP_BYTES, "image/webp"),
        ],
    )
    def test_an_image_is_stored_and_served(
        self, make_consultancy: ConsultancyFactory, content: bytes, content_type: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = upload(abc, file=a_file(content, kind="text/html"))

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"logo": {"url": "/api/consultancy/logo/1/", "show_name": True}}
        served = abc.new_client().get("/api/consultancy/logo/1/")
        assert served.status_code == status.HTTP_200_OK
        assert served["Content-Type"] == content_type
        assert served["X-Content-Type-Options"] == "nosniff"
        assert served.content == content

    def test_the_context_and_the_login_screen_show_it(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)

        logo = {"url": "/api/consultancy/logo/1/", "show_name": True}
        consultancy = abc.client.get(CONTEXT_URL).json()["consultancy"]
        assert (consultancy["name"], consultancy["logo"]) == ("ABC Education", logo)
        assert logo_of(abc) == logo

    def test_an_svg_is_sanitised_before_it_is_stored(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = upload(abc, file=a_file(SVG_BYTES, "logo.svg", "image/svg+xml"))

        assert response.status_code == status.HTTP_200_OK
        served = abc.new_client().get("/api/consultancy/logo/1/")
        assert served["Content-Type"] == "image/svg+xml"
        assert "sandbox" in served["Content-Security-Policy"]
        assert b"<rect" in served.content
        assert b"alert" not in served.content
        assert b"script" not in served.content
        assert b"onload" not in served.content

    def test_it_is_kept_in_private_storage_under_the_consultancys_prefix(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        upload(abc)

        [key] = stored_keys()
        assert key.startswith(f"{abc.consultancy.schema_name}/office-logo/")
        assert key.endswith(".png")
        assert FakeDocumentStorage.files[key].content_type == "image/png"

    def test_changing_it_serves_the_new_one_at_a_new_address_and_drops_the_old(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)

        response = upload(abc, file=a_file(JPEG_BYTES, "new.jpg", "image/jpeg"))

        assert response.json()["logo"]["url"] == "/api/consultancy/logo/2/"
        assert abc.new_client().get("/api/consultancy/logo/1/").status_code == 404
        assert abc.new_client().get("/api/consultancy/logo/2/").content == JPEG_BYTES
        [key] = stored_keys()
        assert key.endswith(".jpg")

    def test_storage_being_down_keeps_the_old_logo(
        self, make_consultancy: ConsultancyFactory, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)
        fake_document_storage.is_down = True

        response = upload(abc, file=a_file(JPEG_BYTES))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json() == {"detail": "The logo couldn't be saved. Try again."}
        assert logo_of(abc)["url"] == "/api/consultancy/logo/1/"

    def test_storage_being_down_when_reading_answers_not_found(
        self, make_consultancy: ConsultancyFactory, fake_document_storage: type[FakeDocumentStorage]
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)
        fake_document_storage.is_down = True

        response = abc.new_client().get("/api/consultancy/logo/1/")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json() == {"detail": "Not found."}

    def test_an_old_file_that_cant_be_removed_still_lets_the_logo_go(
        self,
        make_consultancy: ConsultancyFactory,
        fake_document_storage: type[FakeDocumentStorage],
        json_logs: Any,  # noqa: ANN401  # fixture type
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)
        fake_document_storage.is_down = True

        response = abc.client.delete(LOGO_URL)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        fake_document_storage.is_down = False
        assert logo_of(abc) is None
        json_logs.only(f"Old office logo file not removed: consultancy_id={abc.consultancy.pk}")


class TestRefusedUploads:
    @pytest.mark.parametrize(
        "content",
        [
            b"%PDF-1.7 a document",
            b"GIF89a" + b"\x00" * 20,
            b"<html><script>alert(1)</script></html>",
            b'<!DOCTYPE svg [<!ENTITY a "a">]><svg xmlns="http://www.w3.org/2000/svg"/>',
            b"'; DROP TABLE tenancy_consultancy; --",
            b"<style>body{background:url(https://evil.test)}</style>",
        ],
    )
    def test_refuses_anything_but_a_png_svg_jpg_or_webp(
        self, make_consultancy: ConsultancyFactory, content: bytes
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = upload(abc, file=a_file(content, "logo.png", "image/png"))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"file": [KIND_MESSAGE]}
        assert logo_of(abc) is None
        assert stored_keys() == []

    def test_refuses_a_file_over_1_mb(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        too_big = PNG_BYTES + b"\x00" * (MAX_LOGO_BYTES - len(PNG_BYTES) + 1)

        response = upload(abc, file=a_file(too_big))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"file": ["The logo is too big. Upload at most 1 MB."]}
        assert logo_of(abc) is None

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"file": ""},
            {"file": "not a file"},
            {"file": a_file(b"")},
        ],
    )
    def test_refuses_a_missing_or_empty_file(
        self, make_consultancy: ConsultancyFactory, body: dict[str, object]
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(LOGO_URL, body, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"file": ["Choose a logo to upload."]}

    def test_refuses_unknown_fields_without_naming_them(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = upload(abc, **{"<script>": "x"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert logo_of(abc) is None

    def test_refuses_a_json_body(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(LOGO_URL, {"file": "x"}, format="json")

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE


class TestShowTheNameBesideIt:
    def test_the_admin_hides_and_shows_the_name(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)

        hidden = abc.client.patch(LOGO_URL, {"show_name": False}, format="json")

        assert hidden.status_code == status.HTTP_200_OK
        assert hidden.json() == {"logo": {"url": "/api/consultancy/logo/1/", "show_name": False}}
        assert logo_of(abc)["show_name"] is False
        shown = abc.client.patch(LOGO_URL, {"show_name": True}, format="json")
        assert shown.json()["logo"]["show_name"] is True

    def test_without_a_logo_there_is_nothing_to_change(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = abc.client.patch(LOGO_URL, {"show_name": False}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": "Upload a logo first."}

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"show_name": "false"},
            {"show_name": 0},
            {"show_name": None},
            {"show_name": ["x"]},
            {"show_name": True, "url": "https://evil.test/x.png"},
        ],
    )
    def test_refuses_anything_but_true_or_false(
        self, make_consultancy: ConsultancyFactory, body: dict[str, object]
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)

        response = abc.client.patch(LOGO_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "evil" not in response.content.decode()
        assert logo_of(abc) == {"url": "/api/consultancy/logo/1/", "show_name": True}


class TestRemoveTheLogo:
    def test_removing_returns_to_the_initials(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)

        response = abc.client.delete(LOGO_URL)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert logo_of(abc) is None
        assert abc.client.get(CONTEXT_URL).json()["consultancy"]["logo"] is None
        assert abc.new_client().get("/api/consultancy/logo/1/").status_code == 404
        assert stored_keys() == []

    def test_removing_when_there_is_none_is_fine(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        assert abc.client.delete(LOGO_URL).status_code == status.HTTP_204_NO_CONTENT

    def test_a_new_upload_after_removing_gets_a_new_address(
        self, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)
        abc.client.delete(LOGO_URL)

        assert upload(abc).json()["logo"]["url"] == "/api/consultancy/logo/2/"


class TestWhoMayChangeIt:
    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    @pytest.mark.parametrize("method", ["post", "patch", "delete"])
    def test_only_an_admin(
        self, make_consultancy: ConsultancyFactory, role: Role, method: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        admin = abc.add_staff(Role.ADMIN, "boss@abc.test")
        admin_client = abc.new_client()
        admin_client.credentials(HTTP_AUTHORIZATION=f"Bearer {abc.tokens_for(admin).access}")
        admin_client.post(LOGO_URL, {"file": a_file()}, format="multipart")
        abc.log_in_as(role)

        if method == "post":
            response = upload(abc, file=a_file(JPEG_BYTES))
        elif method == "patch":
            response = abc.client.patch(LOGO_URL, {"show_name": False}, format="json")
        else:
            response = abc.client.delete(LOGO_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN
        assert logo_of(abc) == {"url": "/api/consultancy/logo/1/", "show_name": True}

    @pytest.mark.parametrize("method", ["post", "patch", "delete"])
    def test_logged_out_is_refused(self, make_consultancy: ConsultancyFactory, method: str) -> None:
        abc = make_consultancy("ABC Education", "abcedu")

        response = getattr(abc.client, method)(LOGO_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_deactivated_admin_is_refused(self, make_consultancy: ConsultancyFactory) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        assert upload(abc).status_code in {status.HTTP_401_UNAUTHORIZED, 403}
        assert logo_of(abc) is None

    @pytest.mark.parametrize("method", ["get", "put"])
    def test_only_the_methods_it_needs(
        self, make_consultancy: ConsultancyFactory, method: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(LOGO_URL)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_the_logo_file_is_read_only(
        self, make_consultancy: ConsultancyFactory, method: str
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        abc.log_in_as(Role.ADMIN)
        upload(abc)

        response = getattr(abc.client, method)("/api/consultancy/logo/1/")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_not_on_the_platform_host(self, platform_site: PlatformContext) -> None:
        platform_site.log_in()

        assert platform_site.client.post(LOGO_URL).status_code == 404
        assert platform_site.client.get("/api/consultancy/logo/1/").status_code == 404


class TestLogging:
    def test_logs_ids_only(self, make_consultancy: ConsultancyFactory, json_logs: Any) -> None:  # noqa: ANN401  # fixture type
        abc = make_consultancy("ABC Education", "abcedu")
        admin = abc.log_in_as(Role.ADMIN)
        json_logs.clear()

        upload(abc, file=a_file(name="secret-plan.png"))
        abc.client.delete(LOGO_URL)

        changed = json_logs.only(
            f"Office logo changed: consultancy_id={abc.consultancy.pk}, staff_id={admin.pk}"
        )
        assert changed["level"] == "INFO"
        json_logs.only(
            f"Office logo removed: consultancy_id={abc.consultancy.pk}, staff_id={admin.pk}"
        )
        assert "secret-plan" not in json_logs.text()


def test_the_version_is_stored_beside_the_theme(make_consultancy: ConsultancyFactory) -> None:
    """The logo's details live on the Consultancy, in the public schema."""
    abc = make_consultancy("ABC Education", "abcedu")
    abc.log_in_as(Role.ADMIN)
    upload(abc)
    abc.client.patch(LOGO_URL, {"show_name": False}, format="json")

    consultancy = refreshed(abc)

    assert consultancy.logo_version == 1
    assert consultancy.logo_content_type == "image/png"
    assert consultancy.show_name_beside_logo is False

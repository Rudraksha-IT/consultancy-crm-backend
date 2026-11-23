"""Saved product logos (first live fixes, ticket 07).

Every product logo uploaded in Platform → Settings is kept, at most 10. A Platform Admin picks
one to use, or the app's own logo (no uploaded logo: the tab shows the app's own logo, the login
screens the initials), and removes uploads. Every change of the logo in use gives it a new
address, so the tab icon and every cache follow.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django_tenants.utils import schema_context
from rest_framework import status
from rest_framework.test import APIClient

from platform_admin.brand import MAX_SAVED_LOGOS
from staff.models import Role
from tenancy.subdomains import get_central_login_host
from tests.conftest import (
    PUBLIC_SCHEMA,
    ConsultancyFactory,
    PlatformContext,
    api_client,
    use_access_token,
)
from tests.fakes import FakeDocumentStorage

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

BRAND_URL = "/api/platform/brand/"
UPLOAD_URL = "/api/platform/brand/logo/"
LOGOS_URL = "/api/platform/brand/logos/"
IN_USE_URL = "/api/platform/brand/in-use/"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64
FULL_MESSAGE = "You have 10 saved logos. Remove one before uploading another."
UNKNOWN_LOGO_MESSAGE = "Pick one of the saved logos."
NO_SUCH_ID = "00000000-0000-4000-8000-000000000000"
# The last migration with a single product logo on ProductBrand.
BEFORE_SAVED_LOGOS = [("platform_admin", "0012_code_purpose_length")]


def a_png(content: bytes = PNG_BYTES) -> SimpleUploadedFile:
    """A PNG as the browser would send it."""
    return SimpleUploadedFile("logo.png", content, content_type="image/png")


def upload(client: APIClient, content: bytes = PNG_BYTES) -> _MonkeyPatchedResponse:
    """Upload a PNG logo."""
    return client.post(UPLOAD_URL, {"file": a_png(content)}, format="multipart")


def saved(client: APIClient) -> list[dict[str, Any]]:
    """The saved logos, newest first."""
    response = client.get(LOGOS_URL)
    assert response.status_code == status.HTTP_200_OK
    logos: list[dict[str, Any]] = response.json()["logos"]
    return logos


def use(client: APIClient, logo: object) -> _MonkeyPatchedResponse:
    """Pick a saved logo (its id) or the app's own logo (None)."""
    return client.put(IN_USE_URL, {"logo": logo}, format="json")


def brand(client: APIClient) -> Any:  # noqa: ANN401  # JSON
    """What the login screens and the tab icon get."""
    # The name beside it is checked in test_product_name_integration.py (ticket 15).
    return {"logo": client.get(BRAND_URL).json()["logo"]}


def call(client: APIClient, method: str, url: str) -> _MonkeyPatchedResponse:
    """One saved-logo call; a PUT carries a valid body."""
    if method == "put":
        return client.put(url, {"logo": None}, format="json")
    response: _MonkeyPatchedResponse = getattr(client, method)(url)
    return response


@pytest.fixture
def ops(platform_site: PlatformContext) -> PlatformContext:
    """The Platform, with a Platform Admin logged in on its client."""
    platform_site.log_in()
    return platform_site


class TestList:
    def test_none_saved_yet(self, ops: PlatformContext) -> None:
        response = ops.client.get(LOGOS_URL)

        assert response.json() == {"logos": [], "limit": MAX_SAVED_LOGOS}
        assert response["Cache-Control"] == "no-store"

    def test_every_upload_is_kept_newest_first_and_the_newest_is_in_use(
        self, ops: PlatformContext
    ) -> None:
        upload(ops.client)
        upload(ops.client, OTHER_PNG)

        logos = saved(ops.client)

        assert [logo["in_use"] for logo in logos] == [True, False]
        newest, oldest = logos
        assert set(newest) == {"id", "file_url", "uploaded_at", "in_use"}
        assert uuid.UUID(newest["id"]).version == 4
        assert newest["file_url"] == f"/api/platform/brand/logos/{newest['id']}/file/"
        assert ops.client.get(newest["file_url"]).content == OTHER_PNG
        assert ops.client.get(oldest["file_url"]).content == PNG_BYTES
        assert len(FakeDocumentStorage.files) == 2  # two uploads


class TestUploadLimit:
    def test_the_eleventh_upload_is_refused_and_nothing_changes(self, ops: PlatformContext) -> None:
        for _ in range(MAX_SAVED_LOGOS):
            assert upload(ops.client).status_code == status.HTTP_200_OK
        before = brand(ops.new_client())

        response = upload(ops.client, OTHER_PNG)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"file": [FULL_MESSAGE]}
        assert len(saved(ops.client)) == MAX_SAVED_LOGOS
        assert len(FakeDocumentStorage.files) == MAX_SAVED_LOGOS
        assert brand(ops.new_client()) == before


class TestPick:
    def test_picking_an_older_logo_shows_it_at_a_new_address(self, ops: PlatformContext) -> None:
        upload(ops.client)
        upload(ops.client, OTHER_PNG)
        older = saved(ops.client)[1]

        response = use(ops.client, older["id"])

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"logo": {"url": "/api/platform/brand/logo/3/"}}
        assert brand(ops.new_client()) == response.json()
        assert ops.new_client().get("/api/platform/brand/logo/3/").content == PNG_BYTES
        assert ops.new_client().get("/api/platform/brand/logo/2/").status_code == 404
        assert [logo["in_use"] for logo in saved(ops.client)] == [False, True]

    def test_picking_the_apps_own_logo_shows_no_uploaded_logo(self, ops: PlatformContext) -> None:
        upload(ops.client)

        response = use(ops.client, None)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"logo": None}
        assert brand(ops.new_client()) == {"logo": None}
        assert brand(api_client(get_central_login_host())) == {"logo": None}
        assert ops.new_client().get("/api/platform/brand/logo/1/").status_code == 404
        assert [logo["in_use"] for logo in saved(ops.client)] == [False]
        assert len(FakeDocumentStorage.files) == 1

    def test_going_back_to_an_upload_after_the_apps_own_gives_a_new_address(
        self, ops: PlatformContext
    ) -> None:
        upload(ops.client)
        logo_id = saved(ops.client)[0]["id"]
        use(ops.client, None)

        response = use(ops.client, logo_id)

        assert response.json() == {"logo": {"url": "/api/platform/brand/logo/3/"}}

    @pytest.mark.parametrize(
        "body",
        [
            {"logo": NO_SUCH_ID},
            {"logo": "not-an-id"},
            {"logo": 1},
            {"logo": True},
            {"logo": ["x"]},
            {"logo": "<script>alert(1)</script>"},
            {"logo": "'; DROP TABLE platform_admin_savedproductlogo; --"},
            {"logo": "A" * 5000},
        ],
    )
    def test_anything_but_a_saved_logo_or_null_is_refused(
        self, ops: PlatformContext, body: dict[str, object]
    ) -> None:
        upload(ops.client)
        before = brand(ops.new_client())

        response = ops.client.put(IN_USE_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"logo": [UNKNOWN_LOGO_MESSAGE]}
        assert brand(ops.new_client()) == before

    @pytest.mark.parametrize("body", [{}, {"logo": None, "extra": 1}, "x"])
    def test_a_missing_or_unknown_field_is_refused(
        self, ops: PlatformContext, body: object
    ) -> None:
        response = ops.client.put(IN_USE_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestRemove:
    def test_removing_an_upload_not_in_use_deletes_it_and_its_file(
        self, ops: PlatformContext
    ) -> None:
        upload(ops.client)
        upload(ops.client, OTHER_PNG)
        in_use, older = saved(ops.client)

        response = ops.client.delete(f"{LOGOS_URL}{older['id']}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert [logo["id"] for logo in saved(ops.client)] == [in_use["id"]]
        assert len(FakeDocumentStorage.files) == 1
        assert ops.client.get(older["file_url"]).status_code == 404
        assert brand(ops.new_client()) == {"logo": {"url": "/api/platform/brand/logo/2/"}}

    def test_removing_the_logo_in_use_goes_back_to_the_apps_own(self, ops: PlatformContext) -> None:
        upload(ops.client)
        logo_id = saved(ops.client)[0]["id"]

        response = ops.client.delete(f"{LOGOS_URL}{logo_id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert saved(ops.client) == []
        assert brand(ops.new_client()) == {"logo": None}
        assert FakeDocumentStorage.files == {}
        assert ops.new_client().get("/api/platform/brand/logo/1/").status_code == 404

    @pytest.mark.parametrize("logo_id", [NO_SUCH_ID, "1", "not-an-id", NO_SUCH_ID.upper()])
    def test_an_unknown_logo_is_not_found(self, ops: PlatformContext, logo_id: str) -> None:
        assert ops.client.delete(f"{LOGOS_URL}{logo_id}/").status_code == 404
        assert ops.client.get(f"{LOGOS_URL}{logo_id}/file/").status_code == 404


class TestWhoCan:
    def urls(self, ops: PlatformContext) -> list[tuple[str, str]]:
        """Every saved-logo call, for a logo that exists."""
        upload(ops.client)
        logo = saved(ops.client)[0]
        return [
            ("get", LOGOS_URL),
            ("put", IN_USE_URL),
            ("delete", f"{LOGOS_URL}{logo['id']}/"),
            ("get", logo["file_url"]),
        ]

    def test_nobody_logged_in_reaches_them(self, ops: PlatformContext) -> None:
        client = ops.new_client()

        for method, url in self.urls(ops):
            response = call(client, method, url)
            assert response.status_code == status.HTTP_401_UNAUTHORIZED, url
        assert len(saved(ops.client)) == 1

    def test_staff_never_reach_them(
        self, ops: PlatformContext, make_consultancy: ConsultancyFactory
    ) -> None:
        abc = make_consultancy("ABC Education", "abcedu")
        access = abc.tokens_for(abc.log_in_as(Role.ADMIN)).access
        on_platform = ops.new_client()
        use_access_token(on_platform, access)

        for method, url in self.urls(ops):
            for client in (abc.client, on_platform):
                response = call(client, method, url)
                assert response.status_code in {401, 403, 404}, url
        assert len(saved(ops.client)) == 1

    def test_the_central_login_host_has_none_of_them(self, ops: PlatformContext) -> None:
        central = api_client(get_central_login_host())

        for method, url in self.urls(ops):
            response = call(central, method, url)
            assert response.status_code in {401, 403, 404, 405}, url

    def test_only_the_methods_needed(self, ops: PlatformContext) -> None:
        logo_id = self.urls(ops)[2][1]

        assert ops.client.post(LOGOS_URL, {}, format="json").status_code == 405
        assert ops.client.get(IN_USE_URL).status_code == 405
        assert ops.client.post(IN_USE_URL, {}, format="json").status_code == 405
        assert ops.client.get(logo_id).status_code == 405
        assert ops.client.delete(UPLOAD_URL).status_code == 405


class TestMigration:
    """Today's single product logo becomes the one saved logo, in use, with its file kept."""

    @pytest.mark.parametrize("with_logo", [True, False])
    def test_the_existing_logo_moves_into_the_list(
        self, ops: PlatformContext, *, with_logo: bool
    ) -> None:
        key = "platform/product-logo/4-abc.png"
        with schema_context(PUBLIC_SCHEMA):
            executor = MigrationExecutor(connection)
            executor.migrate(BEFORE_SAVED_LOGOS)
            old_apps = executor.loader.project_state(BEFORE_SAVED_LOGOS).apps
            old_apps.get_model("platform_admin", "ProductBrand").objects.create(
                pk=1,
                logo_key=key if with_logo else "",
                logo_content_type="image/png" if with_logo else "",
                logo_version=4,
            )
            executor = MigrationExecutor(connection)
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())
        FakeDocumentStorage.files.clear()

        logos = saved(ops.client)

        if with_logo:
            assert [logo["in_use"] for logo in logos] == [True]
            assert brand(ops.new_client()) == {"logo": {"url": "/api/platform/brand/logo/4/"}}
        else:
            assert logos == []
            assert brand(ops.new_client()) == {"logo": None}

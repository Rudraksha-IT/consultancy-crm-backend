"""Document Verification, and linking a Document to an Application or Visa (spec 66, 68-69)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from django.db import IntegrityError, transaction
from django_tenants.utils import tenant_context
from rest_framework import status

from applications.statuses import ApplicationStatus
from documents.models import Document
from documents.serializers import DocumentUploadSerializer
from documents.verification import Verification
from staff.models import Role
from students.models import Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_applications_integration import make_application, make_course
from tests.integration.test_documents_integration import (
    COUNSELLOR_OR_ADMIN,
    a_file,
    checklist,
    documents_url,
    files_of,
    item_states,
    make_document,
    own_student,
    stored_documents,
    type_named,
    upload,
)
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    NOT_FOR_TEACHERS,
    NOT_YOUR_STUDENT,
    log_in,
    make_student,
    named_staff,
)
from tests.integration.test_visas_integration import make_visa, offer_application

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

REASON = "Only 3 months shown, need 6 months."
REASON_NEEDED = {"reason": ["Say why the document is rejected."]}
ONLY_REJECTED_HAS_A_REASON = {"reason": ["Only a rejected document has a reason."]}
ALREADY_CHECKED = {"detail": "This document has already been checked."}
NOT_NEWEST = {"detail": "A newer copy of this document has been uploaded."}
CHOOSE_APPLICATION = {"application": ["Choose one of this Student's applications."]}
CHOOSE_VISA = {"visa": ["Choose one of this Student's visas."]}
ONE_LINK = {"non_field_errors": ["Link it to an application or a visa, not both."]}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def verification_url(student: Student | int, document: Document | int) -> str:
    """Where a Document is marked correct or rejected."""
    pk = student if isinstance(student, int) else student.pk
    document_pk = document if isinstance(document, int) else document.pk
    return f"{documents_url(pk)}{document_pk}/verification/"


def judge(
    office: ConsultancyContext, student: Student | int, document: Document | int, **body: object
) -> _MonkeyPatchedResponse:
    """POST a Verification."""
    return office.client.post(verification_url(student, document), body, format="json")


def stored(office: ConsultancyContext, document: Document) -> Document:
    """The Document as stored now."""
    with tenant_context(office.consultancy):
        return Document.objects.get(pk=document.pk)


def passport_item(body: dict[str, Any]) -> dict[str, Any]:
    """The Passport line of a checklist answer."""
    items = body["items"]
    assert isinstance(items, list)
    found: dict[str, Any] = items[0]
    assert found["document_type"] == {"id": found["document_type"]["id"], "name": "Passport"}
    return found


def profile(office: ConsultancyContext, student: Student) -> dict[str, Any]:
    """GET the Student's profile."""
    body: dict[str, Any] = office.client.get(f"/api/students/{student.pk}/").json()
    return body


class TestVerifying:
    def test_the_assigned_counsellor_marks_a_document_correct(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="verified")

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, document).verification == Verification.VERIFIED
        body = response.json()
        assert item_states(body)[0] == ("Passport", "verified")
        assert passport_item(body)["document"]["verification"] == "verified"
        assert body["done"] == 1

    def test_rejecting_keeps_the_reason_and_the_type_is_no_longer_done(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="rejected", reason=f"  {REASON} ")

        assert response.status_code == status.HTTP_200_OK
        found = stored(abc, document)
        assert (found.verification, found.rejection_reason) == (Verification.REJECTED, REASON)
        body = response.json()
        assert item_states(body)[0] == ("Passport", "rejected")
        assert passport_item(body)["document"]["rejection_reason"] == REASON
        assert body["done"] == 0

    def test_a_reason_may_have_several_lines(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        judge(abc, student, document, verification="rejected", reason="Blurred.\nRetake it.")

        assert stored(abc, document).rejection_reason == "Blurred.\nRetake it."

    def test_a_pending_document_has_no_reason(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        make_document(abc, student)

        body = checklist(abc, student).json()

        assert passport_item(body)["document"]["rejection_reason"] == ""

    def test_a_new_copy_after_a_rejection_is_pending_and_counts_again(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        rejected = make_document(abc, student)
        judge(abc, student, rejected, verification="rejected", reason=REASON)

        body = upload(abc, student).json()

        old, new = stored_documents(abc, student)
        assert old.pk == rejected.pk
        assert (old.verification, new.verification) == (
            Verification.REJECTED,
            Verification.PENDING,
        )
        assert passport_item(body)["document"]["id"] == new.pk
        assert passport_item(body)["state"] == "waiting"
        assert body["done"] == 1

    def test_an_admin_judges_any_students_document(self, abc: ConsultancyContext) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        document = make_document(abc, student)
        abc.log_in_as(Role.ADMIN)

        response = judge(abc, student, document, verification="verified")

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize(
        ("role", "refusal"),
        [
            (Role.COUNSELLOR, NOT_YOUR_STUDENT),
            (Role.FRONT_DESK, COUNSELLOR_OR_ADMIN),
            (Role.TEACHER, NOT_FOR_TEACHERS),
        ],
    )
    def test_others_are_refused(
        self, abc: ConsultancyContext, role: Role, refusal: dict[str, str]
    ) -> None:
        student = make_student(abc, named_staff(abc, "Bikash Thapa"))
        document = make_document(abc, student)
        abc.log_in_as(role)

        response = judge(abc, student, document, verification="verified")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == refusal
        assert stored(abc, document).verification == Verification.PENDING

    def test_a_counsellor_loses_the_right_after_a_transfer(self, abc: ConsultancyContext) -> None:
        me = named_staff(abc, "Bikash Thapa")
        student = make_student(abc, named_staff(abc, "Gita Rai"))
        document = make_document(abc, student)
        log_in(abc, me)

        response = judge(abc, student, document, verification="verified")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_checked_document_cannot_be_judged_again(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        verified = make_document(abc, student, "Passport", Verification.VERIFIED)
        rejected = make_document(abc, student, "Citizenship", Verification.REJECTED)

        responses = [
            judge(abc, student, verified, verification="rejected", reason=REASON),
            judge(abc, student, rejected, verification="verified"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_400_BAD_REQUEST] * 2
        assert [r.json() for r in responses] == [ALREADY_CHECKED] * 2
        assert stored(abc, verified).verification == Verification.VERIFIED

    def test_only_the_newest_copy_can_be_judged(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        older = make_document(abc, student)
        make_document(abc, student)

        response = judge(abc, student, older, verification="verified")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == NOT_NEWEST
        assert stored(abc, older).verification == Verification.PENDING

    def test_another_students_document_is_not_found(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        mine, theirs = make_student(abc, me), make_student(abc, me)
        document = make_document(abc, theirs)

        response = judge(abc, mine, document, verification="verified")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert stored(abc, document).verification == Verification.PENDING

    def test_unknown_student_or_document_is_not_found(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        responses = [
            judge(abc, 999_999, document, verification="verified"),
            judge(abc, student, 999_999, verification="verified"),
        ]

        assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="verified")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logs_carry_ids_only(self, abc: ConsultancyContext, json_logs: JsonLogs) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        judge(abc, student, document, verification="rejected", reason="Sita's photo is blurred")

        [entry] = json_logs.with_message("Document checked")
        assert f"document_id={document.pk}" in entry["message"]
        assert "verification=rejected" in entry["message"]
        assert "blurred" not in json_logs.text()


class TestRefusedVerifications:
    def test_a_rejection_needs_a_reason(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        responses = [
            judge(abc, student, document, verification="rejected"),
            judge(abc, student, document, verification="rejected", reason="   "),
        ]

        assert [r.json() for r in responses] == [REASON_NEEDED] * 2
        assert stored(abc, document).verification == Verification.PENDING

    def test_marking_correct_takes_no_reason(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="verified", reason=REASON)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONLY_REJECTED_HAS_A_REASON

    @pytest.mark.parametrize("value", ["pending", "Verified", "", "approved", None, 1, True, []])
    def test_anything_but_correct_or_rejected_is_refused(
        self, abc: ConsultancyContext, value: object
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["verification"]
        assert stored(abc, document).verification == Verification.PENDING

    def test_a_missing_verification_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, reason=REASON)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "verification" in response.json()

    @pytest.mark.parametrize("value", [1, True, ["a"], {"a": 1}])
    def test_a_reason_that_isnt_text_is_refused(
        self, abc: ConsultancyContext, value: object
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="rejected", reason=value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["reason"]

    def test_an_over_long_reason_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="rejected", reason="x" * 501)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["reason"]
        ok = judge(abc, student, document, verification="rejected", reason="x" * 500)
        assert ok.status_code == status.HTTP_200_OK

    def test_control_characters_in_a_reason_are_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="rejected", reason="a\x00b")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert list(response.json()) == ["reason"]

    def test_an_unknown_field_is_refused_and_nothing_stored(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="verified", file_name="x.exe")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored(abc, document).verification == Verification.PENDING

    def test_a_form_body_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = abc.client.post(
            verification_url(student, document), {"verification": "verified"}, format="multipart"
        )

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("reason", INJECTION_STRINGS)
    def test_injection_shaped_reasons_are_kept_as_plain_text(
        self, abc: ConsultancyContext, reason: str
    ) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = judge(abc, student, document, verification="rejected", reason=reason)

        assert response.status_code == status.HTTP_200_OK
        assert stored(abc, document).rejection_reason == reason.strip()
        assert passport_item(response.json())["document"]["rejection_reason"] == reason.strip()


class TestLinking:
    def test_a_document_is_linked_to_one_of_the_students_applications(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        application = make_application(abc, student, make_course(abc, "Federation University"))

        response = upload(abc, student, application=str(application.pk))

        assert response.status_code == status.HTTP_201_CREATED
        [document] = stored_documents(abc, student)
        assert (document.application_id, document.visa_id) == (application.pk, None)
        assert passport_item(response.json())["document"]["link"] == {
            "kind": "application",
            "id": application.pk,
            "university": "Federation University",
        }

    def test_a_document_is_linked_to_one_of_the_students_visas(
        self, abc: ConsultancyContext
    ) -> None:
        student = own_student(abc)
        visa = make_visa(abc, student, offer_application(abc, student, "Deakin University"))

        response = upload(abc, student, visa=str(visa.pk))

        assert response.status_code == status.HTTP_201_CREATED
        [document] = stored_documents(abc, student)
        assert (document.application_id, document.visa_id) == (None, visa.pk)
        assert passport_item(response.json())["document"]["link"] == {
            "kind": "visa",
            "id": visa.pk,
            "university": "Deakin University",
        }

    def test_without_a_link_it_belongs_to_the_student_only(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        body = upload(abc, student).json()

        assert passport_item(body)["document"]["link"] is None

    def test_another_students_application_or_visa_is_refused(self, abc: ConsultancyContext) -> None:
        me = abc.log_in_as(Role.COUNSELLOR)
        mine, theirs = make_student(abc, me), make_student(abc, me)
        their_application = offer_application(abc, theirs)
        their_visa = make_visa(abc, theirs, their_application)

        responses = [
            upload(abc, mine, application=str(their_application.pk)),
            upload(abc, mine, visa=str(their_visa.pk)),
        ]

        assert [r.json() for r in responses] == [CHOOSE_APPLICATION, CHOOSE_VISA]
        assert stored_documents(abc, mine) == []

    @pytest.mark.parametrize("value", ["abc", "-1", "1.5", "999999", "\u0661", "9" * 30])
    def test_a_bad_link_is_refused(self, abc: ConsultancyContext, value: str) -> None:
        student = own_student(abc)

        responses = [upload(abc, student, application=value), upload(abc, student, visa=value)]

        assert [r.json() for r in responses] == [CHOOSE_APPLICATION, CHOOSE_VISA]
        assert stored_documents(abc, student) == []

    def test_an_empty_link_in_the_form_means_no_link(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)

        response = upload(abc, student, application="", visa="")

        assert response.status_code == status.HTTP_201_CREATED
        [document] = stored_documents(abc, student)
        assert (document.application_id, document.visa_id) == (None, None)

    def test_both_links_at_once_are_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        application = offer_application(abc, student)
        visa = make_visa(abc, student, application)

        response = upload(abc, student, application=str(application.pk), visa=str(visa.pk))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == ONE_LINK
        assert stored_documents(abc, student) == []

    def test_a_link_sent_twice_is_refused(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        application = make_application(abc, student, make_course(abc))

        response = abc.client.post(
            documents_url(student),
            {
                "document_type": str(type_named(abc, "Passport").pk),
                "file": a_file(),
                "application": [str(application.pk), str(application.pk)],
            },
            format="multipart",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored_documents(abc, student) == []

    def test_the_application_shows_its_documents(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        application = make_application(abc, student, make_course(abc), ApplicationStatus.OFFER)
        upload(abc, student, application=str(application.pk))
        upload(abc, student)
        [linked, _] = stored_documents(abc, student)

        body = profile(abc, student)

        [shown] = body["applications"]
        [file] = files_of(abc, linked)
        assert shown["documents"] == [
            {
                "id": linked.pk,
                "document_type": {"id": linked.document_type_id, "name": "Passport"},
                "files": [{"id": file.pk, "file_name": "passport.pdf"}],
                "verification": "pending",
            }
        ]

    def test_the_visa_shows_its_documents(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        application = offer_application(abc, student)
        visa = make_visa(abc, student, application)
        upload(abc, student, visa=str(visa.pk))
        [linked] = stored_documents(abc, student)
        judge(abc, student, linked, verification="verified")

        body = profile(abc, student)

        [shown_visa] = body["visas"]
        [shown_application] = body["applications"]
        assert [d["id"] for d in shown_visa["documents"]] == [linked.pk]
        assert shown_visa["documents"][0]["verification"] == "verified"
        assert shown_application["documents"] == []

    def test_every_student_role_sees_the_links(self, abc: ConsultancyContext) -> None:
        student = own_student(abc)
        application = make_application(abc, student, make_course(abc))
        upload(abc, student, application=str(application.pk))
        abc.log_in_as(Role.FRONT_DESK)

        body = profile(abc, student)

        assert len(body["applications"][0]["documents"]) == 1


class TestMethods:
    @pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
    def test_verification_answers_only_post(self, abc: ConsultancyContext, method: str) -> None:
        student = own_student(abc)
        document = make_document(abc, student)

        response = getattr(abc.client, method)(verification_url(student, document))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_not_on_the_platform_host(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        student = make_student(abc)
        document = make_document(abc, student)
        platform_site.log_in()

        response = platform_site.client.post(
            verification_url(student, document), {"verification": "verified"}, format="json"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestLinkSerializer:
    def test_a_link_field_shows_its_record_by_id(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        application = offer_application(abc, student)

        with tenant_context(abc.consultancy):
            field = DocumentUploadSerializer(context={"student": student}).fields["application"]

            assert field.to_representation(application) == application.pk


class TestModels:
    def test_the_database_refuses_a_document_linked_to_both(self, abc: ConsultancyContext) -> None:
        student = make_student(abc)
        application = offer_application(abc, student)
        visa = make_visa(abc, student, application)
        document = make_document(abc, student)

        with tenant_context(abc.consultancy), pytest.raises(IntegrityError), transaction.atomic():
            Document.objects.filter(pk=document.pk).update(application=application, visa=visa)

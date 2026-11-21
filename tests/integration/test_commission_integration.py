"""Commission: what Universities pay the office per Student (spec 76d, 76e; issue 37)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest
from django.utils import timezone
from django_tenants.utils import tenant_context
from rest_framework import status

from catalogue.models import University
from commission.models import Commission
from modules.services import switch_modules
from modules.types import Module
from staff.models import Role, Staff, StaffStatus
from students.models import Stage, Student
from tests.conftest import ConsultancyContext, ConsultancyFactory, JsonLogs, PlatformContext
from tests.integration.test_catalogue_integration import make_university
from tests.integration.test_stage_and_counsellor_integration import (
    INJECTION_STRINGS,
    make_student,
    named_staff,
)

if TYPE_CHECKING:
    from rest_framework.response import _MonkeyPatchedResponse

LIST_URL = "/api/accounts/commission/"
RECEIVED_URL = "/api/accounts/commission/received/"
AMOUNT_MESSAGE = "Enter a whole amount from NPR 1 to NPR 10,00,00,000."
STUDENT_MESSAGE = "Choose a Student."
UNIVERSITY_MESSAGE = "Choose a University from the Catalogue."
INTAKE_MESSAGE = "Choose a month and year from 2000 on."
STATUS_MESSAGE = "Choose a commission status."
ONLY_ADMIN = {"detail": "Only an Admin can see or change Commission."}


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in, with Accounts on."""
    office = make_consultancy("ABC Education", "abcedu")
    switch_modules(office.consultancy, {Module.ACCOUNTS})
    return office


def status_url(commission: Commission | int) -> str:
    """One Commission's status."""
    pk = commission if isinstance(commission, int | str) else commission.public_id
    return f"{LIST_URL}{pk}/status/"


def make_commission(
    office: ConsultancyContext,
    student: Student,
    university: University,
    by: Staff,
    **fields: object,
) -> Commission:
    """Store a Commission directly."""
    details: dict[str, object] = {
        "intake": "2027-02",
        "amount": 290000,
        "status": "waiting_for_enrolment",
        **fields,
    }
    with tenant_context(office.consultancy):
        return Commission.objects.create(
            student=student,
            university=university,
            recorded_by=by,
            recorded_by_name=by.name,
            **details,
        )


def stored(office: ConsultancyContext) -> list[tuple[int, int, str, int, str]]:
    """Every Commission as stored: (student, university, intake, amount, status), oldest first."""
    with tenant_context(office.consultancy):
        return list(
            Commission.objects.order_by("pk").values_list(
                "student_id", "university_id", "intake", "amount", "status"
            )
        )


def body_for(student: Student, university: University, **fields: object) -> dict[str, object]:
    """A good body for a new Commission."""
    return {
        "student": str(student.public_id),
        "university": str(university.public_id),
        "intake": "2027-02",
        "amount": 290000,
        **fields,
    }


def set_status(
    office: ConsultancyContext, commission: Commission, value: object
) -> _MonkeyPatchedResponse:
    """PUT a new status."""
    return office.client.put(status_url(commission), {"status": value}, format="json")


@pytest.fixture
def setup(abc: ConsultancyContext) -> tuple[Staff, Student, University]:
    """The logged-in Admin, a Student and a University."""
    admin = abc.log_in_as(Role.ADMIN)
    student = make_student(abc, None, Stage.APPLYING, name="Aarav Sharma")
    return admin, student, make_university(abc, "Federation University")


class TestList:
    def test_an_admin_sees_every_commission_newest_first_with_totals(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, federation = setup
        deakin = make_university(abc, "Deakin University")
        sita = make_student(abc, name="Sita Karki")
        waiting = make_commission(abc, student, federation, admin, amount=260000)
        claimed = make_commission(abc, sita, deakin, admin, status="claim_sent", amount=290000)
        received = make_commission(
            abc,
            sita,
            federation,
            admin,
            status="received",
            amount=340000,
            intake="2027-01",
            received_on=date(2026, 10, 20),
        )

        response = abc.client.get(LIST_URL)

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {
            "count": 3,
            "page": 1,
            "page_size": 25,
            "totals": {
                "waiting_for_enrolment": 260000,
                "claim_sent": 290000,
                "received": 340000,
                "to_come": 550000,
            },
            "results": [
                {
                    "id": str(received.public_id),
                    "student": {"id": str(sita.public_id), "name": "Sita Karki"},
                    "university": {
                        "id": str(federation.public_id),
                        "name": "Federation University",
                    },
                    "intake": "2027-01",
                    "amount": 340000,
                    "status": "received",
                    "received_on": "2026-10-20",
                },
                {
                    "id": str(claimed.public_id),
                    "student": {"id": str(sita.public_id), "name": "Sita Karki"},
                    "university": {"id": str(deakin.public_id), "name": "Deakin University"},
                    "intake": "2027-02",
                    "amount": 290000,
                    "status": "claim_sent",
                    "received_on": None,
                },
                {
                    "id": str(waiting.public_id),
                    "student": {"id": str(student.public_id), "name": "Aarav Sharma"},
                    "university": {
                        "id": str(federation.public_id),
                        "name": "Federation University",
                    },
                    "intake": "2027-02",
                    "amount": 260000,
                    "status": "waiting_for_enrolment",
                    "received_on": None,
                },
            ],
        }

    def test_nothing_yet(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        body = abc.client.get(LIST_URL).json()

        assert body["count"] == 0
        assert body["totals"] == {
            "waiting_for_enrolment": 0,
            "claim_sent": 0,
            "received": 0,
            "to_come": 0,
        }

    def test_pages_of_25_with_totals_for_everything(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, university = setup
        for _ in range(26):
            make_commission(abc, student, university, admin, amount=10)

        second = abc.client.get(LIST_URL, {"page": "2"}).json()

        assert (len(second["results"]), second["count"]) == (1, 26)
        assert second["totals"]["to_come"] == 260

    def test_a_page_past_the_end_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        assert abc.client.get(LIST_URL, {"page": "2"}).status_code == 404

    @pytest.mark.parametrize(
        "query", [{"page": "0"}, {"page": "x"}, {"page": "<script>"}, {"status": "received"}]
    )
    def test_refused_queries(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(LIST_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        response = abc.client.get(LIST_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN

    def test_the_students_counsellor_is_refused_too(self, abc: ConsultancyContext) -> None:
        counsellor = named_staff(abc, "Bikash Thapa")
        make_student(abc, counsellor, Stage.APPLYING)
        abc.log_in_as(Role.COUNSELLOR)

        assert abc.client.get(LIST_URL).status_code == status.HTTP_403_FORBIDDEN

    def test_a_deactivated_admin_is_refused(self, abc: ConsultancyContext) -> None:
        admin = abc.log_in_as(Role.ADMIN)
        with tenant_context(abc.consultancy):
            Staff.objects.filter(pk=admin.pk).update(status=StaffStatus.DEACTIVATED)

        assert abc.client.get(LIST_URL).status_code in {401, 403}

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_only_get_and_post_on_the_list(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(LIST_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logged_out_is_refused(self, abc: ConsultancyContext) -> None:
        assert abc.client.get(LIST_URL).status_code == status.HTTP_401_UNAUTHORIZED
        assert abc.client.get(RECEIVED_URL).status_code == status.HTTP_401_UNAUTHORIZED


class TestAddCommission:
    def test_an_admin_adds_commission_waiting_for_enrolment(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, university = setup

        response = abc.client.post(LIST_URL, body_for(student, university), format="json")

        assert response.status_code == status.HTTP_201_CREATED
        with tenant_context(abc.consultancy):
            commission = Commission.objects.get()
        assert response.json() == {
            "id": str(commission.public_id),
            "student": {"id": str(student.public_id), "name": "Aarav Sharma"},
            "university": {"id": str(university.public_id), "name": "Federation University"},
            "intake": "2027-02",
            "amount": 290000,
            "status": "waiting_for_enrolment",
            "received_on": None,
        }
        assert commission.recorded_by_id == admin.pk

    def test_a_lost_student_can_still_have_commission(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        _, _, university = setup
        lost = make_student(abc, None, Stage.LOST, name="Hari Lama")

        response = abc.client.post(LIST_URL, body_for(lost, university), format="json")

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused_and_nothing_is_stored(
        self, abc: ConsultancyContext, role: Role
    ) -> None:
        student = make_student(abc)
        university = make_university(abc)
        abc.log_in_as(role)

        response = abc.client.post(LIST_URL, body_for(student, university), format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json() == ONLY_ADMIN
        assert stored(abc) == []

    def test_logs_ids_only(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        json_logs: JsonLogs,
    ) -> None:
        admin, student, university = setup
        json_logs.clear()

        abc.client.post(LIST_URL, body_for(student, university, amount=43210), format="json")

        with tenant_context(abc.consultancy):
            commission_id = Commission.objects.get().pk
        [line] = json_logs.with_message("Commission added")
        assert line["message"] == (
            f"Commission added: commission_id={commission_id}, student_id={student.pk}, "
            f"by_staff_id={admin.pk}"
        )
        assert "43210" not in json_logs.text()
        assert "Aarav" not in json_logs.text()


class TestRefusedCommissionInput:
    @pytest.mark.parametrize(
        "amount", [0, -5, 100_000_001, 12.5, "290000", True, None, [1], {"n": 1}]
    )
    def test_bad_amounts(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        amount: object,
    ) -> None:
        _, student, university = setup

        response = abc.client.post(
            LIST_URL, body_for(student, university, amount=amount), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"amount": [AMOUNT_MESSAGE]}
        assert stored(abc) == []

    @pytest.mark.parametrize("student", [999_999, 0, "1", True, None, [1], "<script>"])
    def test_bad_students(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        student: object,
    ) -> None:
        _, real, university = setup

        response = abc.client.post(
            LIST_URL, {**body_for(real, university), "student": student}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"student": [STUDENT_MESSAGE]}

    @pytest.mark.parametrize("university", [999_999, "1", False, None, [1]])
    def test_bad_universities(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        university: object,
    ) -> None:
        _, student, real = setup

        response = abc.client.post(
            LIST_URL, {**body_for(student, real), "university": university}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"university": [UNIVERSITY_MESSAGE]}

    def test_an_archived_university_is_refused(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        _, student, _ = setup
        archived = make_university(abc, "Old University", archived=True)

        response = abc.client.post(LIST_URL, body_for(student, archived), format="json")

        assert response.json() == {"university": [UNIVERSITY_MESSAGE]}

    @pytest.mark.parametrize(
        "intake", ["1999-12", "2027-13", "2027-2", "Feb 2027", "2027-02-01", "", None, 202702]
    )
    def test_bad_intakes(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        intake: object,
    ) -> None:
        _, student, university = setup

        response = abc.client.post(
            LIST_URL, body_for(student, university, intake=intake), format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"intake": [INTAKE_MESSAGE]}

    def test_missing_fields(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(LIST_URL, {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {
            "student": [STUDENT_MESSAGE],
            "university": [UNIVERSITY_MESSAGE],
            "intake": [INTAKE_MESSAGE],
            "amount": [AMOUNT_MESSAGE],
        }

    @pytest.mark.parametrize("field", ["id", "status", "received_on", "recorded_by", "recorded_at"])
    def test_unknown_fields_are_refused_and_never_stored(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        field: str,
    ) -> None:
        _, student, university = setup

        response = abc.client.post(
            LIST_URL, {**body_for(student, university), field: "received"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"detail": ["Unknown field."]}
        assert stored(abc) == []

    @pytest.mark.parametrize("body", [[{"amount": 1}], "commission", 1])
    def test_a_body_that_is_not_an_object(self, abc: ConsultancyContext, body: object) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.post(LIST_URL, body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert stored(abc) == []

    def test_a_form_body_is_refused(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        _, student, university = setup

        response = abc.client.post(LIST_URL, body_for(student, university))

        assert response.status_code == status.HTTP_415_UNSUPPORTED_MEDIA_TYPE

    @pytest.mark.parametrize("text", INJECTION_STRINGS)
    def test_injection_shaped_names_come_back_as_plain_text(
        self, abc: ConsultancyContext, text: str
    ) -> None:
        abc.log_in_as(Role.ADMIN)
        student = make_student(abc, name=text)
        university = make_university(abc, text)

        response = abc.client.post(LIST_URL, body_for(student, university), format="json")

        assert response.json()["student"]["name"] == text
        assert response.json()["university"]["name"] == text


class TestStatus:
    def test_claim_sent_then_received_sets_the_day_received(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, university = setup
        commission = make_commission(abc, student, university, admin)

        claimed = set_status(abc, commission, "claim_sent")
        received = set_status(abc, commission, "received")

        assert claimed.status_code == status.HTTP_200_OK
        assert (claimed.json()["status"], claimed.json()["received_on"]) == ("claim_sent", None)
        assert received.json()["status"] == "received"
        assert received.json()["received_on"] == timezone.localdate().isoformat()

    def test_marking_received_again_keeps_the_first_day(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, university = setup
        commission = make_commission(
            abc, student, university, admin, status="received", received_on=date(2026, 10, 20)
        )

        response = set_status(abc, commission, "received")

        assert response.json()["received_on"] == "2026-10-20"

    def test_moving_back_from_received_clears_the_day(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, university = setup
        commission = make_commission(
            abc, student, university, admin, status="received", received_on=date(2026, 10, 20)
        )

        response = set_status(abc, commission, "claim_sent")

        assert response.json()["received_on"] is None

    @pytest.mark.parametrize("value", ["Received", "paid", "", None, 1, ["received"]])
    def test_bad_statuses_change_nothing(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        value: object,
    ) -> None:
        admin, student, university = setup
        commission = make_commission(abc, student, university, admin)

        response = set_status(abc, commission, value)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json() == {"status": [STATUS_MESSAGE]}
        assert stored(abc)[0][4] == "waiting_for_enrolment"

    def test_only_the_status_can_be_sent(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, university = setup
        commission = make_commission(abc, student, university, admin)

        response = abc.client.put(
            status_url(commission), {"status": "received", "amount": 1}, format="json"
        )

        assert response.json() == {"detail": ["Unknown field."]}
        assert stored(abc)[0][3:] == (290000, "waiting_for_enrolment")

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_other_roles_are_refused(self, abc: ConsultancyContext, role: Role) -> None:
        admin = named_staff(abc, "Anita Admin", Role.ADMIN)
        commission = make_commission(abc, make_student(abc), make_university(abc), admin)
        abc.log_in_as(role)

        response = set_status(abc, commission, "received")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert stored(abc)[0][4] == "waiting_for_enrolment"

    def test_an_unknown_commission_is_not_found(self, abc: ConsultancyContext) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.put(status_url(999_999), {"status": "received"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
    def test_put_only(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        method: str,
    ) -> None:
        admin, student, university = setup
        commission = make_commission(abc, student, university, admin)

        response = getattr(abc.client, method)(status_url(commission), {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_logs_ids_and_the_status_only(
        self,
        abc: ConsultancyContext,
        setup: tuple[Staff, Student, University],
        json_logs: JsonLogs,
    ) -> None:
        admin, student, university = setup
        commission = make_commission(abc, student, university, admin)
        json_logs.clear()

        set_status(abc, commission, "received")

        [line] = json_logs.with_message("Commission status changed")
        assert line["message"] == (
            f"Commission status changed: commission_id={commission.pk}, status=received, "
            f"by_staff_id={admin.pk}"
        )


class TestReceived:
    def test_the_received_commission_between_two_days(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, university = setup
        for day, amount in [(1, 100), (15, 200), (31, 400)]:
            make_commission(
                abc,
                student,
                university,
                admin,
                status="received",
                amount=amount,
                received_on=date(2026, 10, day),
            )
        make_commission(abc, student, university, admin, status="claim_sent", amount=9)
        make_commission(
            abc,
            student,
            university,
            admin,
            status="received",
            amount=8,
            received_on=date(2026, 9, 30),
        )

        response = abc.client.get(RECEIVED_URL, {"from": "2026-10-01", "to": "2026-10-15"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"total": 300, "count": 2}
        assert abc.client.get(RECEIVED_URL, {"from": "2026-10-01"}).json() == {
            "total": 700,
            "count": 3,
        }
        assert abc.client.get(RECEIVED_URL).json() == {"total": 708, "count": 4}

    @pytest.mark.parametrize(
        "query",
        [
            {"from": "2026-10-32"},
            {"to": "x"},
            {"from": "2026-10-15", "to": "2026-10-01"},
            {"from": "<script>"},
            {"student": "1"},
        ],
    )
    def test_refused_queries(self, abc: ConsultancyContext, query: dict[str, str]) -> None:
        abc.log_in_as(Role.ADMIN)

        response = abc.client.get(RECEIVED_URL, query)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "<script>" not in response.content.decode()

    @pytest.mark.parametrize("role", [Role.COUNSELLOR, Role.FRONT_DESK, Role.TEACHER])
    def test_only_admins(self, abc: ConsultancyContext, role: Role) -> None:
        abc.log_in_as(role)

        assert abc.client.get(RECEIVED_URL).json() == ONLY_ADMIN

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_get_only(self, abc: ConsultancyContext, method: str) -> None:
        abc.log_in_as(Role.ADMIN)

        response = getattr(abc.client, method)(RECEIVED_URL, {}, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_today_counts(
        self, abc: ConsultancyContext, setup: tuple[Staff, Student, University]
    ) -> None:
        admin, student, university = setup
        commission = make_commission(abc, student, university, admin)
        set_status(abc, commission, "received")
        today = timezone.localdate()
        yesterday = (today - timedelta(days=1)).isoformat()

        body = abc.client.get(RECEIVED_URL, {"from": yesterday, "to": today.isoformat()}).json()

        assert body == {"total": 290000, "count": 1}


class TestAccountsModuleOff:
    @pytest.fixture
    def office(self, make_consultancy: ConsultancyFactory) -> ConsultancyContext:
        return make_consultancy("XYZ Overseas", "xyzoverseas")

    @pytest.mark.parametrize("role", list(Role))
    def test_every_commission_endpoint_is_not_found_for_every_role(
        self, office: ConsultancyContext, role: Role
    ) -> None:
        student, university = make_student(office), make_university(office)
        admin = named_staff(office, "Anita Admin", Role.ADMIN)
        commission = make_commission(office, student, university, admin)
        office.log_in_as(role)

        answers = [
            office.client.get(LIST_URL),
            office.client.post(LIST_URL, body_for(student, university), format="json"),
            set_status(office, commission, "received"),
            office.client.get(RECEIVED_URL),
        ]

        assert [answer.status_code for answer in answers] == [404] * 4
        assert stored(office)[0][4] == "waiting_for_enrolment"
        assert len(stored(office)) == 1

    def test_switching_back_on_brings_the_commission_back(
        self, office: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        admin = office.log_in_as(Role.ADMIN)
        make_commission(office, make_student(office), make_university(office), admin)
        assert office.client.get(LIST_URL).status_code == status.HTTP_404_NOT_FOUND

        switch_modules(office.consultancy, {Module.ACCOUNTS})

        assert office.client.get(LIST_URL).json()["totals"]["to_come"] == 290000

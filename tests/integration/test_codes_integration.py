"""The one-time Code service shared by Invites (07), password resets (08) and the Platform (63)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from django_tenants.utils import schema_context, tenant_context

from codes.exceptions import CodeLimitReachedError, InvalidCodeError, WrongCodeScopeError
from codes.models import OneTimeCode
from codes.services import CODE_LIFETIME, MAX_ATTEMPTS, MAX_CODES_PER_HOUR, issue_code, use_code
from codes.types import CodePurpose
from platform_admin.models import PlatformAdminCode
from staff.models import Role, Staff
from tests.conftest import PUBLIC_SCHEMA, ConsultancyContext, ConsultancyFactory, PlatformContext


@pytest.fixture
def abc(make_consultancy: ConsultancyFactory) -> ConsultancyContext:
    """The Consultancy every test here works in."""
    return make_consultancy("ABC Education", "abcedu")


def wrong(code: str) -> str:
    """A different 6-digit Code."""
    return f"{(int(code) + 1) % 1_000_000:06d}"


def age_codes(staff: Staff, by: timedelta) -> None:
    """Pretend every Code of `staff` was issued `by` earlier."""
    for record in OneTimeCode.objects.filter(staff=staff):
        OneTimeCode.objects.filter(pk=record.pk).update(
            created_at=record.created_at - by, expires_at=record.expires_at - by
        )


class TestIssueCode:
    def test_a_code_is_six_digits(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            codes = {issue_code(staff, CodePurpose.RESET) for _ in range(MAX_CODES_PER_HOUR)}

        assert all(len(code) == 6 and code.isdigit() for code in codes)

    def test_the_code_itself_is_never_stored(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)
            record = OneTimeCode.objects.get(staff=staff)

        assert code not in record.code_hash

    def test_a_code_is_valid_for_thirty_minutes(self, abc: ConsultancyContext) -> None:
        assert timedelta(minutes=30) == CODE_LIFETIME
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            issue_code(staff, CodePurpose.RESET)
            record = OneTimeCode.objects.get(staff=staff)

        assert record.expires_at - record.created_at == CODE_LIFETIME

    def test_too_many_codes_in_an_hour_are_refused(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            for _ in range(MAX_CODES_PER_HOUR):
                issue_code(staff, CodePurpose.RESET)

            with pytest.raises(CodeLimitReachedError):
                issue_code(staff, CodePurpose.RESET)

    def test_codes_older_than_an_hour_no_longer_count(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            for _ in range(MAX_CODES_PER_HOUR):
                issue_code(staff, CodePurpose.RESET)
            age_codes(staff, timedelta(hours=1, seconds=1))

            assert issue_code(staff, CodePurpose.RESET)


class TestUseCode:
    def test_the_right_code_works_once(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)

            use_code(staff, CodePurpose.RESET, code)
            with pytest.raises(InvalidCodeError):
                use_code(staff, CodePurpose.RESET, code)

    def test_checking_without_using_leaves_the_code_working(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)

            use_code(staff, CodePurpose.RESET, code, consume=False)
            use_code(staff, CodePurpose.RESET, code)

    def test_a_wrong_code_is_refused(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)

            with pytest.raises(InvalidCodeError):
                use_code(staff, CodePurpose.RESET, wrong(code))

    def test_an_expired_code_is_refused(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)
            age_codes(staff, CODE_LIFETIME + timedelta(seconds=1))

            with pytest.raises(InvalidCodeError):
                use_code(staff, CodePurpose.RESET, code)

    def test_a_code_just_inside_thirty_minutes_still_works(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)
            age_codes(staff, CODE_LIFETIME - timedelta(seconds=5))

            use_code(staff, CodePurpose.RESET, code)

    def test_the_code_stops_working_after_too_many_wrong_tries(
        self, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)
            for _ in range(MAX_ATTEMPTS):
                with pytest.raises(InvalidCodeError):
                    use_code(staff, CodePurpose.RESET, wrong(code))

            with pytest.raises(InvalidCodeError):
                use_code(staff, CodePurpose.RESET, code)

    def test_one_wrong_try_short_of_the_limit_still_lets_the_right_code_work(
        self, abc: ConsultancyContext
    ) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)
            for _ in range(MAX_ATTEMPTS - 1):
                with pytest.raises(InvalidCodeError):
                    use_code(staff, CodePurpose.RESET, wrong(code))

            use_code(staff, CodePurpose.RESET, code)

    def test_a_new_code_replaces_the_old_one(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            old = issue_code(staff, CodePurpose.RESET)
            new = issue_code(staff, CodePurpose.RESET)
            if old == new:
                new = issue_code(staff, CodePurpose.RESET)

            with pytest.raises(InvalidCodeError):
                use_code(staff, CodePurpose.RESET, old)
            use_code(staff, CodePurpose.RESET, new)

    def test_a_code_works_only_for_its_purpose(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.JOIN)

            with pytest.raises(InvalidCodeError):
                use_code(staff, CodePurpose.RESET, code)
            use_code(staff, CodePurpose.JOIN, code)

    def test_a_code_works_only_for_its_staff_member(self, abc: ConsultancyContext) -> None:
        sita = abc.add_staff(Role.COUNSELLOR, email="sita@abcedu.test")
        ram = abc.add_staff(Role.COUNSELLOR, email="ram@abcedu.test")
        with tenant_context(abc.consultancy):
            code = issue_code(sita, CodePurpose.RESET)
            issue_code(ram, CodePurpose.RESET)

            with pytest.raises(InvalidCodeError):
                use_code(ram, CodePurpose.RESET, code)

    def test_a_reset_code_leaves_a_join_code_working(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            join = issue_code(staff, CodePurpose.JOIN)
            issue_code(staff, CodePurpose.RESET)

            use_code(staff, CodePurpose.JOIN, join)

    def test_a_staff_member_with_no_code_is_refused(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy), pytest.raises(InvalidCodeError):
            use_code(staff, CodePurpose.RESET, "123456")

    def test_a_used_code_is_kept_but_marked_used(self, abc: ConsultancyContext) -> None:
        staff = abc.add_staff(Role.COUNSELLOR)
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)
            use_code(staff, CodePurpose.RESET, code)
            record = OneTimeCode.objects.get(staff=staff)

        assert record.used_at is not None
        assert record.used_at <= timezone.now()


class TestPlatformScope:
    """Platform Codes live with Platform Admins on the public schema; office Codes in the office.

    The two scopes never mix (issue 63).
    """

    def test_a_platform_code_works_on_the_platform(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        with schema_context(PUBLIC_SCHEMA):
            code = issue_code(admin, CodePurpose.RESET)

            use_code(admin, CodePurpose.RESET, code, consume=False)
            use_code(admin, CodePurpose.RESET, code)
            with pytest.raises(InvalidCodeError):
                use_code(admin, CodePurpose.RESET, code)
            record = PlatformAdminCode.objects.get(platform_admin=admin)

        assert code not in record.code_hash
        assert record.expires_at - record.created_at == CODE_LIFETIME

    def test_a_platform_code_is_never_issued_inside_an_office(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        with tenant_context(abc.consultancy), pytest.raises(WrongCodeScopeError):
            issue_code(admin, CodePurpose.RESET)

    def test_a_platform_code_never_works_inside_an_office(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        with schema_context(PUBLIC_SCHEMA):
            code = issue_code(admin, CodePurpose.RESET)

        with tenant_context(abc.consultancy), pytest.raises(InvalidCodeError):
            use_code(admin, CodePurpose.RESET, code)
        with schema_context(PUBLIC_SCHEMA):
            use_code(admin, CodePurpose.RESET, code)

    def test_an_office_code_never_works_for_a_platform_admin_with_the_same_email(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        staff = abc.add_staff(Role.ADMIN, email="ops@platform.test")
        admin = platform_site.add_admin(email="ops@platform.test")
        with tenant_context(abc.consultancy):
            code = issue_code(staff, CodePurpose.RESET)

        with schema_context(PUBLIC_SCHEMA), pytest.raises(InvalidCodeError):
            use_code(admin, CodePurpose.RESET, code)

    def test_a_platform_code_never_works_for_a_staff_member(
        self, abc: ConsultancyContext, platform_site: PlatformContext
    ) -> None:
        staff = abc.add_staff(Role.ADMIN, email="ops@platform.test")
        admin = platform_site.add_admin(email="ops@platform.test")
        with schema_context(PUBLIC_SCHEMA):
            code = issue_code(admin, CodePurpose.RESET)

        with tenant_context(abc.consultancy), pytest.raises(InvalidCodeError):
            use_code(staff, CodePurpose.RESET, code)

    def test_platform_codes_have_the_same_hourly_limit(
        self, platform_site: PlatformContext
    ) -> None:
        admin = platform_site.add_admin()
        with schema_context(PUBLIC_SCHEMA):
            for _ in range(MAX_CODES_PER_HOUR):
                issue_code(admin, CodePurpose.RESET)

            with pytest.raises(CodeLimitReachedError):
                issue_code(admin, CodePurpose.RESET)

    def test_platform_codes_run_out_of_tries(self, platform_site: PlatformContext) -> None:
        admin = platform_site.add_admin()
        with schema_context(PUBLIC_SCHEMA):
            code = issue_code(admin, CodePurpose.RESET)
            for _ in range(MAX_ATTEMPTS):
                with pytest.raises(InvalidCodeError):
                    use_code(admin, CodePurpose.RESET, wrong(code))

            with pytest.raises(InvalidCodeError):
                use_code(admin, CodePurpose.RESET, code)

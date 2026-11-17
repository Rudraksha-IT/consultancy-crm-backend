"""REST endpoints on the Platform subdomain: Platform Admin login, Consultancies and Features.

Refresh and logout are in auth_tokens.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from auth_tokens.services import issue_tokens
from auth_tokens.types import TokenClaims
from modules.serializers import ModuleSwitchesSerializer
from modules.services import switch_modules
from platform_admin.auth import get_request_platform_admin
from platform_admin.exceptions import (
    PlatformAdminDetailsError,
    TooManyPasswordTriesError,
    WrongCurrentPlatformPasswordError,
)
from platform_admin.models import PlatformAdmin, SupportVisit
from platform_admin.office_admins import AddAdminRefusedError, add_office_admin
from platform_admin.permissions import IsPlatformAdmin
from platform_admin.serializers import (
    SUBDOMAIN_TAKEN_MESSAGE,
    AccountChangeSerializer,
    AccountPasswordSerializer,
    AddAdminSerializer,
    ConsultancyOnboardingSerializer,
    LoginSerializer,
    StatusChangeSerializer,
    SupportVisitSerializer,
)
from platform_admin.services import (
    AccountProof,
    authenticate_platform_admin,
    change_my_platform_name,
    change_my_platform_password,
    get_overview,
    list_consultancies,
)
from platform_admin.setup import (
    SetupRefusedError,
    SubdomainTakenError,
    retry_consultancy_setup,
    start_consultancy_setup,
)
from platform_admin.support_access import SupportAccessRefusedError, recent_visits, start_visit
from platform_admin.two_factor_views import (
    TOO_MANY_NUMBERS_MESSAGE,
    WRONG_NUMBER_MESSAGE,
    two_factor_login_answer,
)
from staff.employee_views import JOIN_PATH
from staff.exceptions import EmailTakenError, PasswordRulesNotMetError
from staff.reset_views import RULES_NOT_MET_MESSAGE
from staff.serializers import EMAIL_TAKEN_MESSAGE
from tenancy.exceptions import InvalidStatusChangeError
from tenancy.models import Consultancy, Domain
from tenancy.serializers import ThemeSerializer
from tenancy.services import NOT_READY_MESSAGE, change_status, is_ready, set_theme
from two_factor.exceptions import TooManyWrongNumbersError, WrongNumberError

INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."
NOT_YOUR_CURRENT_ONE_MESSAGE = "That isn't your current password."
TOO_MANY_TRIES_MESSAGE = "Too many wrong passwords. Wait 15 minutes, then try again."
# The Account page's change password calls the password field `new_password`.
_ANSWERED_AS = {"password": "new_password"}

logger = logging.getLogger(__name__)


def _summary(admin: PlatformAdmin) -> dict[str, object]:
    return {"id": admin.pk, "name": admin.name, "email": admin.email}


class PlatformLoginView(APIView):
    """Log a Platform Admin in on the Platform subdomain."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request: Request) -> Response:
        """Return `{access, refresh, platform_admin}` for valid, active credentials.

        With two-step login on, return `{two_factor: "setup" | "verify", challenge}` instead.
        """
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        admin = authenticate_platform_admin(
            serializer.validated_data["email"], serializer.validated_data["password"]
        )
        if admin is None:
            logger.info("Platform Admin login failed")
            return Response(
                {"detail": INVALID_CREDENTIALS_MESSAGE}, status=status.HTTP_400_BAD_REQUEST
            )
        if admin.two_factor_required:
            # No tokens yet: the number from the app comes next (issue 61).
            return two_factor_login_answer(admin, request)
        tokens = issue_tokens(admin)
        logger.info("Platform Admin logged in: platform_admin_id=%s", admin.pk)
        return Response({**asdict(tokens), "platform_admin": _summary(admin)})


def _device_of(request: Request) -> str:
    """The device this request comes from, as named in its access token."""
    claims = request.auth
    if not isinstance(claims, TokenClaims):  # pragma: no cover - every caller is logged in
        raise TypeError
    return claims.device


_WAIT_MESSAGES: dict[type[Exception], str] = {
    TooManyPasswordTriesError: TOO_MANY_TRIES_MESSAGE,
    TooManyWrongNumbersError: TOO_MANY_NUMBERS_MESSAGE,
}
_FIELD_MESSAGES: dict[type[Exception], tuple[str, str]] = {
    WrongCurrentPlatformPasswordError: ("current_password", NOT_YOUR_CURRENT_ONE_MESSAGE),
    WrongNumberError: ("code", WRONG_NUMBER_MESSAGE),
}


def _refusal(error: Exception) -> Response:
    """The answer to a refused Account change; never repeats a value or a password."""
    wait = _WAIT_MESSAGES.get(type(error))
    if wait is not None:
        return Response({"detail": wait}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    field = _FIELD_MESSAGES.get(type(error))
    if field is not None:
        body: dict[str, object] = {field[0]: [field[1]]}
    elif isinstance(error, PasswordRulesNotMetError):
        body = {"detail": RULES_NOT_MET_MESSAGE, "unmet_rules": [r.value for r in error.unmet]}
    elif isinstance(error, PlatformAdminDetailsError):
        body = {_ANSWERED_AS.get(k, k): [message] for k, message in error.problems.items()}
    else:  # pragma: no cover - only the errors above are passed in
        raise error
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


class PlatformMeView(APIView):
    """The logged-in Platform Admin's own Account: see it, or change the name or email."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return the Platform Admin's id, name and email."""
        return Response({"platform_admin": _summary(get_request_platform_admin(request))})

    def patch(self, request: Request) -> Response:
        """Change my name; no password needed, and the email can't change here (issue 65).

        Returns `{access, refresh, platform_admin}`: this session's tokens (the Next.js server
        keeps them).
        """
        serializer = AccountChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            admin, tokens = change_my_platform_name(
                get_request_platform_admin(request),
                serializer.validated_data["name"],
                device=_device_of(request),
            )
        except PlatformAdminDetailsError as error:
            # expected refusal, answered with its status
            return _refusal(error)
        return Response({**asdict(tokens), "platform_admin": _summary(admin)})


class PlatformPasswordView(APIView):
    """Change my password on the Account page; every other session is signed out (issue 59)."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def post(self, request: Request) -> Response:
        """Return `{access, refresh}` for this session, or what's wrong."""
        serializer = AccountPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            tokens = change_my_platform_password(
                get_request_platform_admin(request),
                AccountProof(data["current_password"], data.get("code")),
                data["new_password"],
                _device_of(request),
            )
        except (
            TooManyWrongNumbersError,
            WrongNumberError,
            PasswordRulesNotMetError,
            TooManyPasswordTriesError,
            WrongCurrentPlatformPasswordError,
            PlatformAdminDetailsError,
        ) as error:
            # expected refusal, answered with its status; the service logs password refusals
            return _refusal(error)
        return Response(asdict(tokens))


def _join_link(request: Request, host: str) -> str:
    """The new office's sign-up page, with the scheme and port the Platform was opened on."""
    _, separator, port = request.get_host().rpartition(":")
    address = f"{host}:{port}" if separator and port.isdigit() else host
    return f"{request.scheme or 'https'}://{address}{JOIN_PATH}"


def _not_ready(consultancy: Consultancy) -> Response | None:
    """The refusal for an office still being set up or whose setup failed, or None."""
    if is_ready(consultancy):
        return None
    return Response({"detail": NOT_READY_MESSAGE}, status=status.HTTP_400_BAD_REQUEST)


class ProductDomainView(APIView):
    """The product domain every office's address ends in, from PRODUCT_DOMAIN (issue 64).

    The New consultancy page shows it after the address field, so only the front part is typed.
    """

    permission_classes = (IsPlatformAdmin,)

    def get(self, request: Request) -> Response:
        """Return `{product_domain}`."""
        return Response({"product_domain": settings.PRODUCT_DOMAIN})


class ConsultancyListView(APIView):
    """List every Consultancy, or start creating a new one (without any Admin, issue 64)."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def get(self, request: Request) -> Response:
        """Return all Consultancies with subdomain, Status, Modules on and setup state."""
        return Response([asdict(overview) for overview in list_consultancies()])

    def post(self, request: Request) -> Response:
        """Reserve the Consultancy and queue its setup; answer 202 at once (issue 60).

        The `worker` builds its schema; the overview's `setup_state` says how far it has come.
        Admins are added afterwards (`ConsultancyAdminsView`).
        """
        serializer = ConsultancyOnboardingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        onboarding = serializer.to_onboarding()
        try:
            consultancy = start_consultancy_setup(
                onboarding, by=get_request_platform_admin(request)
            )
        except SubdomainTakenError:
            # expected refusal (another create took it a moment earlier); the request line logs it
            return Response(
                {"subdomain": [SUBDOMAIN_TAKEN_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST
            )
        except InvalidStatusChangeError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": error.message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(asdict(get_overview(consultancy)), status=status.HTTP_202_ACCEPTED)


class ConsultancyAdminsView(APIView):
    """Add admin: invite an Admin into one set-up Consultancy by Code (issue 64)."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def post(self, request: Request, consultancy_id: int) -> Response:
        """Answer 201 with `{name, email, status}`, or 400 with what's wrong."""
        consultancy = get_object_or_404(Consultancy, pk=consultancy_id)
        refused = _not_ready(consultancy)
        if refused is not None:
            return refused
        serializer = AddAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        host = Domain.objects.get(tenant=consultancy, is_primary=True).domain
        try:
            staff = add_office_admin(
                consultancy,
                data["name"],
                data["email"],
                join_link=_join_link(request, str(host)),
                by=get_request_platform_admin(request),
            )
        except AddAdminRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": error.message}, status=status.HTTP_400_BAD_REQUEST)
        except EmailTakenError:
            # expected refusal, answered with its status; the request line logs it
            return Response({"email": [EMAIL_TAKEN_MESSAGE]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"name": staff.name, "email": staff.email, "status": staff.status},
            status=status.HTTP_201_CREATED,
        )


class ConsultancyRetrySetupView(APIView):
    """Try again: set up a Consultancy whose setup failed (issue 60)."""

    permission_classes = (IsPlatformAdmin,)

    def post(self, request: Request, consultancy_id: int) -> Response:
        """Queue a new setup Job and answer 202 with the summary, or 400 if it didn't fail."""
        consultancy = get_object_or_404(Consultancy, pk=consultancy_id)
        try:
            retry_consultancy_setup(consultancy, by=get_request_platform_admin(request))
        except SetupRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": error.message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(asdict(get_overview(consultancy)), status=status.HTTP_202_ACCEPTED)


class ConsultancyStatusView(APIView):
    """Change a Consultancy's Status with a reason (spec 13-13c); its data is never deleted."""

    permission_classes = (IsPlatformAdmin,)

    def post(self, request: Request, consultancy_id: int) -> Response:
        """Make the change and return the updated summary, or 400 with why it is refused."""
        consultancy = get_object_or_404(Consultancy, pk=consultancy_id)
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            change_status(
                consultancy, serializer.to_change(), by=get_request_platform_admin(request)
            )
        except InvalidStatusChangeError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": error.message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(asdict(get_overview(consultancy)))


class ConsultancyModulesView(APIView):
    """Switch a Consultancy's switchable Modules on or off (the Features dialog)."""

    permission_classes = (IsPlatformAdmin,)

    def put(self, request: Request, consultancy_id: int) -> Response:
        """Save every switchable Module's switch and return the updated summary."""
        consultancy = get_object_or_404(Consultancy, pk=consultancy_id)
        refusal = _not_ready(consultancy)
        if refusal is not None:
            return refusal
        serializer = ModuleSwitchesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        switch_modules(consultancy, serializer.switched_on())
        logger.info(
            "Features saved: consultancy_id=%s, platform_admin_id=%s",
            consultancy.pk,
            get_request_platform_admin(request).pk,
        )
        return Response(asdict(get_overview(consultancy)))


def visit_answer(visit: SupportVisit) -> dict[str, object]:
    """One Support Access visit as the Platform page lists it (never shown to the office)."""
    ended_at = visit.ended_at
    if ended_at is None and visit.ends_by <= timezone.now():
        ended_at = visit.ends_by
    return {
        "id": visit.pk,
        "platform_admin": {"name": visit.platform_admin.name},
        "consultancy": {"id": visit.consultancy.pk, "name": visit.consultancy.name},
        "reason": visit.reason,
        "consultancy_status": visit.consultancy_status,
        "started_at": visit.started_at.isoformat(),
        "ended_at": None if ended_at is None else ended_at.isoformat(),
    }


class SupportVisitStartView(APIView):
    """Open a Consultancy as Platform Admin, with a reason (spec 9-10)."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def post(self, request: Request, consultancy_id: int) -> Response:
        """Record the visit and return it with the one-time code that enters the office.

        The browser takes the code to the office's own address; it works there once, briefly.
        """
        consultancy = get_object_or_404(Consultancy, pk=consultancy_id)
        serializer = SupportVisitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            started = start_visit(
                get_request_platform_admin(request),
                consultancy,
                serializer.validated_data["reason"],
            )
        except SupportAccessRefusedError as error:
            # expected refusal, answered with its status; the request line logs it
            return Response({"detail": error.message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "visit": visit_answer(started.visit),
                "host": get_overview(consultancy).host,
                "entry_code": started.entry_code,
            },
            status=status.HTTP_201_CREATED,
        )


class SupportVisitListView(APIView):
    """The Platform's record of every visit: "Visits by Platform Admins" (spec 10)."""

    permission_classes = (IsPlatformAdmin,)

    def get(self, request: Request) -> Response:
        """Return the latest visits, newest first."""
        return Response([visit_answer(visit) for visit in recent_visits()])


class ConsultancyThemeView(APIView):
    """Choose an office's colours (its Theme). Only a Platform Admin, only on the Platform."""

    permission_classes = (IsPlatformAdmin,)
    parser_classes = (JSONParser,)

    def put(self, request: Request, consultancy_id: int) -> Response:
        """Validate and store the Theme and return the updated summary."""
        consultancy = get_object_or_404(Consultancy, pk=consultancy_id)
        refusal = _not_ready(consultancy)
        if refusal is not None:
            return refusal
        serializer = ThemeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_theme(consultancy, serializer.to_theme())
        logger.info(
            "Theme changed: consultancy_id=%s, platform_admin_id=%s",
            consultancy.pk,
            get_request_platform_admin(request).pk,
        )
        return Response(asdict(get_overview(consultancy)))

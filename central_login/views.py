"""The Central login endpoint, served only on `login.<product-domain>` (issue 44)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from central_login.serializers import CentralLoginSerializer
from central_login.services import LinkBase, send_office_links
from config.responses import AnswerThenRun

SAME_ANSWER = "If this email belongs to an office, we've sent it a link."


class CentralLoginView(APIView):
    """Email the login links of the offices an email belongs to; always the same answer."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    parser_classes = (JSONParser,)

    def post(self, request: Request) -> Response:
        """Answer 202 with the same message for every valid email; 400 for anything else."""
        serializer = CentralLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email: str = serializer.validated_data["email"]
        base = LinkBase(scheme=request.scheme or "https", port=_port_of(request.get_host()))
        return AnswerThenRun(
            lambda: send_office_links(email, base),
            {"detail": SAME_ANSWER},
            status=status.HTTP_202_ACCEPTED,
        )


def _port_of(host: str) -> str | None:
    """The port in a validated host such as `login.localhost:8080`, or None."""
    _, separator, port = host.rpartition(":")
    return port if separator and port.isdigit() else None

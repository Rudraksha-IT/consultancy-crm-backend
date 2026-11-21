"""Adding Admins to a Consultancy from the Platform, once it is set up (issue 64).

Creating a Consultancy invites nobody. A Platform Admin adds each Admin from the office's row
of the Platform list: they are added as invited and emailed a Code with the office's sign-up
link, exactly as Employees invites anyone (issue 07). Call on the Platform (public schema).
"""

from __future__ import annotations

import logging

from platform_admin.models import PlatformAdmin
from staff.employees import invite
from staff.models import Role, Staff
from staff.types import NewStaff, Office
from tenancy.context import office_context
from tenancy.models import Consultancy
from tenancy.services import TERMINATED_IS_FINAL_MESSAGE, get_status
from tenancy.status import ConsultancyStatus

logger = logging.getLogger(__name__)


class AddAdminRefusedError(Exception):
    """The office can't take an Admin now; `message` says why, in screen words."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def add_office_admin(
    consultancy: Consultancy, name: str, email: str, *, join_link: str, by: PlatformAdmin
) -> Staff:
    """Invite `name` <`email`> as an Admin of `consultancy` and email them a Code.

    Allowed for any Status but Terminated (a read-only office still needs someone to sort it
    out). The caller checks the office is set up (`tenancy.services.is_ready`).

    Raises:
        AddAdminRefusedError: The office is Terminated.
        EmailTakenError: Someone in that office already uses the email.
    """
    if get_status(consultancy) == ConsultancyStatus.TERMINATED:
        raise AddAdminRefusedError(TERMINATED_IS_FINAL_MESSAGE)
    office = Office(consultancy=consultancy, join_link=join_link)
    with office_context(consultancy):
        staff = invite(NewStaff(name=name, email=email, role=Role.ADMIN), office)
    logger.info(
        "Admin invited from the Platform: staff_id=%s, consultancy_id=%s, platform_admin_id=%s",
        staff.pk,
        consultancy.pk,
        by.pk,
    )
    return staff

"""The records an address names by UUID (ADR 0027): `<staff:staff_id>` and so on.

Each converter takes only a UUID of a record in this office and hands the view its number, so
views and services keep working with numbers inside the server.
"""

from __future__ import annotations

from batches.models import Batch
from catalogue.models import Course, University
from clock_in.models import Holiday, WorkFromHome, WorkSession
from commission.models import Commission
from config.public_ids import register_record_converters
from course_files.models import CourseFile
from document_examples.models import DocumentExample
from documents.models import DocumentType
from expenses.models import Expense
from fee_types.models import FeeType
from notifications.models import Notification
from office_networks.models import OfficeNetwork
from platform_admin.models import PlatformAdmin, SavedProductLogo
from staff.models import Staff
from tasks.models import Task
from tenancy.models import Consultancy
from transfer_requests.models import TransferRequest

register_record_converters(
    {
        "staff": Staff,
        "university": University,
        "course": Course,
        "document_type": DocumentType,
        "course_file": CourseFile,
        "example": DocumentExample,
        "fee_type": FeeType,
        "expense": Expense,
        "commission": Commission,
        "task": Task,
        "notification": Notification,
        "batch": Batch,
        "holiday": Holiday,
        "work_session": WorkSession,
        "work_from_home": WorkFromHome,
        "office_network": OfficeNetwork,
        "transfer_request": TransferRequest,
        "consultancy": Consultancy,
        "platform_admin": PlatformAdmin,
        "saved_logo": SavedProductLogo,
    }
)

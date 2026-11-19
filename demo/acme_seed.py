# Demo data for the Acme office (a placeholder name). LOCAL DEVELOPMENT ONLY.
# Run by `python manage.py seed_demo` (the compose `seed` service) on the first start, when
# SEED_DEMO_DATA=true (sample.env.backend). It creates the office through the Platform's own setup,
# waits for the worker to build it, then adds Staff, Students and six months of their story.
# Everything goes through the app's own services (History, Stages, Balances as in real use);
# each step's History, Visit and status dates are then set to its day. Dates are relative to today.
# Staff log in at http://acme.localhost:8080 with the password below.
# Kept as a script, outside ruff and mypy (pyproject.toml); the command around it is tested.
import io
import os
import time
from datetime import date, datetime, time as clock, timedelta
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.models import Max
from django_tenants.utils import schema_context, tenant_context

KTM = ZoneInfo("Asia/Kathmandu")
TODAY = date.today()
PASSWORD = "Demo-Acme-2026!"


def at(day: date, hour: int = 11, minute: int = 0) -> datetime:
    return datetime.combine(day, clock(hour, minute), tzinfo=KTM)


def ago(days: int) -> date:
    return TODAY - timedelta(days=days)


# --- 1. The office, through the Platform's own setup --------------------------------------
from modules.services import switch_modules
from modules.types import SWITCHABLE_MODULES
from platform_admin.models import PlatformAdmin
from platform_admin.setup import start_consultancy_setup
from platform_admin.types import ConsultancyOnboarding
from tenancy.models import Consultancy, SetupState
from tenancy.status import ConsultancyStatus
from tenancy.theme import DEFAULT_THEME

with schema_context("public"):
    ops = PlatformAdmin.objects.get(email=os.environ["PLATFORM_ADMIN_EMAIL"].strip().lower())
    office = Consultancy.objects.filter(schema_name="acme").first()
    if office is None:
        office = start_consultancy_setup(
            ConsultancyOnboarding(
                name="Acme Education Consultancy",
                subdomain="acme",
                theme=DEFAULT_THEME,
                status=ConsultancyStatus.ACTIVE,
                paid_until=date(2027, 6, 30),
            ),
            by=ops,
        )
    for _ in range(120):  # the worker builds it
        office.refresh_from_db()
        if str(office.setup_state) == SetupState.READY.value:
            break
        time.sleep(1)
    assert str(office.setup_state) == SetupState.READY.value, "office setup didn't finish"
    switch_modules(office, list(SWITCHABLE_MODULES))
print("office ready:", office.schema_name)

# --- 2. Inside the office -----------------------------------------------------------------
from applications.services import (
    add_application,
    choose_offer,
    record_offer,
    change_visa_status,
    change_visa_dates,
    start_visa,
)
from applications.appointments import Appointment
from applications.models import Application, Visa
from applications.statuses import OfferKind, VisaStatus as VS
from applications.types import NewApplication
from batches.attendance import take_attendance
from batches.models import Mark
from batches.services import add_student as add_to_batch, create_batch
from batches.types import BatchDetails
from catalogue.models import CourseLevel
from catalogue.services import add_course, add_university
from catalogue.types import NewCourse
from clock_in.models import WorkSession
from commission.models import Commission, CommissionStatus
from commission.services import add_commission, change_status as change_commission
from commission.types import NewCommission
from documents.files import PDF
from documents.models import Document, DocumentType
from documents.groups import DocumentGroup as DG
from documents.requirements import ListedType, set_country_requirements
from documents.services import (
    add_document_type,
    check_document,
    set_needed_by_everyone,
    upload_document,
)
from documents.types import NewDocument, NewDocumentType, NewFile
from documents.verification import Verification
from expenses.models import ExpenseCategory as EC, ExpenseMethod as EM
from expenses.services import add_expense
from expenses.types import NewExpense
from fee_types.models import FeeType
from fees.services import add_fee
from fees.types import NewFee
from history.models import HistoryEntry
from office_networks.networks import parse_office_network
from office_networks.services import add_office_network, office_network_limits
from payments.models import Payment, PaymentMethod as PM
from payments.services import record_payment
from payments.types import NewPayment
from staff.models import Role, Staff
from staff.services import create_staff
from staff.types import NewStaff
from students.journey import set_stage_by_hand
from students.models import Country as C, Qualification as Q, Source as S, Stage, Student
from students.profile import add_academic_record, add_test_score, change_details, edit_note
from students.services import add_student
from students.types import NewStudent
from tasks.models import TaskPriority as TP
from tasks.services import create_task, set_task_done
from tasks.types import NewTask
from transfer_requests.services import ask_transfer
from visits.models import Visit, VisitReason as VR
from visits.services import log_follow_up


def ft(name: str) -> FeeType:
    """The office's Fee Type with this name (the seven every office starts with)."""
    return FeeType.objects.get(name=name)


with tenant_context(office):
    if Student.objects.exists():
        raise SystemExit("acme already has Students; reset first (docker compose down -v).")

    def staff(name, email, role, title, mobile, joined):
        return create_staff(
            NewStaff(name=name, email=email, role=role, mobile=mobile, job_title=title,
                     joined_on=joined),
            PASSWORD,
        )

    admin = staff("Ramesh Bhandari", "admin@acme.com", Role.ADMIN, "Director", "9851012345", ago(400))
    sita = staff("Sita Karki", "counsellor@acme.com", Role.COUNSELLOR, "Senior Counsellor", "9841023456", ago(300))
    bikash = staff("Bikash Thapa", "counsellor2@acme.com", Role.COUNSELLOR, "Counsellor", "9861034567", ago(200))
    anita = staff("Anita Gurung", "frontdesk@acme.com", Role.FRONT_DESK, "Front Desk Officer", "9811045678", ago(250))
    prakash = staff("Prakash Shrestha", "teacher@acme.com", Role.TEACHER, "IELTS & PTE Trainer", "9801056789", ago(180))
    print("staff:", Staff.objects.count())

    # Catalogue (intakes as YYYY-MM).
    def uni(name, country, courses):
        u = add_university(name, country, admin)
        made = {}
        for cname, level, intakes in courses:
            made[cname] = add_course(u, NewCourse(name=cname, level=level, intakes=tuple(intakes)), admin)
        return u, made

    fed, fed_c = uni("Federation University", C.AUSTRALIA, [
        ("Master of Business Administration", CourseLevel.MASTER, ["2027-03", "2027-07"]),
        ("Bachelor of Information Technology", CourseLevel.BACHELOR, ["2027-03", "2027-07"]),
    ])
    vic, vic_c = uni("Victoria University", C.AUSTRALIA, [
        ("Master of Professional Accounting", CourseLevel.MASTER, ["2027-02", "2027-07"]),
        ("Bachelor of Nursing", CourseLevel.BACHELOR, ["2027-02"]),
    ])
    tor, tor_c = uni("Torrens University", C.AUSTRALIA, [
        ("Master of Public Health", CourseLevel.MASTER, ["2027-03", "2027-09"]),
    ])
    herts, herts_c = uni("University of Hertfordshire", C.UK, [
        ("MSc Data Science", CourseLevel.MASTER, ["2027-01", "2027-09"]),
        ("MSc Cyber Security", CourseLevel.MASTER, ["2027-01", "2027-09"]),
    ])
    cov, cov_c = uni("Coventry University", C.UK, [
        ("MBA Global Business", CourseLevel.MASTER, ["2027-01", "2027-05"]),
    ])
    con, con_c = uni("Conestoga College", C.CANADA, [
        ("Graduate Certificate in Project Management", CourseLevel.GRADUATE_CERTIFICATE, ["2027-01", "2027-05", "2027-09"]),
    ])
    fan, fan_c = uni("Fanshawe College", C.CANADA, [
        ("Diploma in Business Administration", CourseLevel.DIPLOMA, ["2027-01", "2027-09"]),
    ])
    ulm, ulm_c = uni("University of Louisiana Monroe", C.USA, [
        ("MS Computer Science", CourseLevel.MASTER, ["2027-01", "2027-08"]),
    ])
    ara, ara_c = uni("Ara Institute of Canterbury", C.NEW_ZEALAND, [
        ("Graduate Diploma in Engineering Technology", CourseLevel.DIPLOMA, ["2027-02", "2027-07"]),
    ])

    # Dating helpers: date whatever the last step wrote.
    def marks():
        return (HistoryEntry.objects.aggregate(m=Max("pk"))["m"] or 0,
                Visit.objects.aggregate(m=Max("pk"))["m"] or 0)

    def dated(mark, when):
        # History is append-only in the app; demo data dates it directly in the database.
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {HistoryEntry._meta.db_table} SET at = %s WHERE id > %s", [when, mark[0]]
            )
        Visit.objects.filter(pk__gt=mark[1]).update(at=when)

    def step(day, fn, *args, **kwargs):
        m = marks()
        result = fn(*args, **kwargs)
        dated(m, at(day, 10 + (day.day % 6), (day.day * 7) % 60))
        return result

    add_document_type(NewDocumentType(name="Offer Letter / CoE", needed_by_everyone=False, usual_group=DG.VISA), admin)
    add_document_type(NewDocumentType(name="Visa Grant Letter", needed_by_everyone=False, usual_group=DG.VISA), admin)
    # Document lists per Country (issue 80), as in the prototype. Bank, sponsor and SOP move off
    # everyone's list onto the Countries' lists.
    for name, group in [("Genuine Student statement (GS)", DG.STATEMENTS),
                        ("Letter of explanation (LOE)", DG.STATEMENTS), ("Police report", DG.VISA),
                        ("Medical report", DG.VISA), ("Birth certificate", DG.VISA),
                        ("Health insurance (OSHC)", DG.VISA), ("Bachelor Transcript", DG.APPLYING),
                        ("CV", DG.APPLYING)]:
        add_document_type(NewDocumentType(name=name, needed_by_everyone=False, usual_group=group), admin)
    for name in ("Bank Statement", "Sponsor Letter", "Statement of Purpose"):
        set_needed_by_everyone(DocumentType.objects.get(name=name).pk, False, admin)
    types = {t.name: t for t in DocumentType.objects.all()}
    country_lists = {
        C.AUSTRALIA: [("Genuine Student statement (GS)", DG.STATEMENTS), ("Statement of Purpose", DG.STATEMENTS),
                      ("Bank Statement", DG.VISA), ("Sponsor Letter", DG.VISA), ("Medical report", DG.VISA),
                      ("Police report", DG.VISA), ("Offer Letter / CoE", DG.VISA), ("Health insurance (OSHC)", DG.VISA)],
        C.UK: [("Statement of Purpose", DG.STATEMENTS), ("Bank Statement", DG.VISA), ("Offer Letter / CoE", DG.VISA)],
        C.CANADA: [("Letter of explanation (LOE)", DG.STATEMENTS), ("Statement of Purpose", DG.STATEMENTS),
                   ("Bank Statement", DG.VISA), ("Police report", DG.VISA), ("Medical report", DG.VISA)],
        C.USA: [("Statement of Purpose", DG.STATEMENTS), ("Bank Statement", DG.APPLYING), ("Offer Letter / CoE", DG.VISA)],
        C.NEW_ZEALAND: [("Statement of Purpose", DG.STATEMENTS), ("Bank Statement", DG.VISA),
                        ("Police report", DG.VISA), ("Medical report", DG.VISA)],
        C.JAPAN: [("Birth certificate", DG.APPLYING), ("Bank Statement", DG.VISA)],
    }
    for country, rows in country_lists.items():
        set_country_requirements(country, [ListedType(types[n], g) for n, g in rows], admin)

    def doc_type(*words):
        for name, t in types.items():
            if all(w.lower() in name.lower() for w in words):
                return t
        return next(iter(types.values()))

    PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

    def upload(student, by, *words, application=None, visa=None, file_name="scan.pdf"):
        page = NewFile(io.BytesIO(PDF_BYTES), PDF, file_name, len(PDF_BYTES))
        return upload_document(student, NewDocument(
            document_type=doc_type(*words), files=(page,), application=application, visa=visa), by)

    # A Student's whole story. `plan` is how far they got; days are counted from `start`.
    def story(name, phone, email, country, source, qual, counsellor, start, plan, note,
              course=None, intake="2027-03", past_degree="", previous_job="", sub_agent="",
              lost_reason="", interview=None):
        s = step(ago(start), add_student, NewStudent(
            name=name, phone=phone, email=email, country=country, source=source,
            highest_qualification=qual, past_degree=past_degree, previous_job=previous_job,
            sub_agent_name=sub_agent, note=note, assigned_counsellor=counsellor), anita)
        Student.objects.filter(pk=s.pk).update(created_at=at(ago(start), 10))
        by = counsellor or admin
        d = lambda n: ago(max(start - n, 0))  # noqa: E731
        if plan == "lead":
            return s
        step(d(3), set_stage_by_hand, s, Stage.COUNSELLING, by)
        if plan == "lost":
            step(d(20), set_stage_by_hand, s, Stage.LOST, by, lost_reason)
            return s
        if plan == "counselling":
            return s
        step(d(10), add_fee, s, NewFee(fee_type=ft("Application fee"), amount=40000), admin)
        step(d(11), record_payment, s, NewPayment(amount=20000, paid_on=d(11), method=PM.ESEWA,
                                                   fee_type=ft("Application fee"), note="first half"), anita)
        upload(s, by, "passport")
        upload(s, by, "citizenship")
        upload(s, by, "+2")
        if plan == "signed_up":
            return s
        upload(s, by, "see")
        upload(s, by, "ielts")
        app = step(d(22), add_application, s, NewApplication(course=course, intake=intake), by)
        Application.objects.filter(pk=app.pk).update(applied_on=d(22))
        if plan == "applied":
            return s
        step(d(40), record_offer, s, app.pk, OfferKind.CONDITIONAL, by)
        if plan == "offer":
            return s
        step(d(58), record_payment, s, NewPayment(amount=20000, paid_on=d(58), method=PM.BANK_TRANSFER,
                                                   fee_type=ft("Application fee"), note="second half"), anita)
        upload(s, by, "offer", application=app, file_name="coe.pdf")
        upload(s, by, "bank")
        upload(s, by, "sponsor")
        upload(s, by, "statement of purpose")
        step(d(72), add_fee, s, NewFee(fee_type=ft("Visa fee"), amount=25000), admin)
        step(d(74), choose_offer, s, app.pk, by)
        if plan == "visa_preparing":
            # Papers being prepared: no Visa until it is sent to the embassy (issue 75).
            return s
        visa = step(d(80), start_visa, s, app.pk, by)
        Visa.objects.filter(pk=visa.pk).update(started_on=d(80))
        if plan == "visa_lodged":
            return s
        if plan == "visa_interview":
            change_visa_dates(s, visa.pk, {Appointment.INTERVIEW: interview}, by)
            return s
        if plan == "refused":
            step(d(95), change_visa_status, s, visa.pk, VS.REFUSED, by)
            Visa.objects.filter(pk=visa.pk)
            return s
        step(d(100), change_visa_status, s, visa.pk, VS.GRANTED, by)
        Visa.objects.filter(pk=visa.pk).update(granted_on=d(100))
        step(d(101), record_payment, s, NewPayment(amount=25000, paid_on=d(101), method=PM.CASH,
                                                    fee_type=ft("Visa fee")), anita)
        upload(s, by, "visa grant", visa=visa, file_name="visa-grant.pdf")
        return s

    M = fed_c["Master of Business Administration"]
    students = {}
    plan = [
        # name, phone, email, country, source, qual, counsellor, start (days ago), plan, note, course, intake
        ("Aarav Sharma", "9841111001", "aarav.sharma@example.com", C.AUSTRALIA, S.WALK_IN, Q.BACHELOR, sita, 175, "granted", "BBS from TU. Wants MBA in Melbourne. Parents sponsoring.", M, "2027-03"),
        ("Priya Shrestha", "9841111002", "priya.shrestha@example.com", C.AUSTRALIA, S.FACEBOOK, Q.BACHELOR, sita, 168, "granted", "BSc Nursing. Prefers Victoria University.", vic_c["Bachelor of Nursing"], "2027-02"),
        ("Suman Rai", "9841111003", "suman.rai@example.com", C.UK, S.REFERRAL, Q.BACHELOR, bikash, 160, "granted", "Computer engineer, 2 years at a software company.", herts_c["MSc Data Science"], "2027-01"),
        ("Nisha Tamang", "9841111004", "nisha.tamang@example.com", C.CANADA, S.WEBSITE, Q.BACHELOR, bikash, 150, "granted", "Project coordinator at an NGO. Canada PGWP route.", con_c["Graduate Certificate in Project Management"], "2027-01"),
        ("Rohan Karki", "9841111005", "rohan.karki@example.com", C.AUSTRALIA, S.SUB_AGENT, Q.BACHELOR, sita, 140, "refused", "Visa refused (GTE). Discussing a second attempt for July.", tor_c["Master of Public Health"], "2027-03"),
        ("Sneha Gurung", "9841111006", "sneha.gurung@example.com", C.UK, S.EVENT_FAIR, Q.BACHELOR, bikash, 130, "visa_interview", "Credibility interview booked. Practise the interview questions.", cov_c["MBA Global Business"], "2027-01"),
        ("Kiran Magar", "9841111007", "kiran.magar@example.com", C.AUSTRALIA, S.WALK_IN, Q.BACHELOR, sita, 125, "visa_lodged", "Visa lodged. Waiting for the outcome.", vic_c["Master of Professional Accounting"], "2027-02"),
        ("Anjali Poudel", "9841111008", "anjali.poudel@example.com", C.USA, S.FACEBOOK, Q.BACHELOR, bikash, 118, "visa_preparing", "CoE (I-20) received. Preparing the DS-160.", ulm_c["MS Computer Science"], "2027-01"),
        ("Bibek Bhandari", "9841111009", "bibek.bhandari@example.com", C.NEW_ZEALAND, S.PHONE_CALL, Q.BACHELOR, sita, 110, "visa_preparing", "Civil engineer. Funds shown by his uncle.", ara_c["Graduate Diploma in Engineering Technology"], "2027-02"),
        ("Pooja Poudel", "9841111010", "pooja.poudel@example.com", C.AUSTRALIA, S.REFERRAL, Q.BACHELOR, sita, 95, "offer", "Conditional offer: needs IELTS 6.5 each band.", fed_c["Bachelor of Information Technology"], "2027-07"),
        ("Sagar Thapa", "9841111011", "sagar.thapa@example.com", C.UK, S.WEBSITE, Q.BACHELOR, bikash, 88, "offer", "Offer from Hertfordshire. Deciding on the September intake.", herts_c["MSc Cyber Security"], "2027-09"),
        ("Asmita KC", "9841111012", "asmita.kc@example.com", C.CANADA, S.FACEBOOK, Q.PLUS_TWO, sita, 80, "applied", "+2 Management, GPA 3.4. Diploma in Canada.", fan_c["Diploma in Business Administration"], "2027-01"),
        ("Dipesh Lama", "9841111013", "dipesh.lama@example.com", C.AUSTRALIA, S.WALK_IN, Q.BACHELOR, sita, 70, "applied", "Applied to Federation for March.", M, "2027-03"),
        ("Rachana Joshi", "9841111014", "rachana.joshi@example.com", C.UK, S.EVENT_FAIR, Q.BACHELOR, bikash, 62, "applied", "BBA. Coventry MBA, January intake.", cov_c["MBA Global Business"], "2027-01"),
        ("Saroj Khadka", "9841111015", "saroj.khadka@example.com", C.AUSTRALIA, S.SUB_AGENT, Q.BACHELOR, sita, 48, "signed_up", "Signed up. Collecting documents.", None, "2027-07"),
        ("Manisha Bhattarai", "9841111016", "manisha.bhattarai@example.com", C.CANADA, S.WEBSITE, Q.BACHELOR, bikash, 40, "signed_up", "Signed up for processing and the IELTS class.", None, "2027-05"),
        ("Ujjwal Basnet", "9841111017", "ujjwal.basnet@example.com", C.AUSTRALIA, S.FACEBOOK, Q.PLUS_TWO, sita, 33, "signed_up", "Test-prep only for now: IELTS morning class.", None, "2027-07"),
        ("Sabina Rana", "9841111018", "sabina.rana@example.com", C.UK, S.REFERRAL, Q.BACHELOR, bikash, 28, "counselling", "Comparing UK and Australia. Budget about NPR 35 lakh.", None, "2027-09"),
        ("Nabin Pandey", "9841111019", "nabin.pandey@example.com", C.AUSTRALIA, S.WALK_IN, Q.BACHELOR, sita, 21, "counselling", "Wants nursing. Brought his transcripts on the second visit.", None, "2027-07"),
        ("Sunita Oli", "9841111020", "sunita.oli@example.com", C.CANADA, S.PHONE_CALL, Q.PLUS_TWO, bikash, 16, "counselling", "Asking about college diplomas and part-time work.", None, "2027-09"),
        ("Rajan Dahal", "9841111021", "rajan.dahal@example.com", C.USA, S.FACEBOOK, Q.BACHELOR, sita, 75, "lost", "Was planning the USA.", None, "2027-08"),
        ("Kabita Nepal", "9841111022", "kabita.nepal@example.com", C.AUSTRALIA, S.WEBSITE, Q.BACHELOR, None, 45, "lost", "Enquired about MBA fees.", None, "2027-07"),
        ("Hari Bista", "9841111023", "hari.bista@example.com", C.JAPAN, S.WALK_IN, Q.PLUS_TWO, None, 9, "lead", "Interested in Japanese language school.", None, "2027-04"),
        ("Gita Pokharel", "9841111024", "gita.pokharel@example.com", C.AUSTRALIA, S.FACEBOOK, Q.BACHELOR, None, 6, "lead", "Saw our Facebook post on Australian scholarships.", None, "2027-07"),
        ("Bishal Ghimire", "9841111025", "", C.UK, S.PHONE_CALL, Q.BACHELOR, None, 4, "lead", "Called about UK MBA intakes. Will visit on Sunday.", None, "2027-01"),
        ("Laxmi Chaudhary", "9841111026", "laxmi.chaudhary@example.com", C.CANADA, S.EVENT_FAIR, Q.PLUS_TWO, None, 2, "lead", "Met at the education fair. Wants a diploma.", None, "2027-09"),
        ("Prabin Subedi", "9841111027", "prabin.subedi@example.com", C.AUSTRALIA, S.REFERRAL, Q.BACHELOR, sita, 1, "lead", "Referred by Aarav Sharma.", None, "2027-07"),
        ("Samjhana Rijal", "9841111028", "samjhana.rijal@example.com", C.NEW_ZEALAND, S.WALK_IN, Q.BACHELOR, None, 0, "lead", "Walked in today. Asking about New Zealand.", None, "2027-07"),
    ]
    lost_reasons = {"Rajan Dahal": "Chose another consultancy closer to home.",
                    "Kabita Nepal": "Decided to stay in Nepal for a job offer."}
    interview_at = at(TODAY + timedelta(days=4), 9, 30)
    for row in plan:
        (name, phone, email, country, source, qual, counsellor, start, how, note, course, intake) = row
        students[name] = story(
            name, phone, email, country, source, qual, counsellor, start, how, note, course,
            intake, past_degree="Bachelor's degree" if qual == Q.BACHELOR else "",
            sub_agent="Himalayan Edu Link" if source == S.SUB_AGENT else "",
            lost_reason=lost_reasons.get(name, ""),
            interview=interview_at if how == "visa_interview" else None,
        )
    print("students:", Student.objects.count())

    # Fuller profiles for a few, one passport about to run out (the warning shows).
    profiles = {
        "Aarav Sharma": ("1999-04-12", "male", "PA1234567", "2021-06-01", "2031-05-31"),
        "Sneha Gurung": ("2000-09-03", "female", "PA2345678", "2017-03-15", "2027-03-14"),
        "Kiran Magar": ("1998-12-21", "male", "PA3456789", "2019-08-10", "2029-08-09"),
        "Pooja Poudel": ("2001-02-28", "female", "PA4567890", "2022-01-20", "2032-01-19"),
    }
    for name, (dob, gender, number, issued, expires) in profiles.items():
        s = students[name]
        change_details(s, {
            "date_of_birth": date.fromisoformat(dob), "gender": gender, "marital_status": "single",
            "father_name": "Ram " + name.split()[1], "mother_name": "Sita " + name.split()[1],
            "passport_number": number, "passport_place_of_issue": "Kathmandu",
            "passport_issue_date": date.fromisoformat(issued),
            "passport_expiry_date": date.fromisoformat(expires),
            "target_level": "master",
        }, admin)
        add_academic_record(s, {"level": Q.PLUS_TWO, "institution": "Kathmandu Model College", "finished_year": 2017, "result": "GPA 3.4"})
        add_academic_record(s, {"level": Q.BACHELOR, "institution": "Tribhuvan University", "finished_year": 2022, "result": "First division"})
        add_test_score(s, {"test": "ielts", "overall": "7.0", "taken_on": ago(150), "details": "L7.5 R7.0 W6.5 S7.0"})
    add_test_score(students["Pooja Poudel"], {"test": "ielts", "overall": "6.0", "taken_on": ago(60), "details": "Writing 5.5, needs a retake"})
    add_test_score(students["Rachana Joshi"], {"test": "pte", "overall": "65", "taken_on": ago(50), "details": ""})

    # A Note edit, so History shows the old and new text.
    step(ago(5), edit_note, students["Sneha Gurung"],
         "Credibility interview booked for next week. Practise the questions; bring bank statements.", bikash)

    # Document checks: most verified, one rejected, the newest left waiting.
    newest = {}
    for d in Document.objects.order_by("pk"):
        newest[(d.student_id, d.document_type_id)] = d
    print("document types used:", sorted({d.document_type.name for d in newest.values()}))
    for d in newest.values():
        if d.student.name in ("Saroj Khadka", "Manisha Bhattarai", "Ujjwal Basnet"):
            continue  # waiting to be checked: shows on Home and the Documents page
        if d.student.name == "Asmita KC" and "+2" in d.document_type.name:
            check_document(d.student, d.pk, Verification.REJECTED, admin, "Scan is blurry; please bring the original.")
            continue
        check_document(d.student, d.pk, Verification.VERIFIED, d.student.assigned_counsellor or admin)

    # Follow-up Visits over the last weeks, and today's desk.
    follow = [("Nabin Pandey", 14, VR.BRINGING_DOCUMENTS, "Brought +2 and Bachelor transcripts"),
              ("Manisha Bhattarai", 12, VR.MAKING_A_PAYMENT, "Paid the IELTS class fee"),
              ("Sabina Rana", 7, VR.COUNSELLING_SESSION, "Second session: UK vs Australia costs"),
              ("Pooja Poudel", 3, VR.ASKING_ABOUT_PROGRESS, "Asked about the offer conditions"),
              ("Kiran Magar", 0, VR.ASKING_ABOUT_PROGRESS, "Asked when the visa outcome is expected"),
              ("Ujjwal Basnet", 0, VR.ATTENDING_A_CLASS, "IELTS morning class")]
    for name, days, reason, details in follow:
        step(ago(days), log_follow_up, students[name], reason, details, anita)

    # Extra Fees and Payments; some still owe.
    step(ago(39), add_fee, students["Manisha Bhattarai"], NewFee(fee_type=ft("IELTS class"), note="6 weeks", amount=15000), admin)
    step(ago(12), record_payment, students["Manisha Bhattarai"], NewPayment(amount=15000, paid_on=ago(12), method=PM.KHALTI, fee_type=ft("IELTS class")), anita)
    step(ago(32), add_fee, students["Ujjwal Basnet"], NewFee(fee_type=ft("IELTS class"), note="6 weeks", amount=15000), admin)
    step(ago(30), record_payment, students["Ujjwal Basnet"], NewPayment(amount=7500, paid_on=ago(30), method=PM.CASH, fee_type=ft("IELTS class"), note="first half"), anita)
    step(ago(20), add_fee, students["Sabina Rana"], NewFee(fee_type=ft("Other"), note="Counselling and SOP review", amount=5000), admin)

    # Commission from the Universities of the granted Students.
    def commission(name, university, intake, amount, status, received=None):
        c = add_commission(NewCommission(student=students[name], university=university, intake=intake, amount=amount), admin)
        if status != CommissionStatus.WAITING_FOR_ENROLMENT:
            change_commission(c.pk, CommissionStatus.CLAIM_SENT, admin)
        if status == CommissionStatus.RECEIVED:
            change_commission(c.pk, CommissionStatus.RECEIVED, admin)
            Commission.objects.filter(pk=c.pk).update(received_on=received)
    commission("Aarav Sharma", fed, "2027-03", 185000, CommissionStatus.RECEIVED, ago(40))
    commission("Priya Shrestha", vic, "2027-02", 160000, CommissionStatus.RECEIVED, ago(12))
    commission("Suman Rai", herts, "2027-01", 120000, CommissionStatus.CLAIM_SENT)
    commission("Nisha Tamang", con, "2027-01", 95000, CommissionStatus.WAITING_FOR_ENROLMENT)

    # Six months of Expenses.
    for months_ago in range(6, -1, -1):
        first = date(TODAY.year, TODAY.month, 1)
        y, m = first.year, first.month - months_ago
        while m <= 0:
            y, m = y - 1, m + 12
        day = lambda d: min(date(y, m, d), TODAY)  # noqa: E731
        add_expense(NewExpense(spent_on=day(1), category=EC.OFFICE_RENT, paid_to="Shrestha Properties", amount=60000, method=EM.BANK_TRANSFER), admin)
        add_expense(NewExpense(spent_on=day(5), category=EC.INTERNET, paid_to="WorldLink", amount=3500, method=EM.ESEWA), admin)
        add_expense(NewExpense(spent_on=day(6), category=EC.ELECTRICITY, paid_to="NEA", amount=4200 + 300 * (m % 3), method=EM.KHALTI), admin)
        add_expense(NewExpense(spent_on=day(4), category=EC.TEA_AND_SNACKS, paid_to="Corner Tea Shop", amount=2800, method=EM.CASH), admin)
        if months_ago > 0:
            add_expense(NewExpense(spent_on=day(28), category=EC.SALARIES, paid_to="Staff salaries", amount=285000, method=EM.BANK_TRANSFER), admin)
        if m % 2 == 0:
            add_expense(NewExpense(spent_on=day(10), category=EC.MARKETING, paid_to="Facebook ads", amount=15000, method=EM.CARD), admin)
    add_expense(NewExpense(spent_on=ago(70), category=EC.EDUCATION_FAIR, paid_to="Kathmandu Education Expo", amount=50000, method=EM.CHEQUE), admin)
    add_expense(NewExpense(spent_on=ago(25), category=EC.OFFICE_SUPPLIES, paid_to="Bhatbhateni", amount=6400, method=EM.CASH), admin)

    # Test-prep classes and their Attendance.
    ielts = create_batch(BatchDetails(name="IELTS Morning", test_type="ielts", teacher=prakash,
                                      time_slot="7:00-8:30 AM", starts_on=ago(26), ends_on=TODAY + timedelta(days=30)), admin)
    pte = create_batch(BatchDetails(name="PTE Evening", test_type="pte", teacher=prakash,
                                    time_slot="5:00-6:30 PM", starts_on=ago(12), ends_on=TODAY + timedelta(days=44)), sita)
    for name in ("Ujjwal Basnet", "Manisha Bhattarai", "Pooja Poudel", "Sabina Rana", "Nabin Pandey"):
        add_to_batch(ielts, students[name], sita)
    for name in ("Rachana Joshi", "Saroj Khadka", "Sunita Oli"):
        add_to_batch(pte, students[name], bikash)
    pattern = [Mark.PRESENT, Mark.PRESENT, Mark.PRESENT, Mark.LATE, Mark.PRESENT, Mark.ABSENT, Mark.PRESENT]
    for batch, names in ((ielts, ("Ujjwal Basnet", "Manisha Bhattarai", "Pooja Poudel", "Sabina Rana", "Nabin Pandey")),
                         (pte, ("Rachana Joshi", "Saroj Khadka", "Sunita Oli"))):
        day = batch.starts_on
        n = 0
        while day < TODAY:
            if day.weekday() != 5:  # no class on Saturday
                take_attendance(batch, day, {students[x].pk: pattern[(n + i) % len(pattern)] for i, x in enumerate(names)}, prakash)
                n += 1
            day += timedelta(days=1)

    # Tasks: overdue, today, soon; one done; one "Only me".
    def task(title, due, who, by, student=None, priority=TP.NORMAL, only_me=False, done=False):
        t = create_task(NewTask(title=title, due_on=due, assignee=who, student=student, priority=priority, only_me=only_me), by)
        if done:
            set_task_done(t.pk, done=True, by=who)
    task("Call Pooja about her IELTS retake date", ago(2), sita, sita, students["Pooja Poudel"], TP.HIGH)
    task("Check Sneha's bank statement before the interview", TODAY, bikash, bikash, students["Sneha Gurung"], TP.HIGH)
    task("Send Asmita a reminder to bring her original +2 transcript", TODAY, sita, anita, students["Asmita KC"])
    task("Follow up with Gita Pokharel (Facebook enquiry)", TODAY + timedelta(days=1), sita, anita, students["Gita Pokharel"])
    task("Prepare Rohan's documents for the second visa attempt", TODAY + timedelta(days=3), sita, sita, students["Rohan Karki"], TP.HIGH)
    task("Book Kiran's medical appointment", ago(5), sita, sita, students["Kiran Magar"], done=True)
    task("Collect Ujjwal's remaining class fee", TODAY + timedelta(days=2), anita, admin, students["Ujjwal Basnet"], TP.LOW)
    task("Print new brochures for the office", TODAY + timedelta(days=6), anita, admin)
    task("Mock test for the IELTS Morning class", TODAY + timedelta(days=5), prakash, prakash)
    task("Renew the office rent agreement", TODAY + timedelta(days=12), admin, admin, only_me=True)

    # One Transfer Request waiting for Sita's answer.
    ask_transfer(students["Nabin Pandey"], bikash, "I'm handling the other nursing applicants this intake.")

    # Clock-in: DEMO_OFFICE_NETWORK (the same public address as the frontend's DEV_CLIENT_IP) as
    # the Office Network, so clocking in works locally, and two weeks of days.
    office_address = os.environ.get("DEMO_OFFICE_NETWORK", "").strip()
    if office_address:
        add_office_network("Office Wi-Fi", parse_office_network(office_address, office_network_limits()), admin)
    for person in (admin, sita, bikash, anita, prakash):
        for back in range(14, 0, -1):
            day = ago(back)
            if day.weekday() == 5:
                continue
            start = at(day, 9, (person.pk * 7 + back) % 25)
            hours = 8 if (back + person.pk) % 6 else 6
            WorkSession.objects.create(staff=person, clocked_in_at=start, clocked_out_at=start + timedelta(hours=hours, minutes=10))

    print("payments:", Payment.objects.count(), "documents:", Document.objects.count(),
          "visits:", Visit.objects.count(), "history:", HistoryEntry.objects.count())
print("done")

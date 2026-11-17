from __future__ import annotations

from django.urls import URLPattern, path

from documents.views import (
    BinFileLinkView,
    BinView,
    CountryListsView,
    CountryListView,
    CourseDocumentsView,
    DeleteDocumentView,
    DocumentFilesView,
    DocumentLinkView,
    DocumentTypeListView,
    DocumentTypeView,
    DocumentVerificationView,
    NewCountryDocumentView,
    PaperworkView,
    PickableTypesView,
    PutInBinView,
    RestoreFromBinView,
    StudentAskedTypeView,
    StudentAskedView,
    StudentDocumentsView,
    TakeOutOfBinView,
)

ONE_STUDENT = "students/<int:student_id>/"
ONE_DOCUMENT = f"{ONE_STUDENT}documents/<int:document_id>/"
ONE_FILE = f"{ONE_DOCUMENT}files/<int:file_id>/"

urlpatterns: list[URLPattern] = [
    path(
        "configuration/document-types/",
        DocumentTypeListView.as_view(),
        name="configuration-document-types",
    ),
    path(
        "configuration/document-types/<int:type_id>/",
        DocumentTypeView.as_view(),
        name="configuration-document-type",
    ),
    path(
        "configuration/document-lists/",
        CountryListsView.as_view(),
        name="configuration-document-lists",
    ),
    # A Country by its value ("australia"); only those with a list (not Other) are found.
    path(
        "configuration/document-lists/<slug:country>/",
        CountryListView.as_view(),
        name="configuration-document-list",
    ),
    path(
        "configuration/document-lists/<slug:country>/new-document/",
        NewCountryDocumentView.as_view(),
        name="configuration-document-list-new",
    ),
    path(f"{ONE_STUDENT}documents/", StudentDocumentsView.as_view(), name="student-documents"),
    path(
        f"{ONE_STUDENT}documents/<int:document_id>/files/",
        DocumentFilesView.as_view(),
        name="student-document-files",
    ),
    path(
        f"{ONE_STUDENT}documents/<int:document_id>/files/<int:file_id>/link/",
        DocumentLinkView.as_view(),
        name="student-document-file-link",
    ),
    path(
        f"{ONE_STUDENT}documents/<int:document_id>/verification/",
        DocumentVerificationView.as_view(),
        name="student-document-verification",
    ),
    path(
        f"{ONE_STUDENT}documents/asked/",
        StudentAskedView.as_view(),
        name="student-documents-asked",
    ),
    path(
        f"{ONE_STUDENT}documents/asked/<int:type_id>/",
        StudentAskedTypeView.as_view(),
        name="student-document-asked",
    ),
    path("documents/students/", PaperworkView.as_view(), name="documents-paperwork"),
    # The Bin (issue 79): for a whole Document, and for one of its files.
    path(ONE_DOCUMENT, DeleteDocumentView.as_view(), name="student-document"),
    path(f"{ONE_DOCUMENT}bin/", PutInBinView.as_view(), name="student-document-bin"),
    path(f"{ONE_DOCUMENT}take-out/", TakeOutOfBinView.as_view(), name="student-document-take-out"),
    path(f"{ONE_DOCUMENT}restore/", RestoreFromBinView.as_view(), name="student-document-restore"),
    path(ONE_FILE, DeleteDocumentView.as_view(), name="student-document-file"),
    path(f"{ONE_FILE}bin/", PutInBinView.as_view(), name="student-document-file-bin"),
    path(f"{ONE_FILE}take-out/", TakeOutOfBinView.as_view(), name="student-document-file-take-out"),
    path(f"{ONE_FILE}restore/", RestoreFromBinView.as_view(), name="student-document-file-restore"),
    path("documents/bin/", BinView.as_view(), name="documents-bin"),
    path(
        "documents/bin/files/<int:file_id>/link/", BinFileLinkView.as_view(), name="bin-file-link"
    ),
    path("documents/types/", PickableTypesView.as_view(), name="documents-types"),
    path(
        "catalogue/courses/<int:course_id>/documents/",
        CourseDocumentsView.as_view(),
        name="catalogue-course-documents",
    ),
]

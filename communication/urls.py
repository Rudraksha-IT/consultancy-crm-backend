from __future__ import annotations

from django.urls import URLPattern, path

from communication.ready_made_views import (
    ManyDraftView,
    ManyReadyMadeView,
    StudentDraftView,
    StudentReadyMadeView,
)
from communication.views import (
    BulkMessagesView,
    ManyAttachableView,
    MessageListView,
    StudentAttachableView,
    StudentMessagesView,
)

urlpatterns: list[URLPattern] = [
    path("messages/", MessageListView.as_view(), name="messages"),
    path("messages/bulk/", BulkMessagesView.as_view(), name="messages-bulk"),
    path("messages/bulk/ready-made/", ManyReadyMadeView.as_view(), name="messages-bulk-ready-made"),
    path("messages/bulk/draft/", ManyDraftView.as_view(), name="messages-bulk-draft"),
    path(
        "messages/bulk/attachable/", ManyAttachableView.as_view(), name="messages-bulk-attachable"
    ),
    path(
        "students/<uuid:student_id>/emails/attachable/",
        StudentAttachableView.as_view(),
        name="student-email-attachable",
    ),
    path(
        "students/<uuid:student_id>/emails/ready-made/",
        StudentReadyMadeView.as_view(),
        name="student-ready-made",
    ),
    path(
        "students/<uuid:student_id>/emails/draft/",
        StudentDraftView.as_view(),
        name="student-email-draft",
    ),
    path(
        "students/<uuid:student_id>/messages/",
        StudentMessagesView.as_view(),
        name="student-messages",
    ),
]

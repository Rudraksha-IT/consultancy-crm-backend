from __future__ import annotations

from django.urls import URLPattern, path

from communication.views import BulkMessagesView, MessageListView, StudentMessagesView

urlpatterns: list[URLPattern] = [
    path("messages/", MessageListView.as_view(), name="messages"),
    path("messages/bulk/", BulkMessagesView.as_view(), name="messages-bulk"),
    path(
        "students/<int:student_id>/messages/",
        StudentMessagesView.as_view(),
        name="student-messages",
    ),
]

from __future__ import annotations

from django.urls import URLPattern, path

from communication.views import (
    BulkMessagesView,
    MessageListView,
    ProviderSettingsView,
    StudentMessagesView,
    TestSmsView,
)

urlpatterns: list[URLPattern] = [
    path(
        "communication/settings/",
        ProviderSettingsView.as_view(),
        name="communication-settings",
    ),
    path(
        "communication/settings/test-sms/",
        TestSmsView.as_view(),
        name="communication-test-sms",
    ),
    path("messages/", MessageListView.as_view(), name="messages"),
    path("messages/bulk/", BulkMessagesView.as_view(), name="messages-bulk"),
    path(
        "students/<int:student_id>/messages/",
        StudentMessagesView.as_view(),
        name="student-messages",
    ),
]

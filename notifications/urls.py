from __future__ import annotations

from django.urls import URLPattern, path

from notifications.views import NotificationListView, NotificationView, ReadAllView

urlpatterns: list[URLPattern] = [
    path("notifications/", NotificationListView.as_view(), name="notifications"),
    path("notifications/read-all/", ReadAllView.as_view(), name="notifications-read-all"),
    path(
        "notifications/<int:notification_id>/",
        NotificationView.as_view(),
        name="notification",
    ),
]

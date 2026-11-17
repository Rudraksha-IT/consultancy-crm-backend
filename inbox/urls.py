from __future__ import annotations

from django.urls import URLPattern, path

from inbox.views import ConversationView, InboxCheckView, InboxPeopleView, InboxView

urlpatterns: list[URLPattern] = [
    path("inbox/", InboxView.as_view(), name="inbox"),
    path("inbox/check/", InboxCheckView.as_view(), name="inbox-check"),
    path("inbox/people/", InboxPeopleView.as_view(), name="inbox-people"),
    path("inbox/<int:person_id>/", ConversationView.as_view(), name="inbox-conversation"),
]

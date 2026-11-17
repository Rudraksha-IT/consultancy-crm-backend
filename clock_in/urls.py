from __future__ import annotations

from django.urls import URLPattern, path

from clock_in.views import (
    ApproveWorkFromHomeView,
    CancelWorkFromHomeView,
    ClockInView,
    ClockOutView,
    DeclineWorkFromHomeView,
    EndWorkFromHomeView,
    HolidaysView,
    HolidayView,
    MissedTimeView,
    MonthView,
    MyClockView,
    MyWorkFromHomeView,
    PersonWorkFromHomeView,
    SetClockOutView,
    TeamMonthView,
    TeamNowView,
    WeeklyOffView,
    WorkingDayView,
)

urlpatterns: list[URLPattern] = [
    path("clock/", MyClockView.as_view(), name="clock"),
    path("clock/in/", ClockInView.as_view(), name="clock-in"),
    path("clock/out/", ClockOutView.as_view(), name="clock-out"),
    path("clock/month/", MonthView.as_view(), name="clock-month"),
    path("clock/team/", TeamMonthView.as_view(), name="clock-team"),
    path("clock/team/now/", TeamNowView.as_view(), name="clock-team-now"),
    path("clock/working-day/", WorkingDayView.as_view(), name="clock-working-day"),
    path("clock/days-off/", WeeklyOffView.as_view(), name="clock-days-off"),
    path("clock/holidays/", HolidaysView.as_view(), name="clock-holidays"),
    path("clock/holidays/<int:holiday_id>/", HolidayView.as_view(), name="clock-holiday"),
    path(
        "clock/sessions/<int:session_id>/clock-out/",
        SetClockOutView.as_view(),
        name="clock-set-clock-out",
    ),
    path(
        "clock/staff/<int:staff_id>/missed-time/",
        MissedTimeView.as_view(),
        name="clock-missed-time",
    ),
    path("clock/work-from-home/", MyWorkFromHomeView.as_view(), name="clock-work-from-home"),
    path(
        "clock/work-from-home/<int:dates_id>/cancel/",
        CancelWorkFromHomeView.as_view(),
        name="clock-work-from-home-cancel",
    ),
    path(
        "clock/work-from-home/<int:dates_id>/approve/",
        ApproveWorkFromHomeView.as_view(),
        name="clock-work-from-home-approve",
    ),
    path(
        "clock/work-from-home/<int:dates_id>/decline/",
        DeclineWorkFromHomeView.as_view(),
        name="clock-work-from-home-decline",
    ),
    path(
        "clock/work-from-home/<int:dates_id>/end/",
        EndWorkFromHomeView.as_view(),
        name="clock-work-from-home-end",
    ),
    path(
        "clock/staff/<int:staff_id>/work-from-home/",
        PersonWorkFromHomeView.as_view(),
        name="clock-person-work-from-home",
    ),
]

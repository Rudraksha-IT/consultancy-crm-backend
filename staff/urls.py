from __future__ import annotations

from django.urls import URLPattern, path

from auth_tokens.views import LogoutView, RefreshView
from staff.employee_views import (
    CounsellorChoicesView,
    EmployeeAssignedStudentsView,
    EmployeeDeactivateView,
    EmployeeInviteView,
    EmployeeListView,
    EmployeeView,
)
from staff.join_views import JoinCodeView, JoinCompleteView, JoinRequestView, LoginStepView
from staff.my_settings_views import (
    MyDetailsView,
    MyDevicesView,
    MyPasswordView,
    SignOutOtherDevicesView,
)
from staff.reset_views import ResetCodeView, ResetCompleteView, ResetRequestView
from staff.two_factor_views import (
    EmployeeTwoFactorResetView,
    EmployeeTwoFactorView,
    MyTwoFactorView,
    StaffTwoFactorConfirmView,
    StaffTwoFactorSetupView,
    StaffTwoFactorVerifyView,
)
from staff.views import LoginView, StaffContextView

urlpatterns: list[URLPattern] = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/login/next/", LoginStepView.as_view(), name="login-step"),
    path("auth/refresh/", RefreshView.as_view(), name="refresh"),
    path("auth/two-factor/setup/", StaffTwoFactorSetupView.as_view(), name="two-factor-setup"),
    path(
        "auth/two-factor/confirm/", StaffTwoFactorConfirmView.as_view(), name="two-factor-confirm"
    ),
    path("auth/two-factor/verify/", StaffTwoFactorVerifyView.as_view(), name="two-factor-verify"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/password-reset/", ResetRequestView.as_view(), name="password-reset"),
    path("auth/password-reset/code/", ResetCodeView.as_view(), name="password-reset-code"),
    path(
        "auth/password-reset/complete/",
        ResetCompleteView.as_view(),
        name="password-reset-complete",
    ),
    path("auth/join/", JoinRequestView.as_view(), name="join"),
    path("auth/join/code/", JoinCodeView.as_view(), name="join-code"),
    path("auth/join/complete/", JoinCompleteView.as_view(), name="join-complete"),
    path("context/", StaffContextView.as_view(), name="staff-context"),
    path("me/", MyDetailsView.as_view(), name="my-details"),
    path("me/password/", MyPasswordView.as_view(), name="my-password"),
    path("me/devices/", MyDevicesView.as_view(), name="my-devices"),
    path("me/two-factor/", MyTwoFactorView.as_view(), name="my-two-factor"),
    path(
        "me/devices/sign-out-others/",
        SignOutOtherDevicesView.as_view(),
        name="sign-out-other-devices",
    ),
    path("staff/", EmployeeListView.as_view(), name="employees"),
    path("staff/counsellors/", CounsellorChoicesView.as_view(), name="counsellor-choices"),
    path("staff/<int:staff_id>/", EmployeeView.as_view(), name="employee"),
    path(
        "staff/<int:staff_id>/deactivate/",
        EmployeeDeactivateView.as_view(),
        name="employee-deactivate",
    ),
    path(
        "staff/<int:staff_id>/assigned-students/",
        EmployeeAssignedStudentsView.as_view(),
        name="employee-assigned-students",
    ),
    path("staff/<int:staff_id>/invite/", EmployeeInviteView.as_view(), name="employee-invite"),
    path(
        "staff/<int:staff_id>/two-factor/",
        EmployeeTwoFactorView.as_view(),
        name="employee-two-factor",
    ),
    path(
        "staff/<int:staff_id>/two-factor/reset/",
        EmployeeTwoFactorResetView.as_view(),
        name="employee-two-factor-reset",
    ),
]

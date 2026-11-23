from __future__ import annotations

from django.urls import URLPattern, URLResolver, include, path

from auth_tokens.views import LogoutView, RefreshView
from platform_admin.brand_views import (
    LogoInUseView,
    ProductLogoView,
    SavedLogoFileView,
    SavedLogosView,
    SavedLogoView,
)
from platform_admin.email_views import PlatformEmailView, PlatformTestEmailView
from platform_admin.reset_views import (
    PlatformResetConfirmView,
    PlatformResetRequestView,
    PlatformResetVerifyView,
)
from platform_admin.team_views import (
    PlatformAdminDeactivateView,
    PlatformAdminReactivateView,
    PlatformAdminResendView,
    PlatformAdminsView,
    PlatformJoinCompleteView,
    PlatformJoinRequestView,
    PlatformJoinVerifyView,
    PlatformLoginStepView,
)
from platform_admin.two_factor_views import (
    MyTwoFactorView,
    TwoFactorConfirmView,
    TwoFactorSetupView,
    TwoFactorVerifyView,
)
from platform_admin.views import (
    ConsultancyAdminsView,
    ConsultancyListView,
    ConsultancyModulesView,
    ConsultancyRetrySetupView,
    ConsultancyStatusView,
    ConsultancyThemeView,
    PlatformLoginView,
    PlatformMeView,
    PlatformPasswordView,
    ProductDomainView,
    SupportVisitListView,
    SupportVisitStartView,
)

urlpatterns: list[URLPattern | URLResolver] = [
    path("", include("platform_admin.brand_urls")),
    path("brand/logo/", ProductLogoView.as_view(), name="product-logo"),
    path("brand/logos/", SavedLogosView.as_view(), name="saved-logos"),
    path("brand/logos/<saved_logo:logo_id>/", SavedLogoView.as_view(), name="saved-logo"),
    path(
        "brand/logos/<saved_logo:logo_id>/file/",
        SavedLogoFileView.as_view(),
        name="saved-logo-file",
    ),
    path("brand/in-use/", LogoInUseView.as_view(), name="product-logo-in-use"),
    path("email/", PlatformEmailView.as_view(), name="platform-email"),
    path("email/test/", PlatformTestEmailView.as_view(), name="platform-test-email"),
    path("auth/login/", PlatformLoginView.as_view(), name="platform-login"),
    path("auth/login/next/", PlatformLoginStepView.as_view(), name="platform-login-step"),
    path("auth/refresh/", RefreshView.as_view(), name="platform-refresh"),
    path("auth/logout/", LogoutView.as_view(), name="platform-logout"),
    path("auth/password-reset/", PlatformResetRequestView.as_view(), name="platform-reset-request"),
    path(
        "auth/password-reset/verify/",
        PlatformResetVerifyView.as_view(),
        name="platform-reset-verify",
    ),
    path(
        "auth/password-reset/confirm/",
        PlatformResetConfirmView.as_view(),
        name="platform-reset-confirm",
    ),
    path("auth/join/", PlatformJoinRequestView.as_view(), name="platform-join"),
    path("auth/join/verify/", PlatformJoinVerifyView.as_view(), name="platform-join-verify"),
    path("auth/join/complete/", PlatformJoinCompleteView.as_view(), name="platform-join-complete"),
    path("admins/", PlatformAdminsView.as_view(), name="platform-admins"),
    path(
        "admins/<platform_admin:admin_id>/resend/",
        PlatformAdminResendView.as_view(),
        name="platform-admin-resend",
    ),
    path(
        "admins/<platform_admin:admin_id>/deactivate/",
        PlatformAdminDeactivateView.as_view(),
        name="platform-admin-deactivate",
    ),
    path(
        "admins/<platform_admin:admin_id>/reactivate/",
        PlatformAdminReactivateView.as_view(),
        name="platform-admin-reactivate",
    ),
    path("auth/two-factor/setup/", TwoFactorSetupView.as_view(), name="platform-2fa-setup"),
    path("auth/two-factor/confirm/", TwoFactorConfirmView.as_view(), name="platform-2fa-confirm"),
    path("auth/two-factor/verify/", TwoFactorVerifyView.as_view(), name="platform-2fa-verify"),
    path("me/", PlatformMeView.as_view(), name="platform-me"),
    path("me/two-factor/", MyTwoFactorView.as_view(), name="platform-me-two-factor"),
    path("me/password/", PlatformPasswordView.as_view(), name="platform-me-password"),
    path("product-domain/", ProductDomainView.as_view(), name="platform-product-domain"),
    path("consultancies/", ConsultancyListView.as_view(), name="platform-consultancies"),
    path(
        "consultancies/<consultancy:consultancy_id>/admins/",
        ConsultancyAdminsView.as_view(),
        name="platform-consultancy-admins",
    ),
    path(
        "consultancies/<consultancy:consultancy_id>/status/",
        ConsultancyStatusView.as_view(),
        name="platform-consultancy-status",
    ),
    path(
        "consultancies/<consultancy:consultancy_id>/retry-setup/",
        ConsultancyRetrySetupView.as_view(),
        name="platform-consultancy-retry-setup",
    ),
    path(
        "consultancies/<consultancy:consultancy_id>/modules/",
        ConsultancyModulesView.as_view(),
        name="platform-consultancy-modules",
    ),
    path(
        "consultancies/<consultancy:consultancy_id>/support-visits/",
        SupportVisitStartView.as_view(),
        name="platform-support-visit-start",
    ),
    path(
        "consultancies/<consultancy:consultancy_id>/theme/",
        ConsultancyThemeView.as_view(),
        name="platform-consultancy-theme",
    ),
    path("support-visits/", SupportVisitListView.as_view(), name="platform-support-visits"),
]

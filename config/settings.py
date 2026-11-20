"""Django settings, configured from environment variables (see ADR 0001 for tenancy)."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from config.jobs import read_job_settings
from config.jwt import read_access_token_lifetime
from config.logging import (
    build_logging,
    read_log_format,
    read_log_level,
    read_slow_request_ms,
)
from config.office_networks import read_office_network_limits
from config.two_factor import read_two_factor_settings

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-local-development-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

PRODUCT_DOMAIN = os.environ.get("PRODUCT_DOMAIN", "localhost")
# Never "*": only Consultancy and Platform subdomains of the product domain (ADR 0002).
ALLOWED_HOSTS = [f".{PRODUCT_DOMAIN}"]
# Platform Admins work on this subdomain; it belongs to no Consultancy.
PLATFORM_SUBDOMAIN = os.environ.get("PLATFORM_SUBDOMAIN", "admin")
# The Central login (issue 44): Staff type their email here and are emailed their offices' links.
# It belongs to no Consultancy either.
CENTRAL_LOGIN_SUBDOMAIN = os.environ.get("CENTRAL_LOGIN_SUBDOMAIN", "login")
# Keys the hash of each Staff email in the Central login's index (at least 32 characters; see
# central_login.directory), never DJANGO_SECRET_KEY. Changing it means rebuild_staff_directory.
STAFF_DIRECTORY_KEY = os.environ.get("STAFF_DIRECTORY_KEY", "")

# Only the Next.js server calls Django (ADR 0002). It sends the browser's host in
# X-Forwarded-Host, which picks the Consultancy, and the browser's scheme in X-Forwarded-Proto.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# The shared secret the Next.js server sends as X-Internal-Token; at least 32 characters, or
# the server refuses to start (tenancy.middleware.InternalTokenMiddleware).
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")

# Login tokens (ADR 0003). Signed with their own key (at least 32 bytes, or the server refuses
# to start: auth_tokens.middleware), never DJANGO_SECRET_KEY.
JWT_SIGNING_KEY = os.environ.get("JWT_SIGNING_KEY", "")
# 15 minutes; JWT_ACCESS_TOKEN_MINUTES can only shorten it (1 to 15), e.g. to check refreshing.
JWT_ACCESS_TOKEN_LIFETIME = read_access_token_lifetime(os.environ)
JWT_REFRESH_TOKEN_LIFETIME = timedelta(days=7)

# Two-step login (issue 61). Authenticator secrets are sealed with TWO_FACTOR_SECRETS_KEY, a
# Fernet key (from Secrets Manager or SSM), never SECRET_KEY. PLATFORM_ADMIN_TWO_FACTOR_DEFAULT
# (true/false, default false) is whether a Platform Admin made by the deploy starts with it on;
# true needs the key. A bad value stops the server at start-up.
_TWO_FACTOR = read_two_factor_settings(os.environ)
TWO_FACTOR_SECRETS_KEY = _TWO_FACTOR.secrets_key
PLATFORM_ADMIN_TWO_FACTOR_DEFAULT = _TWO_FACTOR.platform_admin_default
# Whether every new Staff member starts with two-step login on (issue 62); existing Staff keep
# theirs.
STAFF_TWO_FACTOR_DEFAULT = _TWO_FACTOR.staff_default

# Office Networks (issue 55, ADR 0004): how many an office may have (OFFICE_NETWORKS_MAX, default
# 20) and the widest range (OFFICE_NETWORK_IPV4_WIDEST_PREFIX 24, OFFICE_NETWORK_IPV6_WIDEST_PREFIX
# 48); ranges can only be made narrower. A bad value stops the server at start-up.
OFFICE_NETWORK_LIMITS = read_office_network_limits(os.environ)

# The job queue's worker (issue 60): it checks the queue every JOB_POLL_SECONDS (1-60, default 5)
# and a job running longer than JOB_TIMEOUT_SECONDS (60-86400, default 900) counts as stuck.
JOB_SETTINGS = read_job_settings(os.environ)

SHARED_APPS = [
    # First, so its logged `migrate_schemas` replaces django-tenants' own (issue 58).
    "operations",
    "django_tenants",
    "tenancy",
    "platform_admin",
    "central_login",
    # The job queue (issue 60): a table in public, run by the `worker` container.
    "jobs",
    # The email builder, its templates and the demo command (ADR 0023). No models.
    "emails",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    # Platform Admins' refresh tokens live in public; each Consultancy keeps its Staff's own.
    "auth_tokens",
    # Two-step login (issue 61): Platform Admins' in public, Staff's in their own schema (62).
    "two_factor",
]

TENANT_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "staff",
    "auth_tokens",
    "two_factor",
    "codes",
    "students",
    "history",
    "catalogue",
    "applications",
    "documents",
    "course_files",
    "document_examples",
    "fee_types",
    "payments",
    "fees",
    "expenses",
    "commission",
    "tasks",
    "notifications",
    "batches",
    "communication",
    "home",
    "visits",
    "reports",
    "search",
    "exports",
    "office_networks",
    "clock_in",
    "transfer_requests",
    "staff_progress",
    "inbox",
]

INSTALLED_APPS = [*SHARED_APPS, *(app for app in TENANT_APPS if app not in SHARED_APPS)]

TENANT_MODEL = "tenancy.Consultancy"
TENANT_DOMAIN_MODEL = "tenancy.Domain"

MIDDLEWARE = [
    # First, so every request (even a refused one) is logged with its correlation ID.
    "config.middleware.RequestLogMiddleware",
    "tenancy.middleware.InternalTokenMiddleware",
    "tenancy.middleware.ConsultancyHostMiddleware",
    # Right after the host picks the schema: a token issued for another one is refused (401).
    "auth_tokens.middleware.TokenConsultancyMiddleware",
    # Then the Consultancy Status decides what gets through (issue 42).
    "tenancy.middleware.ConsultancyAccessMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
PLATFORM_URLCONF = "config.platform_urls"
CENTRAL_LOGIN_URLCONF = "config.central_login_urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": os.environ.get("POSTGRES_DB", "crm"),
        "USER": os.environ.get("POSTGRES_USER", "crm"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "crm"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}
DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Staff live in each Consultancy's schema. Platform Admins are a second user model in public;
# both are found from their login token (auth_tokens), never from a session.
AUTH_USER_MODEL = "staff.Staff"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    # Django sets no cookie and needs no CSRF check: the Next.js server holds the tokens and
    # refuses cross-origin changes itself (ADR 0003).
    "DEFAULT_AUTHENTICATION_CLASSES": ["auth_tokens.authentication.BearerTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # Logs every failure the standard way and answers an unexpected one with a reference.
    "EXCEPTION_HANDLER": "config.exceptions.handle_exception",
}

# Every email the product sends (ADR 0023): Codes, the Central login list and emails to Students,
# all through this one SMTP account, set only here. Offices never set up an account of their own.
# Locally the console or a catch-all SMTP server (MailHog in Compose); tests replace it with
# Django's in-memory outbox.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "false").lower() == "true"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", f"no-reply@{PRODUCT_DOMAIN}")

# Document files (issue 16): private S3, one prefix per Consultancy schema. In AWS the server's IAM
# role gives access; locally Compose points these at MinIO (and AWS_* keys in the environment).
DOCUMENT_STORAGE = os.environ.get("DOCUMENT_STORAGE", "documents.storage.S3DocumentStorage")
DOCUMENTS_BUCKET = os.environ.get("DOCUMENTS_BUCKET", "")
DOCUMENTS_S3_REGION = os.environ.get("DOCUMENTS_S3_REGION", "ap-south-1")
# Empty means AWS S3 itself. The public endpoint signs links for the browser (MinIO locally).
DOCUMENTS_S3_ENDPOINT_URL = os.environ.get("DOCUMENTS_S3_ENDPOINT_URL", "")
DOCUMENTS_S3_PUBLIC_ENDPOINT_URL = os.environ.get("DOCUMENTS_S3_PUBLIC_ENDPOINT_URL", "")
# The files of one Document upload (documents.files.MAX_FILES_AT_ONCE, 10) plus one, so that one
# too many is refused with a clear message and more than that by Django itself. Larger files go
# to a temporary file, not memory.
DATA_UPLOAD_MAX_NUMBER_FILES = 11

# One email to many Students (issue 26) is sent one by one while the request waits, so it is
# kept short: at most this many Students, and anyone not reached in this many seconds is recorded
# as failed. Keep it under gunicorn's --timeout and the Next.js bulk-send timeout.
BULK_MAX_STUDENTS = 200
BULK_SEND_SECONDS = 60

# The standard log format (issue 43): JSON on stdout at INFO; LOG_LEVEL=DEBUG only locally.
LOGGING = build_logging(read_log_level(os.environ), read_log_format(os.environ))
# A request slower than this (milliseconds) is logged at WARNING (issue 58).
LOG_SLOW_REQUEST_MS = read_slow_request_ms(os.environ)

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kathmandu"
USE_I18N = False
USE_TZ = True

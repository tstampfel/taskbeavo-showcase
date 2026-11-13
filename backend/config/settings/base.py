"""Settings shared by every environment. Environment modules override what differs."""

import base64
import datetime
import hashlib
from pathlib import Path

import dj_database_url
from celery.schedules import crontab

from config.env import env, env_bool, env_list

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# One of "local", "test", "staging" or "production". Production-only behaviour
# (introspection off, strict security headers) keys off this value.
APP_ENV = env("APP_ENV", "local")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env_bool("DEBUG", default=False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    # The admin with the audited staff site (staff.sites); see staff.roles for who sees what.
    "staff.admin_apps.StaffAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "channels",
    "strawberry_django",
    "core",
    "payments",
    "accounts",
    "notifications",
    "uploads",
    "areas",
    "care_contacts",
    "helpers",
    "moderation",
    "tasks",
    "offers",
    "bookings",
    "legal",
    "vat",
    "staff",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL"),
        engine="django.contrib.gis.db.backends.postgis",
        conn_max_age=int(env("DB_CONN_MAX_AGE", "0")),
        conn_health_checks=True,
    ),
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    },
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = None
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Europe/Zagreb"
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BEAT_SCHEDULE: dict[str, dict[str, object]] = {
    # Safety net for outbox rows whose commit hook or retry message never ran.
    "outbox-sweep": {"task": "core.outbox.sweep", "schedule": 60.0},
    # Trims raw Stripe payloads past their retention (payments/events.py).
    "stripe-events-trim": {"task": "payments.stripe_events.trim", "schedule": 24 * 60 * 60.0},
    # Handles Stripe events stored before their handler was deployed (payments/webhooks.py).
    "stripe-events-dispatch-pending": {
        "task": "payments.stripe_events.dispatch_pending",
        "schedule": 5 * 60.0,
    },
    # Cancels or refunds failed checkouts whose own settle task gave up (bookings/checkout.py).
    "bookings-checkout-sweep-unsettled": {
        "task": "bookings.checkout.sweep_unsettled",
        "schedule": 15 * 60.0,
    },
    # Re-runs cancellation settlements whose own task gave up (bookings/settlement.py).
    "bookings-cancellation-sweep": {
        "task": "bookings.cancellation.sweep",
        "schedule": 15 * 60.0,
    },
    # Flags withdrawals after the start whose share support hasn't entered (bookings/cancel.py).
    "bookings-cancellation-overdue-shares": {
        "task": "bookings.cancellation.flag_overdue_shares",
        "schedule": 60 * 60.0,
    },
    # Flags settlements sent to Stripe that no webhook confirmed in 24 h (bookings/settlement.py).
    "bookings-cancellation-flag-unconfirmed": {
        "task": "bookings.cancellation.flag_unconfirmed",
        "schedule": 60 * 60.0,
    },
    # No-fault cancellation requests unanswered for 48 h (bookings/no_fault.py).
    "bookings-no-fault-settle-unanswered": {
        "task": "bookings.no_fault.settle_unanswered",
        "schedule": 15 * 60.0,
    },
    # Day-of-job reminders, the "Are you on your way?" prompt and the poster's no-show
    # question (bookings/arrival.py). Every minute: the prompt is due 15 min after the start.
    "bookings-arrival-timeline": {
        "task": "bookings.arrival.run_timeline",
        "schedule": 60.0,
    },
    # No-shows the helper didn't contest within 24 h: the refund opens (bookings/arrival.py).
    "bookings-arrival-settle-uncontested-no-shows": {
        "task": "bookings.arrival.settle_uncontested_no_shows",
        "schedule": 15 * 60.0,
    },
    # Ends checkouts nobody paid within CHECKOUT_ABANDON_AFTER_MINUTES (bookings/checkout.py).
    "bookings-checkout-expire-abandoned": {
        "task": "bookings.checkout.expire_abandoned",
        "schedule": 5 * 60.0,
    },
}
# Minutes a checkout stays open before its PaymentIntent is cancelled and the offers
# reopen (plan: "Checkout with card authentication (3D Secure)").
CHECKOUT_ABANDON_AFTER_MINUTES = int(env("CHECKOUT_ABANDON_AFTER_MINUTES", "30"))
# The hard limit from acceptance, however many cards the poster tries.
CHECKOUT_MAX_MINUTES = int(env("CHECKOUT_MAX_MINUTES", "60"))

# Outbox publisher retries (core/outbox.py): 30 s, 1 min, 2 min, ... capped at 1 h.
OUTBOX_MAX_ATTEMPTS = int(env("OUTBOX_MAX_ATTEMPTS", "10"))
OUTBOX_BACKOFF_BASE_SECONDS = int(env("OUTBOX_BACKOFF_BASE_SECONDS", "30"))
OUTBOX_BACKOFF_MAX_SECONDS = int(env("OUTBOX_BACKOFF_MAX_SECONDS", "3600"))
# A handler holds a row lock and a transaction open while it runs; this bounds it.
OUTBOX_PUBLISH_SOFT_TIME_LIMIT_SECONDS = int(env("OUTBOX_PUBLISH_SOFT_TIME_LIMIT_SECONDS", "60"))

# Days a raw Stripe event payload is kept in full before it is trimmed to non-personal
# fields (payments/events.py).
STRIPE_EVENT_PAYLOAD_RETENTION_DAYS = int(env("STRIPE_EVENT_PAYLOAD_RETENTION_DAYS", "30"))
# Stripe (payments/stripe_client.py, payments/webhooks.py). Required when deployed;
# empty locally until backend/.env sets them. STRIPE_WEBHOOK_SECRET may list several
# comma-separated whsec_ secrets: the platform and Connect endpoints each have one, and a
# rotated secret overlaps its successor. Live keys belong to production, and only there.
_needs_stripe = APP_ENV not in ("local", "test", "build")
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY") if _needs_stripe else env("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRETS = (
    env_list("STRIPE_WEBHOOK_SECRET") if _needs_stripe else env_list("STRIPE_WEBHOOK_SECRET", "")
)
# Production takes live keys only and every other environment test keys only; the
# webhook drops events from the other mode (payments/webhooks.py).
STRIPE_LIVEMODE = APP_ENV == "production"
if STRIPE_SECRET_KEY and STRIPE_SECRET_KEY.startswith(("sk_live_", "rk_live_")) != STRIPE_LIVEMODE:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        f"STRIPE_SECRET_KEY is a {'test' if STRIPE_LIVEMODE else 'live'} key; "
        f"APP_ENV {APP_ENV} takes {'live' if STRIPE_LIVEMODE else 'test'} keys only."
    )
# Demo payments (payments/demo_stripe.py, BEAV-198): a stand-in for Stripe's API inside
# this process, so the showcase runs checkout, the webhook, refunds and payouts without
# Stripe keys. Every money move still goes through the real webhook handler. Never in
# production, and never next to a live key.
DEMO_PAYMENTS = env_bool("DEMO_PAYMENTS")
if DEMO_PAYMENTS and (
    APP_ENV == "production" or STRIPE_SECRET_KEY.startswith(("sk_live_", "rk_live_"))
):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("DEMO_PAYMENTS can't be on in production or with a live key.")
# Browser origins the demo card form may confirm from (payments/demo_views.py).
DEMO_PAYMENTS_ALLOWED_ORIGINS = env_list(
    "DEMO_PAYMENTS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
)

# --- Accounts and sign-in (accounts/, BEAV-70) ---------------------------------------
AUTH_USER_MODEL = "accounts.User"
# Signs access and WebSocket JWTs (accounts/sessions.py). Separate from SECRET_KEY so either
# can be rotated alone (rotating it signs everyone out within ACCESS_TOKEN_TTL_SECONDS).
# Render generates it; local, test and the image build fall back to SECRET_KEY.
JWT_SIGNING_KEY = (
    env("JWT_SIGNING_KEY", SECRET_KEY)
    if APP_ENV in ("local", "test", "build")
    else env("JWT_SIGNING_KEY")
)
ACCESS_TOKEN_TTL_SECONDS = int(env("ACCESS_TOKEN_TTL_SECONDS", "900"))
WS_TOKEN_TTL_SECONDS = int(env("WS_TOKEN_TTL_SECONDS", "60"))
REFRESH_TOKEN_TTL_DAYS = int(env("REFRESH_TOKEN_TTL_DAYS", "30"))
# A used refresh token still refreshes this long after its use (parallel browser requests).
REFRESH_TOKEN_REUSE_GRACE_SECONDS = int(env("REFRESH_TOKEN_REUSE_GRACE_SECONDS", "10"))
EMAIL_CODE_TTL_SECONDS = int(env("EMAIL_CODE_TTL_SECONDS", "600"))
EMAIL_CODE_MAX_ATTEMPTS = int(env("EMAIL_CODE_MAX_ATTEMPTS", "5"))
# Limits on email codes (accounts/signin.py, BEAV-76): code requests per sliding hour per
# address and per client address (IPv6 per /64; generous for shared Wi-Fi), and a lockout
# of an address after this many wrong codes within the window, across reissued codes.
EMAIL_CODE_REQUESTS_PER_HOUR_PER_EMAIL = int(env("EMAIL_CODE_REQUESTS_PER_HOUR_PER_EMAIL", "5"))
EMAIL_CODE_REQUESTS_PER_HOUR_PER_IP = int(env("EMAIL_CODE_REQUESTS_PER_HOUR_PER_IP", "20"))
# Wrong codes: 5 from one source block that source for the address, 20 from all sources
# lock the address (decision 2026-11-04, accounts/signin.py).
EMAIL_CODE_LOCKOUT_AFTER_FAILURES = int(env("EMAIL_CODE_LOCKOUT_AFTER_FAILURES", "5"))
EMAIL_CODE_ADDRESS_FAILURES_PER_HOUR = int(env("EMAIL_CODE_ADDRESS_FAILURES_PER_HOUR", "20"))
EMAIL_CODE_FAILURE_WINDOW_SECONDS = int(env("EMAIL_CODE_FAILURE_WINDOW_SECONDS", "3600"))
EMAIL_CODE_LOCKOUT_SECONDS = int(env("EMAIL_CODE_LOCKOUT_SECONDS", "1800"))
# The "This wasn't me" link in the new-device email logs out everywhere for this long.
SIGN_OUT_LINK_MAX_AGE_SECONDS = int(env("SIGN_OUT_LINK_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
# MaxMind GeoLite2 City database for the new-device email's coarse location ("Munich,
# Germany"). Optional: without it the email names only the device. The web process downloads
# it into this writable path with the MaxMind account below, weekly, and deletes it once it is
# more than 30 days old (GeoLite EULA, BEAV-190, accounts/geolite.py). The key is a secret.
GEOIP_CITY_DATABASE = env("GEOIP_CITY_DATABASE", "")
MAXMIND_ACCOUNT_ID = env("MAXMIND_ACCOUNT_ID", "")
MAXMIND_LICENSE_KEY = env("MAXMIND_LICENSE_KEY", "")
SIGNUP_TICKET_MAX_AGE_SECONDS = int(env("SIGNUP_TICKET_MAX_AGE_SECONDS", "1800"))
# Google and Apple sign-in (accounts/providers.py, BEAV-71). An ID token is accepted only
# when its audience is one of these client IDs; unset ones are left out, and a provider
# with none configured refuses every token. They are public identifiers, not secrets.
GOOGLE_OAUTH_CLIENT_IDS = [
    client_id
    for client_id in (
        env("GOOGLE_OAUTH_WEB_CLIENT_ID", ""),
        env("GOOGLE_OAUTH_IOS_CLIENT_ID", ""),
        env("GOOGLE_OAUTH_ANDROID_CLIENT_ID", ""),
    )
    if client_id
]
# The iOS app's bundle ID (native sign-in) and the Services ID (web sign-in).
APPLE_SIGN_IN_CLIENT_IDS = [
    client_id
    for client_id in (env("APPLE_BUNDLE_ID", ""), env("APPLE_SERVICES_ID", ""))
    if client_id
]
SIGN_IN_NONCE_TTL_SECONDS = int(env("SIGN_IN_NONCE_TTL_SECONDS", "600"))
# core.email.send_email. Local, test and the image build default to the console. Every
# deployed environment must name a real backend, normally Resend
# (core.resend.ResendEmailBackend, which needs RESEND_API_KEY): a console, file or
# memory backend would leave sign-in emails, with their address and a live code, in the
# logs or on disk, so those refuse to start.
_LOCAL_EMAIL_BACKENDS = {
    f"django.core.mail.backends.{name}.EmailBackend" for name in ("console", "filebased", "locmem")
}
if APP_ENV in ("local", "test", "build"):
    EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
else:
    EMAIL_BACKEND = env("EMAIL_BACKEND")
    if EMAIL_BACKEND in _LOCAL_EMAIL_BACKENDS:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            f"EMAIL_BACKEND {EMAIL_BACKEND} is only for local development and tests; "
            "set a real backend such as django.core.mail.backends.smtp.EmailBackend."
        )
RESEND_EMAIL_BACKEND = "core.resend.ResendEmailBackend"
RESEND_API_KEY = (
    env("RESEND_API_KEY")
    if EMAIL_BACKEND == RESEND_EMAIL_BACKEND and APP_ENV not in ("local", "test", "build")
    else env("RESEND_API_KEY", "")
)
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "TaskBeavo <no-reply@taskbeavo.hr>")
# TaskBeavo's identity as a trader (the imprint), shown where the law requires it, e.g. the
# contract confirmation email. PLACEHOLDERS locally and in tests until the company details
# exist (BEAV-177); required when deployed, so no confirmation goes out with a placeholder.
_IMPRINT_DEFAULTS = {
    "TASKBEAVO_LEGAL_NAME": "PLACEHOLDER TaskBeavo company name",
    "TASKBEAVO_OIB": "PLACEHOLDER-OIB",
    "TASKBEAVO_ADDRESS": "PLACEHOLDER registered address, Dubrovnik, HR",
    "TASKBEAVO_CONTACT_EMAIL": "placeholder-support@taskbeavo.example",
}
_imprint = {
    name: env(name, default) if APP_ENV in ("local", "test", "build") else env(name)
    for name, default in _IMPRINT_DEFAULTS.items()
}
TASKBEAVO_LEGAL_NAME = _imprint["TASKBEAVO_LEGAL_NAME"]
TASKBEAVO_OIB = _imprint["TASKBEAVO_OIB"]
TASKBEAVO_ADDRESS = _imprint["TASKBEAVO_ADDRESS"]
TASKBEAVO_CONTACT_EMAIL = _imprint["TASKBEAVO_CONTACT_EMAIL"]
# Shared with the web app's /api/graphql route handler, which forwards each visitor's
# address in X-TaskBeavo-Client-IP (config/graphql_limits.py). Required when deployed:
# without it every web visitor shares one mutation rate-limit bucket. Unset locally,
# where the header is ignored.
WEB_PROXY_SECRET = (
    env("WEB_PROXY_SECRET", "")
    if APP_ENV in ("local", "test", "build")
    else env("WEB_PROXY_SECRET")
)
# Encrypts personal data that must be read back (phone numbers now, OIB and IBAN later;
# core/crypto.py): comma-separated Fernet keys, the first encrypts, all decrypt. Generate
# one with `Fernet.generate_key()` (docs/deploy-render.md has the command).
# Required when deployed; local, test and the image build derive one from SECRET_KEY.
FIELD_ENCRYPTION_KEY = (
    env(
        "FIELD_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(
            hashlib.sha256(f"field-encryption:{SECRET_KEY}".encode()).digest()
        ).decode(),
    )
    if APP_ENV in ("local", "test", "build")
    else env("FIELD_ENCRYPTION_KEY")
)


def _check_field_encryption_key(keys: str) -> None:
    from cryptography.fernet import Fernet
    from django.core.exceptions import ImproperlyConfigured

    try:
        parsed = [k.strip() for k in keys.split(",") if k.strip()]
        if not parsed:
            raise ValueError("no key")
        for key in parsed:
            Fernet(key)
    except ValueError:
        # The message names the variable only, never its value.
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY must be comma-separated Fernet keys (32 url-safe "
            "base64-encoded bytes each)."
        ) from None


_check_field_encryption_key(FIELD_ENCRYPTION_KEY)

# Keys `core.crypto.pii_digest`, the digests behind lookups and unique constraints on
# encrypted values (one verified phone number per account). Separate from SECRET_KEY so
# that one can be rotated after a leak. Never rotate this one without running
# `manage.py recompute_pii_digests` at once. Required when deployed; local, test and the
# image build derive one from SECRET_KEY.
PII_DIGEST_KEY = (
    env("PII_DIGEST_KEY", f"pii-digest:{SECRET_KEY}")
    if APP_ENV in ("local", "test", "build")
    else env("PII_DIGEST_KEY")
)


def _env_mapping(name: str, default: str = "") -> dict[str, str]:
    """ "HR=200,DE=100" → {"HR": "200", "DE": "100"}; keys upper-cased."""
    pairs = (item.split("=", 1) for item in env_list(name, default) if "=" in item)
    return {key.strip().upper(): value.strip() for key, value in pairs}


# --- Phone verification by SMS (accounts/phone.py, BEAV-72) ---
# core.sms. Local defaults to the console, tests to memory. Deployed environments must
# name a real provider, normally Infobip (core.infobip.InfobipSmsBackend, which needs
# INFOBIP_BASE_URL and INFOBIP_API_KEY); the console and memory backends
# would leave live codes in the logs, so they refuse to start there.
_LOCAL_SMS_BACKENDS = {"core.sms.ConsoleSmsBackend", "core.sms.LocmemSmsBackend"}
if APP_ENV in ("local", "test", "build"):
    SMS_BACKEND = env("SMS_BACKEND", "core.sms.ConsoleSmsBackend")
else:
    SMS_BACKEND = env("SMS_BACKEND")
    if SMS_BACKEND in _LOCAL_SMS_BACKENDS:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            f"SMS_BACKEND {SMS_BACKEND} is only for local development and tests; "
            "set core.infobip.InfobipSmsBackend."
        )
INFOBIP_BACKEND = "core.infobip.InfobipSmsBackend"
_needs_infobip = SMS_BACKEND == INFOBIP_BACKEND and APP_ENV not in ("local", "test", "build")
INFOBIP_BASE_URL = (
    env("INFOBIP_BASE_URL") if _needs_infobip else env("INFOBIP_BASE_URL", "")
).rstrip("/")
INFOBIP_API_KEY = env("INFOBIP_API_KEY") if _needs_infobip else env("INFOBIP_API_KEY", "")
# The registered alphanumeric sender ID messages come from, and senders per country
# ("US=+12025550143,CA=+12025550143"). North American carriers drop alphanumeric senders,
# so +1 numbers get no SMS (PHONE_COUNTRY_NOT_SUPPORTED) until a numeric sender is set
# for their country here (core.sms.sender_for).
INFOBIP_SENDER = env("INFOBIP_SENDER", "TaskBeavo")
INFOBIP_SENDER_BY_REGION = _env_mapping("INFOBIP_SENDER_BY_REGION")
SMS_TIMEOUT_SECONDS = int(env("SMS_TIMEOUT_SECONDS", "10"))
PHONE_CODE_TTL_SECONDS = int(env("PHONE_CODE_TTL_SECONDS", "600"))
PHONE_CODE_MAX_ATTEMPTS = int(env("PHONE_CODE_MAX_ATTEMPTS", "5"))
# Seconds before the same account can ask for another code (the "Resend in 0:59" timer).
PHONE_CODE_RESEND_SECONDS = int(env("PHONE_CODE_RESEND_SECONDS", "60"))
# Code SMS per hour, counted per account, per phone number and per client address. SMS
# costs money and is the classic SMS-pumping target, so these stay tight; the address
# limit is looser because Croatian mobile networks put many people behind one address.
PHONE_CODE_REQUESTS_PER_HOUR_PER_USER = int(env("PHONE_CODE_REQUESTS_PER_HOUR_PER_USER", "5"))
PHONE_CODE_REQUESTS_PER_HOUR_PER_PHONE = int(env("PHONE_CODE_REQUESTS_PER_HOUR_PER_PHONE", "5"))
PHONE_CODE_REQUESTS_PER_HOUR_PER_IP = int(env("PHONE_CODE_REQUESTS_PER_HOUR_PER_IP", "10"))
# Code checks per hour per account, across codes; each code also burns after
# PHONE_CODE_MAX_ATTEMPTS wrong guesses.
PHONE_CODE_VERIFY_ATTEMPTS_PER_HOUR = int(env("PHONE_CODE_VERIFY_ATTEMPTS_PER_HOUR", "10"))
# Posters can live anywhere (hard rule j), so every country's mobile numbers get SMS,
# under caps against SMS pumping: per country per hour (PHONE_CODE_COUNTRY_HOURLY_CAPS
# overrides the default for busy countries) and per day in total, with an error to Sentry
# at 80% of the daily cap. PHONE_DENIED_REGIONS (ISO 3166-1 alpha-2, empty by default)
# shuts a country off in an emergency.
PHONE_DENIED_REGIONS = frozenset(code.upper() for code in env_list("PHONE_DENIED_REGIONS", ""))
PHONE_CODE_REQUESTS_PER_HOUR_PER_COUNTRY = int(
    env("PHONE_CODE_REQUESTS_PER_HOUR_PER_COUNTRY", "20")
)
PHONE_CODE_COUNTRY_HOURLY_CAPS = {
    region: int(cap)
    for region, cap in _env_mapping(
        "PHONE_CODE_COUNTRY_HOURLY_CAPS", "HR=200,DE=100,AT=60,CH=60"
    ).items()
}
PHONE_CODE_SMS_PER_DAY = int(env("PHONE_CODE_SMS_PER_DAY", "500"))
CELERY_BEAT_SCHEDULE["accounts-purge-expired"] = {
    "task": "accounts.purge_expired",
    "schedule": 24 * 60 * 60.0,
}
# Ended sessions (and their coarse location and device hash) are deleted 6 months after
# they end: decision 2026-11-04, after CNIL Délibération 2021-122 (accounts/tasks.py).
SESSION_RETENTION_DAYS = int(env("SESSION_RETENTION_DAYS", "183"))
# A device token stops making its device known 6 months after the last sign-in that used
# it: decision 2026-11-04, strictly necessary security cookie (accounts/sessions.py).
DEVICE_TOKEN_MAX_AGE_DAYS = int(env("DEVICE_TOKEN_MAX_AGE_DAYS", "183"))
CELERY_BEAT_SCHEDULE["accounts-purge-ended-sessions"] = {
    "task": "accounts.purge_ended_sessions",
    "schedule": 24 * 60 * 60.0,
}
# --- end accounts --------------------------------------------------------------------

# --- Notifications: Expo push and email (notifications/, BEAV-60) ---------------------
# The web app's origin, for the links in notification emails ("https://taskbeavo.com").
# Required when deployed: a wrong default would send people to the wrong site.
WEB_APP_URL = (
    env("WEB_APP_URL", "http://localhost:3000")
    if APP_ENV in ("local", "test", "build")
    else env("WEB_APP_URL")
).rstrip("/")
# Expo's "enhanced security for push" access token; optional, but once it is turned on
# in the Expo project every send needs it.
EXPO_ACCESS_TOKEN = env("EXPO_ACCESS_TOKEN", "")
EXPO_TIMEOUT_SECONDS = int(env("EXPO_TIMEOUT_SECONDS", "10"))
# Retries of one push or email, with the outbox backoff (core.outbox.backoff).
NOTIFICATION_MAX_ATTEMPTS = int(env("NOTIFICATION_MAX_ATTEMPTS", "8"))
NOTIFICATION_SEND_SOFT_TIME_LIMIT_SECONDS = int(
    env("NOTIFICATION_SEND_SOFT_TIME_LIMIT_SECONDS", "60")
)
# Expo recommends checking receipts about 15 minutes after sending; it keeps them a day.
PUSH_RECEIPT_DELAY_MINUTES = int(env("PUSH_RECEIPT_DELAY_MINUTES", "15"))
PUSH_RECEIPT_MAX_AGE_HOURS = int(env("PUSH_RECEIPT_MAX_AGE_HOURS", "24"))
PUSH_RECEIPT_BATCH = int(env("PUSH_RECEIPT_BATCH", "5000"))
# Days a finished delivery (who got which notification) is kept.
NOTIFICATION_DELIVERY_RETENTION_DAYS = int(env("NOTIFICATION_DELIVERY_RETENTION_DAYS", "90"))
CELERY_BEAT_SCHEDULE["notifications-purge"] = {
    "task": "notifications.purge_finished",
    "schedule": 24 * 60 * 60.0,
}
CELERY_BEAT_SCHEDULE["notifications-sweep"] = {"task": "notifications.sweep", "schedule": 60.0}
CELERY_BEAT_SCHEDULE["notifications-push-receipts"] = {
    "task": "notifications.check_push_receipts",
    "schedule": 15 * 60.0,
}

# --- end notifications ---------------------------------------------------------------

# --- Stripe Express onboarding (payments/connect.py, BEAV-95) ------------------------
# Where Stripe's hosted onboarding sends the helper back: when they leave the flow
# (return) and when the single-use link expired or was used (refresh, which asks for a
# new link). The app opens these paths as universal links.
STRIPE_CONNECT_RETURN_URL = f"{WEB_APP_URL}/helper/payouts/return"
STRIPE_CONNECT_REFRESH_URL = f"{WEB_APP_URL}/helper/payouts/refresh"
# "Stripe needs more information" reminders after the first notice: at most this many,
# this many days apart.
PAYOUT_INFO_REMINDER_INTERVAL_DAYS = int(env("PAYOUT_INFO_REMINDER_INTERVAL_DAYS", "3"))
PAYOUT_INFO_MAX_REMINDERS = int(env("PAYOUT_INFO_MAX_REMINDERS", "3"))
CELERY_BEAT_SCHEDULE["payouts-info-reminders"] = {
    "task": "payments.connect.send_info_reminders",
    "schedule": 24 * 60 * 60.0,
}
# Reads back accounts in a status Stripe may still change, in case an event was lost.
CELERY_BEAT_SCHEDULE["payouts-sweep-unsettled-accounts"] = {
    "task": "payments.connect.sweep_unsettled_accounts",
    "schedule": 24 * 60 * 60.0,
}


# --- Photo uploads: presigned Cloudflare R2 URLs (uploads/, BEAV-63) -------------------
# Clients PUT photos straight into the private bucket; the API stores keys only. Required
# when deployed; local development and tests run without R2 (tests use a fake client).
def _r2(name: str) -> str:
    return env(name, "") if APP_ENV in ("local", "test", "build") else env(name)


R2_ACCOUNT_ID = _r2("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = _r2("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = _r2("R2_SECRET_ACCESS_KEY")
# Never public: upload originals, evidence photos, fiscal receipts.
R2_PRIVATE_BUCKET = _r2("R2_PRIVATE_BUCKET")
# Public, served at R2_PUBLIC_BASE_URL: only task photos re-encoded without metadata.
R2_PUBLIC_BUCKET = _r2("R2_PUBLIC_BUCKET")
R2_PUBLIC_BASE_URL = _r2("R2_PUBLIC_BASE_URL").rstrip("/")
# EU-jurisdiction buckets by default (GDPR); override for another jurisdiction.
R2_ENDPOINT_URL = env(
    "R2_ENDPOINT_URL",
    f"https://{R2_ACCOUNT_ID}.eu.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else "",
)
UPLOAD_MAX_BYTES = int(env("UPLOAD_MAX_BYTES", str(10 * 1024 * 1024)))
# Refused before decoding, against decompression bombs.
UPLOAD_MAX_PIXELS = int(env("UPLOAD_MAX_PIXELS", "50000000"))
# The clean copy's longest side.
UPLOAD_MAX_DIMENSION_PX = int(env("UPLOAD_MAX_DIMENSION_PX", "2560"))
UPLOAD_URL_TTL_SECONDS = int(env("UPLOAD_URL_TTL_SECONDS", "600"))
UPLOAD_READ_URL_TTL_SECONDS = int(env("UPLOAD_READ_URL_TTL_SECONDS", "300"))
UPLOAD_MAX_PENDING_PER_USER = int(env("UPLOAD_MAX_PENDING_PER_USER", "20"))
# How long one processing run owns an upload; longer than the task's 150 s hard limit.
UPLOAD_PROCESSING_LEASE_SECONDS = int(env("UPLOAD_PROCESSING_LEASE_SECONDS", "180"))
# Retention (uploads/models.py). Uploads no task or booking claimed are deleted, with
# their objects, after this.
UPLOAD_UNATTACHED_RETENTION_HOURS = int(env("UPLOAD_UNATTACHED_RETENTION_HOURS", "48"))
# A public task photo: after its task closes or expires.
UPLOAD_TASK_PHOTO_RETENTION_DAYS = int(env("UPLOAD_TASK_PHOTO_RETENTION_DAYS", "30"))
# A draft's photos are deleted this long after its last save unless it is published.
TASK_DRAFT_PHOTO_RETENTION_DAYS = int(env("TASK_DRAFT_PHOTO_RETENTION_DAYS", "30"))
# An evidence photo, original and position: after its booking closes.
UPLOAD_EVIDENCE_RETENTION_DAYS = int(env("UPLOAD_EVIDENCE_RETENTION_DAYS", "180"))
# ... or after payment, when the booking ended in a no-access call-out, a partial release
# or a refund.
UPLOAD_EVIDENCE_PAYMENT_ISSUE_RETENTION_MONTHS = int(
    env("UPLOAD_EVIDENCE_PAYMENT_ISSUE_RETENTION_MONTHS", "18")
)
# After the dispute, chargeback or claim that held an upload is resolved.
UPLOAD_LEGAL_HOLD_RETENTION_YEARS = int(env("UPLOAD_LEGAL_HOLD_RETENTION_YEARS", "5"))
CELERY_BEAT_SCHEDULE["uploads-sweep"] = {"task": "uploads.sweep", "schedule": 15 * 60.0}
# Staff notes follow the evidence periods above (staff/retention.py).
CELERY_BEAT_SCHEDULE["staff-sweep-notes"] = {"task": "staff.sweep_notes", "schedule": 60 * 60.0}
# --- end uploads ---------------------------------------------------------------------

# --- areas (BEAV-84) -----------------------------------------------------------------
# Days a waitlist entry is kept when its city never launches: 730, so never more than
# 24 months. Entries whose launch email was sent go at the next run.
WAITLIST_RETENTION_DAYS = int(env("WAITLIST_RETENTION_DAYS", "730"))
CELERY_BEAT_SCHEDULE["areas-purge-waitlist"] = {
    "task": "areas.purge_waitlist",
    "schedule": 24 * 60 * 60.0,
}
# --- end areas -----------------------------------------------------------------------

# --- offers (BEAV-100) ----------------------------------------------------------------
# Offers expire 7 days after sending (offers/services.py); the poster's reminder goes out
# within a day of the expiry. Both run often so neither is late by much.
CELERY_BEAT_SCHEDULE["offers-expire"] = {"task": "offers.expire", "schedule": 15 * 60.0}
CELERY_BEAT_SCHEDULE["offers-expiry-reminders"] = {
    "task": "offers.queue_expiry_reminders",
    "schedule": 15 * 60.0,
}
# --- end offers ----------------------------------------------------------------------

# --- Legal documents (legal/, BEAV-79) ------------------------------------------------
# Draft legal text (placeholders no lawyer approved) may be published and shown. Off in
# production, where it can't be turned on: `check_legal_documents` fails the pre-deploy
# step while a draft is current or scheduled, and drafts can't be published there.
LEGAL_DRAFTS_ALLOWED = env_bool("LEGAL_DRAFTS_ALLOWED", default=APP_ENV != "production")
if LEGAL_DRAFTS_ALLOWED and APP_ENV == "production":
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("LEGAL_DRAFTS_ALLOWED can't be on in production.")
# --- end legal -----------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "hr"
LANGUAGES = [("hr", "Hrvatski"), ("en", "English"), ("de", "Deutsch"), ("fr", "Français")]
TIME_ZONE = "Europe/Zagreb"
USE_I18N = True
# gettext catalogs for emails, push, SMS and PDFs (core/i18n.py). The .mo files are
# committed: the image has no gettext. Rebuild them with `manage.py compilemessages`.
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_TZ = True

# Service fee price list (bookings/money.py, BEAV-191): the day the first price list takes
# effect, which is the launch date /fees shows (ZZP čl. 7(9)). Required in every deployed
# environment, as YYYY-MM-DD; local, test and the build step use a fixed date. The first
# list also covers any time before this date, so nothing is ever left without a fee.
FEE_LAUNCH_DATE_DEFAULT = "2026-11-01"
_fee_launch = (
    env("FEE_LAUNCH_DATE", FEE_LAUNCH_DATE_DEFAULT)
    if APP_ENV in ("local", "test", "build")
    else env("FEE_LAUNCH_DATE")
)
try:
    FEE_LAUNCH_DATE = datetime.date.fromisoformat(_fee_launch)
except ValueError:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("FEE_LAUNCH_DATE must be a date as YYYY-MM-DD.") from None

# Whether the first price list's fee includes VAT (bookings/money.py `FeeVat`): INCLUDED or
# NOT_CHARGED, as the accountant decides. Deployed environments refuse to start without
# one (user decision 2026-11-07: no launch before the decision); local, test and the build
# step may run UNDECIDED, which /fees shows as an open question.
_FEE_VATS = ("INCLUDED", "NOT_CHARGED")
FEE_VAT = env("FEE_VAT", "UNDECIDED") if APP_ENV in ("local", "test", "build") else env("FEE_VAT")
if FEE_VAT not in (*_FEE_VATS, "UNDECIDED") or (
    FEE_VAT == "UNDECIDED" and APP_ENV not in ("local", "test", "build")
):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "FEE_VAT must be INCLUDED or NOT_CHARGED (the accountant's decision)."
    )

# Who gets the VAT threshold alerts (vat/monitor.py, BEAV-194): support and the founder,
# comma-separated. Unset means the support address (TASKBEAVO_CONTACT_EMAIL).
VAT_ALERT_EMAILS = [
    address.strip() for address in env("VAT_ALERT_EMAILS", "").split(",") if address.strip()
] or [TASKBEAVO_CONTACT_EMAIL]
# Daily at 06:00 Europe/Zagreb (CELERY_TIMEZONE): totals this year's service fees against
# the VAT thresholds (docs/vat-position.md). A wall-clock crontab, because Beat's schedule
# file lives in /tmp and each deploy would restart a 24-hour interval from zero.
CELERY_BEAT_SCHEDULE["vat-check-thresholds"] = {
    "task": "vat.check_thresholds",
    "schedule": crontab(hour=6, minute=0),
}

# Cancellation policy (hard rule g, bookings/cancellation.py): under this many hours before
# the start a poster's cancel is late. A policy value, deliberately not read from the env.
LATE_CANCEL_WINDOW_HOURS = 24

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# GraphQL limits (hard rule d). The schema adds the depth, token, alias and complexity
# limits and the mutation rate limit (config/graphql_limits.py); introspection is
# disabled when APP_ENV is "production".
GRAPHQL_MAX_DEPTH = int(env("GRAPHQL_MAX_DEPTH", "10"))
GRAPHQL_MAX_TOKENS = int(env("GRAPHQL_MAX_TOKENS", "2000"))
GRAPHQL_MAX_ALIASES = int(env("GRAPHQL_MAX_ALIASES", "15"))
GRAPHQL_MAX_COMPLEXITY = int(env("GRAPHQL_MAX_COMPLEXITY", "1000"))
# Estimated page size: what a list field selects counts this many times.
GRAPHQL_COMPLEXITY_LIST_MULTIPLIER = int(env("GRAPHQL_COMPLEXITY_LIST_MULTIPLIER", "10"))
# Calls of each mutation per client per window; tighter per-mutation limits go in
# the overrides, e.g. {"requestSignInCode": 5}.
GRAPHQL_MUTATION_RATE_LIMIT = int(env("GRAPHQL_MUTATION_RATE_LIMIT", "30"))
GRAPHQL_MUTATION_RATE_WINDOW_SECONDS = int(env("GRAPHQL_MUTATION_RATE_WINDOW_SECONDS", "60"))
GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES: dict[str, int] = {
    # Each call presigns a PUT; a task needs a handful of photos.
    "createUploadUrl": int(env("UPLOAD_URL_RATE_LIMIT", "20")),
    # Each call can send a paid SMS; accounts/phone.py adds hourly limits on top.
    "requestPhoneCode": 3,
    # A poster sets their business once or twice; a tight limit keeps the OIB check from
    # being used to probe OIBs (accounts/business.py, BEAV-195).
    "setPosterBusiness": 3,
    # Each call that reaches Stripe writes a booking, legal records kept 11 years and a
    # PaymentIntent.
    "acceptOffer": 5,
    # Each moves money back through Stripe (bookings/cancel.py); one is enough per booking.
    "cancelBooking": 5,
    "withdrawFromContract": 5,
    "verifyPhoneCode": 10,
    # Google and Apple sign-in: each nonce is a database row.
    "requestSignInNonce": 10,
    "signInWithProvider": 10,
    # Each call sends an email; accounts/signin.py adds hourly limits on top.
    "requestEmailCode": 5,
    # Each wrong code also counts against the code and the address (EMAIL_CODE_*).
    "verifyEmailCode": 10,
    "linkProviderWithEmailCode": 10,
    # Public: each call tries a "This wasn't me" link token.
    "signOutEverywhereFromEmail": 10,
    # Onboarding takes a few saves; a low limit also slows probing OIBs with OIB_IN_USE.
    "updateHelperProfile": 10,
    # Each call makes a Stripe request for a new onboarding link.
    "startPayoutOnboarding": 5,
    # Public and unauthenticated: each call stores a CONSENT row (the web cookie banner).
    "setConsent": 10,
    # Public and unauthenticated: each call stores an email address.
    "joinWaitlist": 5,
    # Public and unauthenticated: each call tries a withdrawal token.
    "leaveWaitlist": 10,
    # Each published task notifies helpers (later tickets); drafts save as the poster types.
    "postTask": 10,
    "publishTaskDraft": 10,
}
# Queries that cost like a mutation, counted the same way, per account (BEAV-111 R1-3), and
# allowed once per operation so aliases can't multiply them. {"fieldName": calls per window}
GRAPHQL_QUERY_RATE_LIMITS: dict[str, int] = {
    # Each call reads the PaymentIntent from Stripe, whose rate limit the whole platform shares.
    "checkoutSession": int(env("CHECKOUT_SESSION_RATE_LIMIT", "10")),
}
# Proxies in front of Django that append to X-Forwarded-For. 0 trusts only REMOTE_ADDR;
# the Render settings (production.py) default to 1.
GRAPHQL_TRUSTED_PROXY_COUNT = int(env("GRAPHQL_TRUSTED_PROXY_COUNT", "0"))

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    # Shared by every web worker, so the mutation rate limit holds across processes.
    "graphql_rate_limit": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "taskbeavo",
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

# Observability (config/telemetry.py). Sentry starts only when SENTRY_DSN is set, and
# OpenTelemetry only when OTEL_EXPORTER_OTLP_HEADERS (the Honeycomb key) is set, so
# local development and CI send nothing. The OTEL_* variables are read by the SDK.
SENTRY_DSN = env("SENTRY_DSN", "")
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", APP_ENV)
# Render sets RENDER_GIT_COMMIT on every deploy.
SENTRY_RELEASE = env("RENDER_GIT_COMMIT", "")

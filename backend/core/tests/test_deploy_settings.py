"""Production settings and the Render pre-deploy script, run as Render runs them: in a
fresh process with only the environment the env group provides."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]

RENDER_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.production",
    "APP_ENV": "staging",
    "SECRET_KEY": "test-0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJ-not-secret",
    "JWT_SIGNING_KEY": "jwt-0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJ-not-secret",
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "WEB_PROXY_SECRET": "proxy-0123456789-not-secret",
    "WEB_APP_URL": "https://staging.taskbeavo.com",
    "FEE_LAUNCH_DATE": "2026-12-01",
    "FEE_VAT": "NOT_CHARGED",
    "TASKBEAVO_LEGAL_NAME": "TaskBeavo test d.o.o.",
    "TASKBEAVO_OIB": "00000000124",
    "TASKBEAVO_ADDRESS": "Test ulica 1, 20000 Dubrovnik, HR",
    "TASKBEAVO_CONTACT_EMAIL": "support@example.com",
    "FIELD_ENCRYPTION_KEY": "c3RhZ2luZy1vbmx5LWZpZWxkLWtleS0wMTIzNDU2Nzg=",
    "PII_DIGEST_KEY": "pii-digest-0123456789-not-secret",
    "SMS_BACKEND": "core.infobip.InfobipSmsBackend",
    "INFOBIP_BASE_URL": "https://example.api.infobip.com",
    "INFOBIP_API_KEY": "infobip-key-not-secret",
    "INFOBIP_SENDER": "TaskBeavo",
    "R2_ACCOUNT_ID": "0123456789abcdef",
    "R2_ACCESS_KEY_ID": "r2-key-id",
    "R2_SECRET_ACCESS_KEY": "r2-secret-not-secret",
    "R2_PRIVATE_BUCKET": "taskbeavo-private-staging",
    "R2_PUBLIC_BUCKET": "taskbeavo-public-staging",
    "R2_PUBLIC_BASE_URL": "https://photos-staging.taskbeavo.com",
    "ALLOWED_HOSTS": "api-staging.taskbeavo.com",
    "DATABASE_URL": "postgresql://app:pooled-s3cret@ep-x-pooler.eu-central-1.aws.neon.tech/db",
    "REDIS_URL": "redis://red-x:6379",
    "RENDER_EXTERNAL_HOSTNAME": "taskbeavo-api-staging.onrender.com",
    "STRIPE_SECRET_KEY": "sk_test_staging_not_a_real_key",
    "STRIPE_WEBHOOK_SECRET": "whsec_platform_not_secret,whsec_connect_not_secret",
}

PRINT_SETTINGS = """
import json, django
from django.conf import settings
django.setup()
db = settings.DATABASES["default"]
print(json.dumps({
    "DEBUG": settings.DEBUG,
    "APP_ENV": settings.APP_ENV,
    "ALLOWED_HOSTS": settings.ALLOWED_HOSTS,
    "ENGINE": db["ENGINE"],
    "SSLMODE": db["OPTIONS"].get("sslmode"),
    "DISABLE_SERVER_SIDE_CURSORS": db.get("DISABLE_SERVER_SIDE_CURSORS"),
    "FIRST_MIDDLEWARE": settings.MIDDLEWARE[0],
}))
"""


def run_python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}  # noqa: S108
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=base | env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_production_settings_load_from_the_render_environment() -> None:
    result = run_python(PRINT_SETTINGS, RENDER_ENV)

    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert loaded["DEBUG"] is False
    assert loaded["APP_ENV"] == "staging"
    assert loaded["ENGINE"] == "django.contrib.gis.db.backends.postgis"
    assert loaded["SSLMODE"] == "require"
    # Neon's pooled connection runs PgBouncer in transaction mode.
    assert loaded["DISABLE_SERVER_SIDE_CURSORS"] is True


def test_render_hostname_is_allowed_so_the_health_check_passes() -> None:
    result = run_python(PRINT_SETTINGS, RENDER_ENV)

    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert loaded["ALLOWED_HOSTS"] == [
        "api-staging.taskbeavo.com",
        "taskbeavo-api-staging.onrender.com",
    ]
    # The probe itself may use an internal Host header; this answers it first.
    assert loaded["FIRST_MIDDLEWARE"] == "core.middleware.HealthCheckMiddleware"


def test_production_settings_refuse_to_start_without_a_secret_and_do_not_log_values() -> None:
    env = {key: value for key, value in RENDER_ENV.items() if key != "SECRET_KEY"}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert "Set the SECRET_KEY environment variable." in result.stderr
    assert "pooled-s3cret" not in result.stderr + result.stdout


def test_production_settings_refuse_to_start_without_an_email_backend() -> None:
    # Without one, sign-in emails (address and live code) would go to the logs.
    env = {key: value for key, value in RENDER_ENV.items() if key != "EMAIL_BACKEND"}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert "Set the EMAIL_BACKEND environment variable." in result.stderr


def test_production_settings_refuse_to_start_without_the_web_proxy_secret() -> None:
    # Without it every web visitor shares one mutation rate-limit bucket.
    env = {key: value for key, value in RENDER_ENV.items() if key != "WEB_PROXY_SECRET"}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert "Set the WEB_PROXY_SECRET environment variable." in result.stderr


@pytest.mark.parametrize(
    "name",
    ["TASKBEAVO_LEGAL_NAME", "TASKBEAVO_OIB", "TASKBEAVO_ADDRESS", "TASKBEAVO_CONTACT_EMAIL"],
)
def test_production_settings_refuse_to_start_without_taskbeavos_trader_identity(
    name: str,
) -> None:
    # The contract confirmation names TaskBeavo as the trader for the fee: no placeholder.
    env = {key: value for key, value in RENDER_ENV.items() if key != name}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert f"Set the {name} environment variable." in result.stderr


def test_production_settings_refuse_to_start_without_the_fee_launch_date() -> None:
    # The first price list's effective date, shown on /fees (BEAV-191).
    env = {key: value for key, value in RENDER_ENV.items() if key != "FEE_LAUNCH_DATE"}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert "Set the FEE_LAUNCH_DATE environment variable." in result.stderr


def test_production_settings_refuse_to_start_while_vat_is_undecided() -> None:
    # No launch before the accountant decides (user decision 2026-11-07).
    missing = {key: value for key, value in RENDER_ENV.items() if key != "FEE_VAT"}
    for env in (missing, RENDER_ENV | {"FEE_VAT": "UNDECIDED"}, RENDER_ENV | {"FEE_VAT": "yes"}):
        result = run_python("import django; django.setup()", env)

        assert result.returncode != 0
        assert "FEE_VAT" in result.stderr


def test_the_first_price_list_carries_the_accountants_vat_decision() -> None:
    code = (
        "import django; django.setup(); from bookings import money; "
        "print(money.FEE_SCHEDULES[0].vat)"
    )

    result = run_python(code, RENDER_ENV | {"FEE_VAT": "INCLUDED"})

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "INCLUDED"


def test_the_fee_launch_date_must_be_a_date() -> None:
    result = run_python("import django; django.setup()", RENDER_ENV | {"FEE_LAUNCH_DATE": "1.12."})

    assert result.returncode != 0
    assert "FEE_LAUNCH_DATE must be a date as YYYY-MM-DD." in result.stderr


def test_the_first_price_list_takes_effect_on_the_launch_date() -> None:
    code = (
        "import django; django.setup(); from bookings import money; "
        "print(money.FEE_SCHEDULES[0].effective_from)"
    )

    result = run_python(code, RENDER_ENV)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2026-12-01"


def test_production_settings_refuse_to_start_without_the_web_app_url() -> None:
    # Notification emails link to it.
    env = {key: value for key, value in RENDER_ENV.items() if key != "WEB_APP_URL"}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert "Set the WEB_APP_URL environment variable." in result.stderr


@pytest.mark.parametrize("name", ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"])
def test_production_settings_refuse_to_start_without_stripe(name: str) -> None:
    # Without the webhook secret every Stripe event would be rejected, and no booking
    # would ever reach FUNDS_HELD.
    env = {key: value for key, value in RENDER_ENV.items() if key != name}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert f"Set the {name} environment variable." in result.stderr


def test_stripe_webhook_secret_lists_every_endpoint_secret() -> None:
    code = (
        "import django; from django.conf import settings; django.setup(); "
        "print(settings.STRIPE_WEBHOOK_SECRETS)"
    )

    result = run_python(code, RENDER_ENV)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['whsec_platform_not_secret', 'whsec_connect_not_secret']"


@pytest.mark.parametrize("key", ["sk_live_0123456789", "rk_live_0123456789"])
def test_staging_refuses_a_live_stripe_key_without_logging_it(key: str) -> None:
    result = run_python("import django; django.setup()", RENDER_ENV | {"STRIPE_SECRET_KEY": key})

    assert result.returncode != 0
    assert "STRIPE_SECRET_KEY is a live key" in result.stderr
    assert key not in result.stderr + result.stdout


@pytest.mark.parametrize("key", ["sk_test_0123456789", "rk_test_0123456789"])
def test_production_refuses_a_test_stripe_key_without_logging_it(key: str) -> None:
    # A copied staging env group would otherwise take test payments as real ones.
    env = RENDER_ENV | {"APP_ENV": "production", "STRIPE_SECRET_KEY": key}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert "STRIPE_SECRET_KEY is a test key" in result.stderr
    assert key not in result.stderr + result.stdout


def test_production_takes_a_live_stripe_key() -> None:
    env = RENDER_ENV | {"APP_ENV": "production", "STRIPE_SECRET_KEY": "rk_live_0123456789"}

    result = run_python("import django; django.setup()", env)

    assert result.returncode == 0, result.stderr


def test_an_empty_app_env_falls_back_to_production_not_local() -> None:
    # BEAV-217: an empty APP_ENV (e.g. a blank env group value) must fail closed. It used to
    # fall through to APP_ENV "local", which relaxes the deploy-only requirements.
    live = RENDER_ENV | {"APP_ENV": "", "STRIPE_SECRET_KEY": "rk_live_0123456789"}
    result = run_python(PRINT_SETTINGS, live)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["APP_ENV"] == "production"

    # So production's rules apply too: the staging env group's test key is refused.
    result = run_python("import django; django.setup()", RENDER_ENV | {"APP_ENV": ""})
    assert result.returncode != 0
    assert "STRIPE_SECRET_KEY is a test key" in result.stderr


def test_production_refuses_demo_payments() -> None:
    env = RENDER_ENV | {
        "APP_ENV": "production",
        "STRIPE_SECRET_KEY": "rk_live_0123456789",
        "DEMO_PAYMENTS": "1",
    }

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert "DEMO_PAYMENTS can't be on in production or with a live key." in result.stderr


@pytest.mark.parametrize("key", ["sk_live_0123456789", "rk_live_0123456789"])
def test_demo_payments_never_run_next_to_a_live_key(key: str) -> None:
    env = RENDER_ENV | {"STRIPE_SECRET_KEY": key, "DEMO_PAYMENTS": "1"}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert key not in result.stderr + result.stdout


def test_staging_may_run_demo_payments_with_a_test_key() -> None:
    env = RENDER_ENV | {"DEMO_PAYMENTS": "1"}

    result = run_python(
        "import django; from django.conf import settings; django.setup(); "
        "print(settings.DEMO_PAYMENTS)",
        env,
    )

    assert result.stdout.strip() == "True", result.stderr


PRINT_LEGAL_DRAFTS = (
    "import django; from django.conf import settings; django.setup(); "
    "print(settings.LEGAL_DRAFTS_ALLOWED)"
)


def test_production_never_allows_legal_drafts() -> None:
    live = RENDER_ENV | {"APP_ENV": "production", "STRIPE_SECRET_KEY": "rk_live_0123456789"}

    default = run_python(PRINT_LEGAL_DRAFTS, live)
    turned_on = run_python(PRINT_LEGAL_DRAFTS, live | {"LEGAL_DRAFTS_ALLOWED": "true"})

    assert default.returncode == 0, default.stderr
    assert default.stdout.strip() == "False"
    assert turned_on.returncode != 0
    assert "LEGAL_DRAFTS_ALLOWED can't be on in production." in turned_on.stderr


def test_staging_allows_legal_drafts_unless_turned_off() -> None:
    default = run_python(PRINT_LEGAL_DRAFTS, RENDER_ENV)
    turned_off = run_python(PRINT_LEGAL_DRAFTS, RENDER_ENV | {"LEGAL_DRAFTS_ALLOWED": "false"})

    assert default.stdout.strip() == "True", default.stderr
    assert turned_off.stdout.strip() == "False", turned_off.stderr


@pytest.mark.parametrize(
    "name",
    [
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_PRIVATE_BUCKET",
        "R2_PUBLIC_BUCKET",
        "R2_PUBLIC_BASE_URL",
    ],
)
def test_production_settings_refuse_to_start_without_r2(name: str) -> None:
    # Photo uploads need both buckets; a missing one would only fail on first upload.
    env = {key: value for key, value in RENDER_ENV.items() if key != name}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert f"Set the {name} environment variable." in result.stderr


def test_the_resend_backend_needs_its_api_key() -> None:
    env = RENDER_ENV | {"EMAIL_BACKEND": "core.resend.ResendEmailBackend"}

    without = run_python("import django; django.setup()", env)
    with_key = run_python("import django; django.setup()", env | {"RESEND_API_KEY": "re_x"})

    assert without.returncode != 0
    assert "Set the RESEND_API_KEY environment variable." in without.stderr
    assert with_key.returncode == 0, with_key.stderr


@pytest.mark.parametrize(
    "backend",
    [
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.filebased.EmailBackend",
        "django.core.mail.backends.locmem.EmailBackend",
    ],
)
def test_production_settings_refuse_an_email_backend_that_keeps_codes_locally(
    backend: str,
) -> None:
    result = run_python("import django; django.setup()", RENDER_ENV | {"EMAIL_BACKEND": backend})

    assert result.returncode != 0
    assert f"EMAIL_BACKEND {backend} is only for local development and tests" in result.stderr


@pytest.mark.parametrize(
    "name",
    [
        "FIELD_ENCRYPTION_KEY",
        "PII_DIGEST_KEY",
        "SMS_BACKEND",
        "INFOBIP_BASE_URL",
        "INFOBIP_API_KEY",
    ],
)
def test_production_settings_refuse_to_start_without_encryption_or_sms(name: str) -> None:
    env = {key: value for key, value in RENDER_ENV.items() if key != name}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert f"Set the {name} environment variable." in result.stderr


@pytest.mark.parametrize("value", ["too-short-not-secret", ", ,"])
def test_production_settings_refuse_a_malformed_encryption_key(value: str) -> None:
    env = RENDER_ENV | {"FIELD_ENCRYPTION_KEY": value}

    result = run_python("import django; django.setup()", env)

    assert result.returncode != 0
    assert "FIELD_ENCRYPTION_KEY" in result.stderr
    assert "FIELD_ENCRYPTION_KEY must be" in result.stderr
    assert "too-short-not-secret" not in result.stderr


@pytest.mark.parametrize("backend", ["core.sms.ConsoleSmsBackend", "core.sms.LocmemSmsBackend"])
def test_production_settings_refuse_an_sms_backend_that_keeps_codes_locally(backend: str) -> None:
    result = run_python("import django; django.setup()", RENDER_ENV | {"SMS_BACKEND": backend})

    assert result.returncode != 0
    assert f"SMS_BACKEND {backend} is only for local development and tests" in result.stderr


def test_deploy_checks_pass() -> None:
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        cwd=BACKEND_DIR,
        env={"PATH": os.environ["PATH"]} | RENDER_ENV,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def fake_python(tmp_path: Path) -> Path:
    """A `python` that records its arguments and DATABASE_URL instead of migrating."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "python"
    script.write_text('#!/bin/sh\necho "args=$*"\necho "url=$DATABASE_URL"\n')
    script.chmod(0o755)
    return bin_dir


def run_predeploy(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    path = f"{fake_python(tmp_path)}:{os.environ['PATH']}"
    return subprocess.run(
        ["/bin/sh", "scripts/predeploy.sh"],
        cwd=BACKEND_DIR,
        env={"PATH": path} | env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_predeploy_migrates_over_the_direct_connection(tmp_path: Path) -> None:
    result = run_predeploy(
        tmp_path,
        {"DATABASE_URL": "postgresql://pooled", "DATABASE_URL_DIRECT": "postgresql://direct"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "args=manage.py migrate --noinput",
        "url=postgresql://direct",
        # Production refuses to go live with draft legal documents (legal/).
        "args=manage.py check_legal_documents",
        "url=postgresql://direct",
    ]


def test_predeploy_falls_back_to_database_url(tmp_path: Path) -> None:
    result = run_predeploy(tmp_path, {"DATABASE_URL": "postgresql://render-postgres"})

    assert result.returncode == 0, result.stderr
    assert "url=postgresql://render-postgres" in result.stdout.splitlines()


def test_predeploy_stops_when_a_step_fails(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "python"
    script.write_text('#!/bin/sh\necho "args=$*"\nexit 1\n')
    script.chmod(0o755)

    result = subprocess.run(
        ["/bin/sh", "scripts/predeploy.sh"],
        cwd=BACKEND_DIR,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "DATABASE_URL": "postgresql://x"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode != 0
    assert result.stdout.splitlines() == ["args=manage.py migrate --noinput"]

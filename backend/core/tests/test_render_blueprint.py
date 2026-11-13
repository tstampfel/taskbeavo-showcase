"""render.yaml (repo root): the Render services, their environments and how secrets enter."""

from pathlib import Path
from typing import Any

import pytest
import yaml

BACKEND_DIR = Path(__file__).resolve().parents[2]
BLUEPRINT = BACKEND_DIR.parent / "render.yaml"

ENVIRONMENTS = {
    "staging": {"suffix": "-staging", "branch": "develop", "app_env": "staging"},
    "production": {"suffix": "", "branch": "main", "app_env": "production"},
}
ROLES = {"api": "web", "worker": "worker", "beat": "worker"}

# Variables that may carry a literal value in the blueprint.
PUBLIC_VALUES = {
    "DJANGO_SETTINGS_MODULE",
    "APP_ENV",
    "SENTRY_ENVIRONMENT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SERVICE_NAME",
    "FINA_CERT_PATH",
    "CIS_URL",
    "FEE_LAUNCH_DATE",
    "FEE_VAT",
}
# What each env group declares in render.yaml: public values plus the generated secrets.
BLUEPRINT_GROUP_KEYS = (PUBLIC_VALUES - {"OTEL_SERVICE_NAME"}) | {"SECRET_KEY", "JWT_SIGNING_KEY"}
# Secrets and per-environment URLs. Render ignores `sync: false` inside env groups, so
# these are added to each env group by hand in the dashboard (docs/deploy-render.md)
# and must never appear in render.yaml.
DASHBOARD_VARIABLES = {
    "ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "DATABASE_URL",
    "DATABASE_URL_DIRECT",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "SENTRY_DSN",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_PRIVATE_BUCKET",
    "R2_PUBLIC_BUCKET",
    "R2_PUBLIC_BASE_URL",
    "FIELD_ENCRYPTION_KEY",
    "FINA_CERT_PASSWORD",
    "RESEND_API_KEY",
    "POSTHOG_KEY",
    "EXPO_ACCESS_TOKEN",
    "WEB_PROXY_SECRET",
    "EMAIL_BACKEND",
    "EMAIL_HOST",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "WEB_APP_URL",
    "VAT_ALERT_EMAILS",
    "SMS_BACKEND",
    "INFOBIP_BASE_URL",
    "INFOBIP_API_KEY",
    "INFOBIP_SENDER",
    "INFOBIP_SENDER_BY_REGION",
    "PII_DIGEST_KEY",
    "GEOIP_CITY_DATABASE",
    "MAXMIND_ACCOUNT_ID",
    "MAXMIND_LICENSE_KEY",
}
RUNBOOK = BACKEND_DIR.parent / "docs" / "deploy-render.md"


@pytest.fixture(scope="module")
def blueprint() -> dict[str, Any]:
    if not BLUEPRINT.exists():
        # The compose service mounts only backend/, so the repo root is not there.
        pytest.skip("render.yaml is outside the mounted backend/ directory")
    return yaml.safe_load(BLUEPRINT.read_text())


def service(blueprint: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [s for s in blueprint["services"] if s["name"] == name]
    assert len(matches) == 1, f"expected one service named {name}"
    return matches[0]


def group(blueprint: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    matches = [g for g in blueprint["envVarGroups"] if g["name"] == name]
    assert len(matches) == 1, f"expected one env group named {name}"
    return {var["key"]: var for var in matches[0]["envVars"]}


def all_env_vars(blueprint: dict[str, Any]) -> list[dict[str, Any]]:
    service_vars = [v for s in blueprint["services"] for v in s.get("envVars", [])]
    group_vars = [v for g in blueprint["envVarGroups"] for v in g["envVars"]]
    return [v for v in service_vars + group_vars if "key" in v]


def test_every_service_runs_in_frankfurt(blueprint: dict[str, Any]) -> None:
    assert {s["region"] for s in blueprint["services"]} == {"frankfurt"}


@pytest.mark.parametrize("env_name", ENVIRONMENTS)
def test_environment_has_api_worker_beat_and_key_value(
    blueprint: dict[str, Any], env_name: str
) -> None:
    env = ENVIRONMENTS[env_name]
    for role, service_type in ROLES.items():
        svc = service(blueprint, f"taskbeavo-{role}{env['suffix']}")
        assert svc["type"] == service_type
        assert svc["runtime"] == "docker"
        assert svc["branch"] == env["branch"]
        assert svc["rootDir"] == "backend"
        assert (BACKEND_DIR / svc["dockerfilePath"]).resolve() == BACKEND_DIR / "Dockerfile"
        assert svc["dockerContext"] == "."
    kv = service(blueprint, f"taskbeavo-kv{env['suffix']}")
    assert kv["type"] == "keyvalue"


@pytest.mark.parametrize("suffix", ["-staging", ""])
def test_api_checks_health_and_migrates_before_deploy(
    blueprint: dict[str, Any], suffix: str
) -> None:
    api = service(blueprint, f"taskbeavo-api{suffix}")
    assert api["healthCheckPath"] == "/healthz"
    assert api["preDeployCommand"] == "sh scripts/predeploy.sh"
    # The image CMD (gunicorn with uvicorn workers) serves the API.
    assert "dockerCommand" not in api


@pytest.mark.parametrize("suffix", ["-staging", ""])
def test_worker_and_beat_commands(blueprint: dict[str, Any], suffix: str) -> None:
    worker = service(blueprint, f"taskbeavo-worker{suffix}")
    beat = service(blueprint, f"taskbeavo-beat{suffix}")
    assert worker["dockerCommand"].startswith("celery -A config worker ")
    assert " -B" not in worker["dockerCommand"], "Beat must not also run inside the worker"
    assert beat["dockerCommand"].startswith("celery -A config beat ")
    # /app is read-only for the app user, so the schedule file must live elsewhere.
    assert "--schedule /tmp/" in beat["dockerCommand"]


@pytest.mark.parametrize("suffix", ["-staging", ""])
def test_beat_is_a_single_unscaled_instance(blueprint: dict[str, Any], suffix: str) -> None:
    beat = service(blueprint, f"taskbeavo-beat{suffix}")
    assert beat["numInstances"] == 1
    assert "scaling" not in beat


@pytest.mark.parametrize("suffix", ["-staging", ""])
def test_key_value_never_evicts_and_is_private(blueprint: dict[str, Any], suffix: str) -> None:
    kv = service(blueprint, f"taskbeavo-kv{suffix}")
    assert kv["maxmemoryPolicy"] == "noeviction"
    assert kv["ipAllowList"] == []


@pytest.mark.parametrize("env_name", ENVIRONMENTS)
def test_services_use_their_own_environment(blueprint: dict[str, Any], env_name: str) -> None:
    suffix = ENVIRONMENTS[env_name]["suffix"]
    for role in ROLES:
        svc = service(blueprint, f"taskbeavo-{role}{suffix}")
        env_vars = svc["envVars"]
        assert {"fromGroup": f"taskbeavo-backend-{env_name}"} in env_vars
        redis = next(v for v in env_vars if v.get("key") == "REDIS_URL")
        assert redis["fromService"] == {
            "type": "keyvalue",
            "name": f"taskbeavo-kv{suffix}",
            "property": "connectionString",
        }
        otel_name = next(v for v in env_vars if v.get("key") == "OTEL_SERVICE_NAME")
        assert otel_name["value"] == f"taskbeavo-{role}"


@pytest.mark.parametrize("env_name", ENVIRONMENTS)
def test_env_group_declares_the_non_secret_variables(
    blueprint: dict[str, Any], env_name: str
) -> None:
    variables = group(blueprint, f"taskbeavo-backend-{env_name}")
    assert set(variables) == BLUEPRINT_GROUP_KEYS
    assert variables["DJANGO_SETTINGS_MODULE"]["value"] == "config.settings.production"
    assert variables["APP_ENV"]["value"] == ENVIRONMENTS[env_name]["app_env"]
    assert variables["SENTRY_ENVIRONMENT"]["value"] == ENVIRONMENTS[env_name]["app_env"]
    assert variables["OTEL_EXPORTER_OTLP_ENDPOINT"]["value"] == "https://api.eu1.honeycomb.io"
    assert variables["FINA_CERT_PATH"]["value"] == "/etc/secrets/fina.p12"
    assert variables["SECRET_KEY"] == {"key": "SECRET_KEY", "generateValue": True}
    assert variables["JWT_SIGNING_KEY"] == {"key": "JWT_SIGNING_KEY", "generateValue": True}
    # User decision 2026-11-07: launch, and the first price list, on 1 January 2027.
    assert variables["FEE_LAUNCH_DATE"]["value"] == "2027-01-01"
    # BEAV-194, user decision 2026-11-07: launch outside the VAT system (ZPDV čl. 90 st. 1).
    assert variables["FEE_VAT"]["value"] == "NOT_CHARGED"


def test_staging_never_talks_to_production_cis(blueprint: dict[str, Any]) -> None:
    staging = group(blueprint, "taskbeavo-backend-staging")["CIS_URL"]["value"]
    production = group(blueprint, "taskbeavo-backend-production")["CIS_URL"]["value"]
    assert "cistest" in staging
    assert "cistest" not in production


def test_no_secret_has_a_value_in_the_blueprint(blueprint: dict[str, Any]) -> None:
    for var in all_env_vars(blueprint):
        if var["key"] in PUBLIC_VALUES:
            continue
        assert "value" not in var, f"{var['key']} must not have a value in render.yaml"
        assert var.get("generateValue") or var.get("fromService"), (
            f"{var['key']} must be generated or taken from a service"
        )


def test_dashboard_variables_stay_out_of_the_blueprint(blueprint: dict[str, Any]) -> None:
    keys = {var["key"] for var in all_env_vars(blueprint)}
    assert not keys & DASHBOARD_VARIABLES
    # Render ignores `sync: false` in env groups; nothing here may rely on it.
    assert all("sync" not in var for var in all_env_vars(blueprint))


def test_runbook_lists_every_dashboard_variable() -> None:
    if not RUNBOOK.exists():
        pytest.skip("docs/ is outside the mounted backend/ directory")
    runbook = RUNBOOK.read_text()
    missing = {key for key in DASHBOARD_VARIABLES if f"`{key}`" not in runbook}
    assert not missing, f"docs/deploy-render.md does not list {sorted(missing)}"


def test_env_example_lists_every_deploy_variable() -> None:
    lines = (BACKEND_DIR / ".env.example").read_text().splitlines()
    keys = {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}
    # Listed commented out: an empty value would hide the entry points' default (BEAV-217).
    assert "# DJANGO_SETTINGS_MODULE=config.settings.local" in lines
    keys.add("DJANGO_SETTINGS_MODULE")
    assert BLUEPRINT_GROUP_KEYS | DASHBOARD_VARIABLES | {"REDIS_URL", "OTEL_SERVICE_NAME"} <= keys


def test_env_example_has_no_secret_values() -> None:
    for line in (BACKEND_DIR / ".env.example").read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in PUBLIC_VALUES or key in {"DATABASE_URL"}:
            continue
        assert value == "", f"{key} must be empty in .env.example"


def test_certificates_never_enter_the_image() -> None:
    # Render copies secret files into the Docker build context; `COPY . .` must skip them.
    ignored = (BACKEND_DIR / ".dockerignore").read_text().splitlines()
    for pattern in ("*.p12", "*.pfx", "*.pem", "*.key", ".env", ".env.*"):
        assert pattern in ignored

---
name: backend-deploy
description: Build and deploy TaskBeavo's Django backend with Docker and Render, covering backend/Dockerfile (python:3.12-slim, uv sync --frozen, GDAL/GEOS/PROJ, non-root user, collectstatic), docker-compose.yml for local dev, Render services (web, Celery worker, Celery beat, Key Value) in Frankfurt, the pre-deploy migrate, environment variables and secret files (Stripe, Sentry, Honeycomb, R2, FINA certificate), health checks, and the AWS ECS Fargate migration path as reference. Use this whenever the Dockerfile, compose file, render.yaml, env vars, secrets, worker/beat processes or deployment of the backend change, or someone asks "how do we deploy", "add an env var", "the build fails", "set up Render".
---

# Backend deploy

The backend is a single Docker image that runs as three processes (web, worker, beat) on Render in Frankfurt, close to Vercel `fra1` and Neon `eu-central-1`. The same image runs locally in docker-compose. Keeping one image for all roles means what you test locally is what runs in production. Deploying and changing production environment variables are outward-facing, so **confirm with the user first**.

**Plan:** `docs/PLAN.md`, sections "Deployment: Docker on Render now, an easy path to AWS later"; "A Django monolith, Expo apps and Stripe fit a solo React developer". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

The FINA certificate's use (signing, expiry warning) is owned by `tax-jobs`; this skill only mounts it as a Render secret file.

## Files

### `backend/Dockerfile`

The file has three stages (see `backend/Dockerfile`):

- `base`: `python:3.12-slim`, GDAL/GEOS/PROJ, `uv`, the non-root user `app` (uid 10001). The venv lives at `/opt/venv` (`UV_PROJECT_ENVIRONMENT`), outside `/app`, so compose can bind-mount the source without hiding it.
- `dev`: `uv sync --frozen` with the dev group, runs `uvicorn --reload`. Only docker-compose builds it.
- `runtime` (last, so `docker build -t taskbeavo-backend backend/` builds it): `uv sync --frozen --no-dev`, the source, `collectstatic` with `config.settings.build`, `DJANGO_SETTINGS_MODULE=config.settings.production` as the default (fail closed), `USER app`, and the default command `gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker -b 0.0.0.0:8000 --workers 3 --timeout 60`.

`backend/.dockerignore` keeps `.env`, the host `.venv` and caches out of the build context.

- `uv sync --frozen` fails if `uv.lock` is out of date, so builds are reproducible. Update the lock with `cd backend && uv lock`.
- GDAL, GEOS and PROJ are needed by GeoDjango/PostGIS. If Django can't find them, set `GDAL_LIBRARY_PATH`/`GEOS_LIBRARY_PATH`.
- `config.settings.build` needs no secrets: it's only for `collectstatic`.
- The image runs as non-root user `app`.
- The uvicorn worker class comes from the `uvicorn-worker` package, which supports WebSockets for Channels subscriptions.

### `docker-compose.yml` (repo root)

Services:
- `postgis` (`postgis/postgis:16-3.4`, volume, healthcheck)
- `redis` (`redis:7`)
- `django` (builds the `dev` target, runs `migrate` then `uvicorn config.asgi:application --reload` so WebSockets work like production, mounts `./backend`, healthcheck on `/healthz`)
- `celery` (`celery -A config worker -B -l info --schedule /tmp/celerybeat-schedule` locally only; in production beat runs separately)
- `stripe-cli` (`stripe/stripe-cli`, `listen --forward-to django:8000/webhooks/stripe` via `docker/stripe-listen.sh`, test key `STRIPE_SECRET_KEY` from `backend/.env`; exits cleanly without a key and refuses anything but `sk_test_`/`rk_test_`)

Ports are published on `127.0.0.1` only: PostGIS on host port **55432** (5432 is often taken by another Postgres; `config.settings.local`/`test` and `.env.example` default to it), Redis on 6379, Django on 8000. Override a taken host port with `POSTGIS_HOST_PORT`, `REDIS_HOST_PORT` or `DJANGO_HOST_PORT`.

Local settings come from `backend/.env` (gitignored). Commit a `backend/.env.example` with every variable name and no values.

```bash
docker compose up -d postgis redis
docker compose up django celery stripe-cli
docker compose logs -f django
```

### `render.yaml` (Render Blueprint)

Staging deploys from `develop` (services suffixed `-staging`), production from `main`. All services use `rootDir: backend` and `autoDeployTrigger: checksPass`. Setup steps for the user: `docs/deploy-render.md`.

| Service | Type | Command | Notes |
|---|---|---|---|
| `taskbeavo-api` | web, Docker, region `frankfurt` | image `CMD` | `healthCheckPath: /healthz`, `preDeployCommand: sh scripts/predeploy.sh` (migrate over `DATABASE_URL_DIRECT` when set) |
| `taskbeavo-worker` | worker, Docker | `celery -A config worker -l info --concurrency 4` | same image |
| `taskbeavo-beat` | worker, Docker | `celery -A config beat -l info --schedule /tmp/celerybeat-schedule` | **exactly one instance**, or scheduled jobs run twice; `/app` is read-only for the app user |
| `taskbeavo-kv` | Key Value (Redis-compatible) | | `maxmemoryPolicy: noeviction`, empty `ipAllowList`. The Celery broker and Channels layer must not evict keys. |

Render's health check uses a Host header outside `ALLOWED_HOSTS`, so production settings put `core.middleware.HealthCheckMiddleware` first; it answers `/healthz` before the host check. `RENDER_EXTERNAL_HOSTNAME` is added to `ALLOWED_HOSTS`.

Database: Neon `eu-central-1` (pooled connection string for the web service, direct connection for migrations) or Render Postgres in Frankfurt with PostGIS enabled. `CREATE EXTENSION postgis;` is part of the first migration.

## Environment and secrets

Use one env group per environment, `taskbeavo-backend-staging` and `taskbeavo-backend-production`, shared by that environment's three services. `REDIS_URL` (from the Key Value) and `OTEL_SERVICE_NAME` are set per service. `render.yaml` declares only the non-secret values and the generated `SECRET_KEY`; the secrets are added to each group by hand in the dashboard (listed in `docs/deploy-render.md`). `backend/core/tests/test_render_blueprint.py` checks both groups and that no secret appears in the Blueprint:

| Variable | Notes |
|---|---|
| `DJANGO_SETTINGS_MODULE=config.settings.production`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | |
| `DATABASE_URL`, `DATABASE_URL_DIRECT` | Neon pooled / direct |
| `REDIS_URL` | from `taskbeavo-kv` |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | live keys exist **only** in Render, never locally |
| `SENTRY_DSN` (EU), `SENTRY_ENVIRONMENT` | release = `RENDER_GIT_COMMIT`, which Render sets |
| `OTEL_EXPORTER_OTLP_ENDPOINT=https://api.eu1.honeycomb.io`, `OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=<key>`, `OTEL_SERVICE_NAME` (per service) | |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_PRIVATE_BUCKET`, `R2_PUBLIC_BUCKET`, `R2_PUBLIC_BASE_URL` (optional `R2_ENDPOINT_URL`) | private bucket for originals, evidence and receipts; public bucket only for metadata-free task photos a task has claimed (`docs/r2.md`) |
| `FIELD_ENCRYPTION_KEY` | OIB/IBAN encryption; rotate with a re-encrypt migration |
| `FINA_CERT_PATH=/etc/secrets/fina.p12`, `FINA_CERT_PASSWORD`, `CIS_URL` | |
| `EMAIL_BACKEND=core.resend.ResendEmailBackend`, `RESEND_API_KEY` | required; SMTP (`EMAIL_HOST*`) also works |
| `WEB_APP_URL` | required; notification email links |
| `TASKBEAVO_LEGAL_NAME`, `TASKBEAVO_OIB`, `TASKBEAVO_ADDRESS`, `TASKBEAVO_CONTACT_EMAIL` | required; TaskBeavo's trader identity in the contract confirmation email |
| `EXPO_ACCESS_TOKEN`, `POSTHOG_KEY` | optional |
| `GEOIP_CITY_DATABASE`, `MAXMIND_ACCOUNT_ID`, `MAXMIND_LICENSE_KEY` (secret) | optional; the GeoLite2 City database the API keeps current itself, and the MaxMind account that downloads it (see below) |

The **FINA certificate** is a Render **secret file** (`fina.p12`, mounted at `/etc/secrets/`). Never commit it or bake it into the image.

### GeoLite2 City database (BEAV-190)

The new-sign-in email names the city and country of a sign-in from a local MaxMind GeoLite2 City database (`accounts/geo.py`). The GeoLite EULA requires keeping it current and deleting old copies, so `accounts/geolite.py` does what `geoipupdate` would, **inside the process that does the lookups, the API service** (manager decision 2026-11-07: on Render each service has its own disk, so a copy downloaded by the Celery worker would never reach the API). There is no Celery job for it.

- `config/asgi.py` calls `geolite.start_background_updates()`: one daemon thread per web process runs `update_database` at once and then every hour, with or without sign-ins (the 30-day deletion must not depend on traffic). A lookup restarts the thread if its process has none. A request never waits; until a copy exists the lookup returns no city.
- A new release is downloaded from `download.maxmind.com` with `MAXMIND_ACCOUNT_ID` and `MAXMIND_LICENSE_KEY` when the file is missing or was built 7 or more days ago, so weekly, but at most once a day, so a late MaxMind release isn't fetched hourly. A failed download is retried an hour later and sign-in keeps working.
- The download is streamed to disk (not held in a worker's memory), checked against the SHA-256 MaxMind publishes and must open as a database; it replaces the old file atomically, so no older copy stays on disk. A file lock (`.geolite.lock`) lets one of gunicorn's workers update at a time; an interrupted download (`.geolite-*`) is deleted on the next run.
- A copy built more than 30 days ago is deleted on the thread's next round, even when downloads keep failing or the MaxMind account was removed (the thread runs whenever `GEOIP_CITY_DATABASE` is set), and `geo.py` never reads one that old.
- Only city and country names are read, never coordinates or postal codes (EULA §5, §6.3).
- Without the license key nothing is downloaded or looked up (local, test, CI), and `usesGeolite2` is false, so the imprint and privacy notice hide MaxMind's attribution. With the three variables set it is true on every instance, even one still downloading.
- `GEOIP_CITY_DATABASE` must be writable by the app user: on Render use `/tmp/geoip/GeoLite2-City.mmdb`; locally a path under `./backend/var/` (git-ignored). `manage.py update_geolite` runs the same step by hand and exits 1 when the download failed.

## Steps

1. Make the change in `backend/Dockerfile`, `docker-compose.yml`, `render.yaml` or `backend/.env.example`.
2. Build and run locally:
   ```bash
   docker build -t taskbeavo-backend backend/
   docker run --rm taskbeavo-backend python -c "import django; from django.contrib.gis import gdal; print(gdal.gdal_version())"
   docker compose up django celery
   curl -fsS localhost:8000/healthz
   ```
3. Run the backend tests in the container: `docker compose run --rm -e REDIS_URL=redis://redis:6379/15 django uv run pytest -q --ds=config.settings.test`. The overrides keep the tests on test settings and off the dev stack's Redis db 0, whose channel layer the tests flush. `sh docker/smoke-test.sh` runs steps 2 and 3 plus the image checks (non-root, GDAL, collectstatic, gunicorn `/healthz`, stripe-cli live-key refusal) and always ends with `docker compose down`.
4. For a new variable: add it to `.env.example` and the settings module (fail fast if it's missing in production). A non-secret value also goes in both env groups in `render.yaml` and `PUBLIC_VALUES` in `test_render_blueprint.py`. A secret or per-environment URL never goes in `render.yaml` (Render ignores `sync: false` inside env groups): add it to the table in `docs/deploy-render.md` and `DASHBOARD_VARIABLES` in the test, then ask the user to set it in both Render env groups. A `render.yaml` change applies to staging and production as soon as it merges to `develop`, because the Blueprint is synced from there.
5. Deploy (**confirm with the user first**): merging to the deploy branch triggers Render auto-deploy. The pre-deploy `migrate` runs before the new version receives traffic. Migrations must be backward-compatible with the running code (expand, then contract).
6. Verify: `/healthz` is green, Sentry shows the new release, Honeycomb receives traces from api, worker and beat, and the worker processes a test outbox row.

## Reference only: migrating to AWS ECS Fargate

Don't do this without a decision. It's kept here so the setup stays portable:
- the same image goes to ECR; three ECS services (api behind an ALB with WebSocket support and `/healthz` checks, worker, beat with desired count 1) in `eu-central-1`;
- ElastiCache Redis (noeviction), RDS PostgreSQL with PostGIS (or keep Neon), Secrets Manager for env vars and the FINA certificate (mounted via an init container or read at startup);
- migrations run as a one-off ECS task before the service update;
- OTel goes through the ADOT collector or direct OTLP to Honeycomb EU.

## Checklist

- [ ] Dockerfile: `python:3.12-slim`, `uv sync --frozen`, GDAL/GEOS/PROJ, non-root, collectstatic, gunicorn + uvicorn worker
- [ ] `docker build` succeeds and GDAL loads in the image
- [ ] Compose runs postgis, redis, django, celery and stripe-cli; `/healthz` OK
- [ ] Render: api, worker and **single** beat in Frankfurt; Key Value with `noeviction`; pre-deploy migrate; health check `/healthz`
- [ ] New variables in `.env.example` and settings; set in the Render env group by the user
- [ ] FINA certificate only as a secret file; no secrets in the image or repo; live Stripe keys only in Render
- [ ] Migrations are backward-compatible
- [ ] User confirmed before deploy; Sentry release and Honeycomb traces verified afterwards

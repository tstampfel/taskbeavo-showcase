# Deploying the backend on Render

The backend runs on Render in Frankfurt from the Blueprint in [`render.yaml`](../render.yaml). The plan section is `docs/PLAN.md`, "Deployment: Docker on Render now, an easy path to AWS later"; the how-to is the `backend-deploy` skill.

| Environment | Branch | Services |
|---|---|---|
| Staging | `develop` | `taskbeavo-api-staging`, `taskbeavo-worker-staging`, `taskbeavo-beat-staging`, `taskbeavo-kv-staging` |
| Production | `main` | `taskbeavo-api`, `taskbeavo-worker`, `taskbeavo-beat`, `taskbeavo-kv` |

All services build `backend/Dockerfile` (root directory `backend/`, so only changes there trigger a deploy) and deploy once the commit's checks pass.

- **API**: the image's default command (gunicorn with uvicorn workers). The health check is `GET /healthz`. The pre-deploy command `sh scripts/predeploy.sh` runs `migrate` on the new image before it takes traffic, over `DATABASE_URL_DIRECT` when set, then `check_legal_documents`, which fails the deploy while a draft legal document is current or scheduled and drafts aren't allowed (always in production; see `LEGAL_DRAFTS_ALLOWED`).
- **Worker**: `celery -A config worker`.
- **Beat**: `celery -A config beat`, exactly one instance. Never scale it: a second Beat runs every scheduled job twice.
- **Key Value**: Redis-compatible, `noeviction`, reachable only from inside the workspace. It holds the Celery queue and the Channels layer, never data that must survive.

## Database

Neon, region `aws-eu-central-1`, one project per environment (or one branch per environment). Enable PostGIS once per database before the first deploy:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

Use the **pooled** connection string (host contains `-pooler`) as `DATABASE_URL` and the **direct** one as `DATABASE_URL_DIRECT`. Both need `sslmode=require`; production settings enforce it. Server-side cursors are off because the pooler runs PgBouncer in transaction mode.

Render Postgres in Frankfurt works too: enable PostGIS the same way, set `DATABASE_URL` to its internal URL and leave `DATABASE_URL_DIRECT` empty.

## Environment variables

Each environment has one env group (`taskbeavo-backend-staging`, `taskbeavo-backend-production`) shared by its api, worker and beat. Render generates `SECRET_KEY` and `JWT_SIGNING_KEY`. `REDIS_URL` comes from the environment's Key Value and `OTEL_SERVICE_NAME` is set per service. Render also sets `RENDER_EXTERNAL_HOSTNAME`, which is added to `ALLOWED_HOSTS`, and `RENDER_GIT_COMMIT`, which Sentry uses as the release (`config/telemetry.py`).

`render.yaml` declares only the non-secret values. Render ignores `sync: false` inside env groups, so after the Blueprint is applied you add the following by hand to each env group (Dashboard → Env Groups → the group → Add Environment Variable). Render never overwrites or removes group variables the Blueprint doesn't declare, so they survive later Blueprint syncs.

| Variable | Staging | Production |
|---|---|---|
| `ALLOWED_HOSTS` | `api-staging.taskbeavo.com` (comma-separated; the `onrender.com` host is added automatically) | `api.taskbeavo.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://api-staging.taskbeavo.com` | `https://api.taskbeavo.com` |
| `DATABASE_URL` | Neon pooled connection string | same, production project |
| `DATABASE_URL_DIRECT` | Neon direct connection string | same, production project |
| `STRIPE_SECRET_KEY` | test key `sk_test_…` / `rk_test_…` | live key; exists only here |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…` of the staging webhook endpoints at `https://<api host>/webhooks/stripe`, comma-separated: the platform endpoint and the Connect endpoint (connected-account events) each have one. The platform endpoint must subscribe to `payment_intent.requires_action` as well as `payment_intent.succeeded` and `payment_intent.payment_failed`: a 3D Secure start restarts the checkout's 30 minutes (BEAV-110). It must also subscribe to `charge.refunded` and `transfer.reversed`, which confirm a cancellation's settlement: without them a cancelled booking never leaves CANCEL_REQUESTED (BEAV-106, BEAV-123) | the same for the live endpoints |
| `SENTRY_DSN` | Sentry EU (`*.de.sentry.io`) DSN | same project |
| `EMAIL_BACKEND` | `core.resend.ResendEmailBackend` (Resend's HTTP API, needs `RESEND_API_KEY`). `django.core.mail.backends.smtp.EmailBackend` with the `EMAIL_HOST*` variables also works. Required: without it, or with the console, file or memory backend, the app refuses to start, because those would put sign-in codes and addresses in the logs | same |
| `RESEND_API_KEY` | a Resend API key with sending access for the staging domain. Required when `EMAIL_BACKEND` is the Resend backend: the app refuses to start without it | production key, production domain |
| `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | only with the SMTP backend: the provider's host and credentials (port 587 with STARTTLS by default; `EMAIL_PORT` and `EMAIL_USE_TLS` override) | production credentials |
| `WEB_APP_URL` | the web app's origin, e.g. `https://staging.taskbeavo.com`; notification emails link to `<WEB_APP_URL>/<locale>/<route>`. Required: the app refuses to start without it | `https://taskbeavo.com` |
| `VAT_ALERT_EMAILS` | optional, comma-separated: who gets the VAT threshold alerts (BEAV-194, `docs/vat-position.md`), i.e. support and the founder. Defaults to `TASKBEAVO_CONTACT_EMAIL` when unset; set it so the founder gets them too | same |
| `TASKBEAVO_LEGAL_NAME`, `TASKBEAVO_OIB`, `TASKBEAVO_ADDRESS`, `TASKBEAVO_CONTACT_EMAIL` | TaskBeavo's company name, OIB, registered address and contact email, shown as the trader for the service fee in the contract confirmation email (BEAV-177 holds the real values). Required: the app refuses to start without them | same |
| `LEGAL_DRAFTS_ALLOWED` | optional, default `true`: placeholder legal text may be published (`publish_legal_document --draft`) and go live. Set `false` to rehearse the production check | never set: always off in production, and the app refuses to start if it is `true` |
| `EXPO_ACCESS_TOKEN` | optional. An Expo access token for push; required by Expo once "enhanced security for push" is turned on in the Expo project (recommended before launch) | same |
| `WEB_PROXY_SECRET` | random string, **exactly the same value** as the web app's `WEB_PROXY_SECRET` on Vercel (Preview); lets the `/api/graphql` route handler pass each visitor's address to the mutation rate limit. Required: the API refuses to start without it, and logs a warning on each request whose secret doesn't match (then every web visitor shares one rate-limit bucket) | same, Vercel Production value |
| `OTEL_EXPORTER_OTLP_HEADERS` | `x-honeycomb-team=<staging ingest key>` | `x-honeycomb-team=<production ingest key>` |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | the Cloudflare account ID and an R2 API token with Object Read & Write on the two staging buckets only. Required: the app refuses to start without them | production token, scoped to the production buckets |
| `R2_PRIVATE_BUCKET` | the staging private bucket (EU jurisdiction, no public access): upload originals, evidence photos, receipts. Required | production private bucket |
| `R2_PUBLIC_BUCKET`, `R2_PUBLIC_BASE_URL` | the staging public bucket (EU jurisdiction) and the origin it is served at, e.g. `https://photos-staging.taskbeavo.com`; holds only task photos re-encoded without metadata that a task has claimed. Required. Bucket setup (CORS, custom domain): `docs/r2.md` | production bucket, `https://photos.taskbeavo.com` |
| `FIELD_ENCRYPTION_KEY` | its own Fernet key (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`); encrypts phone numbers now, OIB and IBAN later. Comma-separated to rotate: the new key first, the old one after it until every row is re-saved. Required: the app refuses to start without it or with a malformed key. Losing it loses every encrypted value. To rotate: new key first, deploy, run `manage.py reencrypt_fields`, then drop the old key; a value no listed key opens makes every request of its user fail | its own key, never shared with staging |
| `PII_DIGEST_KEY` | a random string of at least 50 characters, its own per environment. Keys the digests behind "one verified phone number per account" (and OIB look-ups later). Required: the app refuses to start without it. **Never rotate it without running `manage.py recompute_pii_digests` straight after the deploy**: until then the same number can be verified on a second account | its own key |
| `SMS_BACKEND` | `core.infobip.InfobipSmsBackend`. Required: without it, or with the console or memory backend, the app refuses to start, because those would put phone codes in the logs | same |
| `INFOBIP_BASE_URL`, `INFOBIP_API_KEY`, `INFOBIP_SENDER` | the Infobip account's API host (`https://<id>.api.infobip.com`), an API key with SMS send scope only, and the registered alphanumeric sender (defaults to `TaskBeavo`). The URL and key are required with the Infobip backend. Set a monthly SMS spend alert and Infobip's SMS pumping protection | production account values |
| `INFOBIP_SENDER_BY_REGION` | optional, `REGION=sender` pairs, e.g. `US=+12025550143,CA=+12025550143`. US and Canadian (+1) carriers drop alphanumeric senders, so +1 numbers are refused with `PHONE_COUNTRY_NOT_SUPPORTED` until a numeric or toll-free sender is set for them here | same |
| `FINA_CERT_PASSWORD` | password of the FINA demo certificate | password of the production certificate |
| `POSTHOG_KEY` | as available; empty until the integration lands | same |
| `GEOIP_CITY_DATABASE`, `MAXMIND_ACCOUNT_ID`, `MAXMIND_LICENSE_KEY` | optional; all three or none. A writable path for the MaxMind GeoLite2 City database, e.g. `/tmp/geoip/GeoLite2-City.mmdb` (the image's `/app` is read-only for the app user), and the free MaxMind account (account ID and a license key; the key is a secret). The **API service keeps its own copy** (manager decision 2026-11-07: each Render service has its own disk, so a copy downloaded by the worker would never reach the API, which looks up the sign-in city): one background thread per web process downloads it at startup when the file is missing, then checks every hour, with or without sign-ins and never delaying a request, and downloads a new release once its copy was built 7 days ago (at most once a day); until a copy exists the new-sign-in email names no city. A copy built more than 30 days ago is deleted and never used (GeoLite EULA, BEAV-190). Each deploy starts with an empty disk and downloads again, which stays far inside MaxMind's daily download limit. The worker and beat services need none of these. With them set, the imprint and the privacy notice show MaxMind's attribution. Without the key nothing is downloaded or looked up | same |

Before launch, in the **live** Stripe account, turn **Link off** in both payment method configurations: Connect > Payment methods for connected accounts (Link **Blocked**; this one governs checkout, whose charges are `on_behalf_of` the helper) and Settings > Payment methods, the platform default (Link disabled) (BEAV-190: Link keeps a Stripe login on the payer's device). Stripe refuses it in `excluded_payment_method_types`; if a PaymentIntent still lists `link`, Sentry gets a warning tagged `support.reason=STRIPE_LINK_ENABLED` and checkout continues.

Values already in `render.yaml`: `DJANGO_SETTINGS_MODULE`, `APP_ENV`, `SENTRY_ENVIRONMENT`, `OTEL_EXPORTER_OTLP_ENDPOINT` (`https://api.eu1.honeycomb.io`), `FINA_CERT_PATH`, `CIS_URL` (Porezna's test CIS on staging, the real CIS on production), `FEE_LAUNCH_DATE` (`2027-01-01`, user decision 2026-11-07: the launch date, on which the first service fee price list takes effect and which `/fees` shows; the app refuses to start without it or with an invalid date; later price lists are added in `backend/bookings/money.py` with their own date) and `FEE_VAT` (`NOT_CHARGED`, BEAV-194, user decision 2026-11-07: TaskBeavo launches outside the VAT system under ZPDV čl. 90 st. 1, so `/fees`, pricelist.csv and the receipts say no VAT is charged on the fee; see `docs/vat-position.md`. The app refuses to start without it or while it is `UNDECIDED`; `INCLUDED` is the other accepted value, for when TaskBeavo enters the VAT system).

## Secret files

Add the FINA certificate as a **secret file** named `fina.p12` on each env group (Dashboard → Env Groups → the group → Secret Files). It is mounted at `/etc/secrets/fina.p12`. Staging gets the FINA **demo** certificate. `backend/.dockerignore` excludes `*.p12`, `*.pfx`, `*.pem` and `*.key`, so a secret file that lands in the build context never reaches the image.

## Secrets rules

- No secret value is ever committed. Secrets are not even named in `render.yaml`: they exist only in the dashboard. `backend/core/tests/test_render_blueprint.py` fails if one appears in `render.yaml`, gets a value in `backend/.env.example`, or is missing from the table above.
- Live Stripe keys exist only in the production env group, never locally (the compose `stripe-cli` refuses them).
- Settings fail with `Set the <NAME> environment variable.` and never print values. `/healthz` reports only `ok`/`error` per dependency.

## First deploy (staging)

1. Create the Neon project in `aws-eu-central-1` and run `CREATE EXTENSION postgis;`.
2. In Render: **New → Blueprint**, pick this repository, branch `develop`, and Blueprint file `render.yaml`. Render lists all eight services and both env groups.
3. Apply. The first staging deploy fails at the pre-deploy step with `Set the DATABASE_URL environment variable.`, because the secrets aren't there yet. That's expected.
4. Dashboard → Env Groups → `taskbeavo-backend-staging`: add every variable from the table above. Do the same for `taskbeavo-backend-production` before `main` first deploys; don't merge to `main` until its values are real.
5. Add the `fina.p12` secret file to each env group.
6. Redeploy `taskbeavo-api-staging`, `taskbeavo-worker-staging` and `taskbeavo-beat-staging` (Manual Deploy → Deploy latest commit). Watch the api: build → pre-deploy migrate → health check.
7. Verify: `curl -fsS https://taskbeavo-api-staging.onrender.com/healthz` returns `{"status": "ok", "checks": {"database": "ok", "redis": "ok"}}`. The worker log shows `celery@… ready` and the beat log shows `beat: Starting...`.
8. Optionally add the custom domain `api-staging.taskbeavo.com` on the api service and set the DNS record Render shows.

## Changing things later

- New non-secret variable: add it to `backend/.env.example`, both env groups in `render.yaml` and `PUBLIC_VALUES` in the blueprint test.
- New secret or per-environment URL: add it to `backend/.env.example`, the table above and `DASHBOARD_VARIABLES` in the blueprint test (never to `render.yaml`), then set it in both env groups in the dashboard before merging the code that reads it. Settings must fail fast in production when it is missing.
- Migrations must be backward-compatible with the running code (expand, then contract): the pre-deploy migrate runs while the old version still serves traffic.
- Code deploys by merging: `develop` → staging, `main` → production.
- Infrastructure config does not follow that split. The Blueprint is synced from `develop`, so a change to `render.yaml` (plans, commands, env group values such as `CIS_URL`) applies to **both** environments, production included, as soon as it merges to `develop`.

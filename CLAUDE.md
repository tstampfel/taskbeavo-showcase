# TaskBeavo

TaskBeavo is a local help marketplace in Croatia, launching in Dubrovnik first. Many posters are diaspora families in Germany, Austria and Switzerland booking help for their parents back home, so German, French and remote booking matter as much as Croatian.

The goal is a complete, working product published as a public showcase: every flow works end to end with external services in test or sandbox mode (Stripe test mode, the CIS test environment, EAS preview builds). A real commercial launch is optional and later; its prerequisites (company, FINA certificate, binding opinion, lawyer-approved texts, insurance, helper recruiting, store submissions) are documented in `docs/PLAN.md` but don't block building. The hard rules below apply in full.

The business plan is `docs/PLAN.md`. It is the source of truth for product, legal and tax rules; each skill names the plan sections it implements.

Posters post tasks, helpers send price offers, the poster accepts and pays, the helper taps "Request payment" when done, and the poster taps "Release payment". There is no PIN step. The flow is modelled on Airtasker.

## Monorepo layout

This is one repo using pnpm workspaces and Turborepo.

| Path | Contents |
|---|---|
| `apps/web` | Next.js App Router on Vercel, region `fra1` (Vercel root directory `apps/web`) |
| `apps/mobile` | React Native with Expo for iOS and Android. Uses development builds (`expo-dev-client`), not Expo Go. Built, submitted and updated with EAS; builds are installed on simulators and devices with Expo Orbit. |
| `packages/graphql` | `schema.graphql` exported from the backend, GraphQL operations, GraphQL Code Generator (client preset) output, Apollo cache type policies |
| `packages/ui-tokens` | Brand tokens: green `#1F5C46`, orange `#F2A33A`, off-white `#F5F4EF`, ink `#17211C`, font Manrope |
| `packages/i18n` | ICU translation catalogues for `hr`, `en`, `de`, `fr` |
| `packages/tsconfig`, `packages/eslint-config` | Shared TypeScript and ESLint (flat config) settings; Prettier is configured once at the root |
| `backend/` | Django 5, managed with uv, shipped as a Docker image deployed on Render (Frankfurt) |
| `docs/PLAN.md` | The business plan: flows, legal, tax, data model, build sequence |
| `design/mockups` | Static HTML mockups, one file per screen flow. These are the designs: build a screen in `apps/mobile` or `apps/web` from its mockup, then update or delete the mockup |

A feature normally lands in one branch: backend model and resolver → schema export → `packages/graphql` codegen → web and mobile screens.

## Backend

- Django 5 under ASGI, served by gunicorn with uvicorn workers.
- Strawberry GraphQL and `strawberry-graphql-django` at `/graphql`.
- GraphQL subscriptions run over Django Channels with a Redis channel layer.
- A Celery worker and Celery Beat, using Redis.
- PostgreSQL with PostGIS, on Neon (`eu-central-1`) or Render.
- The only plain HTTP endpoints are `POST /webhooks/stripe` and `GET /healthz`. Everything else goes through GraphQL. (With `DEMO_PAYMENTS=1`, a demo-only Stripe stub adds `/demo-stripe/…/confirm`; it is never mounted in production.)
- Photo uploads use presigned Cloudflare R2 URLs: clients upload directly to R2, and the API only issues URLs and stores keys.

## Frontend data

- Apollo Client with a normalised `InMemoryCache` on web and mobile, using the type policies from `packages/graphql`.
- `graphql-ws` for subscriptions.
- Web uses `@apollo/client-integration-nextjs`. Public SEO pages are server components; logged-in pages are client components with Apollo.
- Mobile persists the cache with `apollo3-cache-persist`, and clears it on logout and on account deletion.
- Active bookings refetch when the app returns to the foreground (and when the subscription reconnects), because events sent while backgrounded aren't replayed.

## Observability

- **Sentry** (EU region) captures errors on backend, web and mobile. Session Replay is off everywhere.
- **Honeycomb** (EU, `api.eu1.honeycomb.io`) receives OpenTelemetry traces.
- An Apollo Link sends a W3C `traceparent` header on every request. The trace context is saved in outbox rows, so Celery work joins the original trace.
- **Never send PII to Sentry or Honeycomb.** PII here means names, email, OIB, IBAN, address, phone and chat text. Use `before_send` scrubbing and attribute allow-lists (see the `observability` skill).

## Local development

`docker-compose.yml` at the repo root runs `postgis`, `redis`, `django`, `celery` and `stripe-cli`, which forwards Stripe test webhooks to `django:8000/webhooks/stripe`. Web and mobile run on the host.

## Hard rules

These rules protect money, legal status and user privacy. If a request conflicts with one, stop and raise the conflict instead of working around it.

a) **Money never moves from a client mutation alone.** A booking becomes `FUNDS_HELD` only when the Stripe webhook confirms the payment. Every Stripe call passes an idempotency key derived from the action, for example `booking:{id}:release:v1`. Webhook events are inserted into `stripe_events` with `ON CONFLICT DO NOTHING` before any processing.

b) **Stripe Connect.**
- Use destination charges with `on_behalf_of` set to the helper's Express account.
- The application fee is the service fee: 12% of the task price, minimum €2.50, less any TaskBeavo credit the poster spends on that booking (rule g), down to €0, when the field is left out. The helper's amount never changes.
- Payouts are manual and triggered by "Release payment".
- TaskBeavo is the helper's agent and never the merchant of record. Never route customer money through TaskBeavo's own bank account.

c) **Booking state changes.** Every transition runs in a transaction with `select_for_update()`, and writes a `booking_events` row and an `outbox` row. The outbox drives subscription events, Expo push and email.

d) **GraphQL.**
- Every type exposes `id`. Mutations and subscription events return the full updated object.
- Subscriptions check permissions when the client connects and on every event.
- Query depth and complexity limits apply, mutations are rate-limited, and introspection is off in production.

e) **Privacy.** Never expose phone numbers (except the on-site contact's phone, below), IBAN or a private individual's OIB to other users. OIB and IBAN are encrypted at rest.
- **Exact address, access details** (doorbell, floor, gate code, key) **and the on-site contact's phone** are visible only to the helper of an accepted booking, and only while it is active (the poster who owns the contact also sees its phone). Everyone else, and the helper once the booking closes, sees the approximate area.
- **A registered business** (obrt or company) shows its business name and OIB on its public profile and on each offer, as a trader must. Its geographic business address goes only to the signed-in poster of a task it offered on, on that offer and at checkout; the public profile shows only the town, because many obrts are registered at the owner's home. Nothing changes for private helpers.

f) **Fiscalization.** Every service fee charged creates a fiscal receipt: the ZKI is signed with the FINA certificate, and the JIR comes from CIS. Refunds create a storno receipt.

g) **Cancellation policy.**

| Situation | Outcome |
|---|---|
| Before the offer is accepted | Free. No payment exists yet. |
| Poster cancels within 1 h of accepting, or more than 24 h before the start | Full refund, including the service fee |
| Poster cancels less than 24 h before the start | The helper gets 25% of the task price (minimum €5, maximum €20). TaskBeavo keeps 12% of that compensation; the rest is refunded. Exception: a trader helper's booking still inside its 14-day withdrawal period is a full refund. |
| Poster cancels at or after the start (no no-fault reason) | Not a cancellation: refused with `WORK_STARTED`, also for a trader helper inside its 14-day period. The poster opens a dispute, or, for a trader helper inside its 14-day period, uses the in-app withdrawal button; support only works out the pro rata split of the part already done. A no-fault reason still gives a full refund. |
| Helper can't get access | The poster chooses within 48 h: reschedule (no fee), accept the call-out, or dispute. The call-out is 25% of the task price (minimum €5, maximum €15); TaskBeavo keeps 12% of it and the rest is refunded. No reply in 48 h accepts the call-out. |
| Helper cancels or doesn't show up | Full refund, plus a €5 credit funded by TaskBeavo if under 24 h before the start (a no-show the poster reports waits 24 h for the helper to contest; support decides a contest): a €5 credit off TaskBeavo's service fee on later bookings (any rest carries over). The helper never pays a penalty. A credit a booking used comes back in full after a failed checkout and after any cancellation, withdrawal or dispute split; it is spent only when the payment is released. On a credited booking TaskBeavo still keeps the full 12% of a compensation; the part the reduced application fee didn't cover comes back from the helper's account with the transfer reversal, never through TaskBeavo's bank account. |
| No one is at fault (weather, hospital, bereavement, emergency, or both agree) | Full refund once the other side agrees (a poster's free cancel after the helper reports running late counts as agreed). A disagreement goes to support. With no reply in 48 h, the poster's request stands (full refund); the helper's goes to support, with no automatic outcome until support decides. If support rejects the reason, the normal policy applies to whoever asked, as of when they asked: a poster under 24 h before the start pays the late-cancel compensation; a poster at or after the start gets the booking back to held funds and may dispute; a helper's request becomes a helper cancellation (full refund, the €5 credit if under 24 h). Both sides are told the decision and what it means. |
| Helper has already requested payment | Not a cancellation: the poster releases payment or opens a dispute |

The consumer's consent to an early start (giving up the 14-day withdrawal right) is captured at checkout. The amounts are computed in `bookings/cancellation.py` and nowhere else.

h) **GDPR.** Account deletion deletes or anonymises personal data within one month. Accounting and fiscal records are kept for 11 years, and DAC7 data for its retention period, in a restricted archive.

i) **i18n and accessibility.** All user-facing strings go through `packages/i18n` (`hr`, `en`, `de`, `fr`). The API returns enum keys and error codes, not text; only emails, push, SMS and PDFs are translated on the backend, in the recipient's language. Legal texts are never machine-translated. Tap targets are at least 44px, and contrast meets WCAG AA.

j) **Service area.** Tasks are allowed only inside an active Croatian `SERVICE_AREA` polygon (PostGIS `ST_Covers`). The server checks it in `postTask`, and re-checks it when an offer is sent, when a booking is created and when a task's address changes; the client check is only a convenience. Out-of-area attempts return `OUT_OF_SERVICE_AREA` and may create a `WAITLIST_ENTRY`. Posters can live anywhere; helpers need a Croatian address and OIB.

k) **Marketplace, not a work organiser.** TaskBeavo must stay outside the digital-labour-platform rules (Zakon o radu Glava IV.a, Directive (EU) 2024/2831). It never sets or suggests a task price, never assigns tasks automatically, and never ranks offers or tasks in a way that steers work to particular helpers (no ranking by acceptance rate, availability or response time). Declining a task, ignoring a notification or going offline carries no penalty, down-ranking or deactivation. TaskBeavo gives no instructions on how the work is done (no uniforms, scripts or tracking during a task), never asks for exclusivity, and puts any automated decision about a helper (fraud flag, suspension) before a human with a reasoned explanation. Private helpers are capped and must register an obrt above the cap. The decision record behind this rule (A4) is kept outside the public repository.

## Commands

Rows marked "(planned)" don't run yet. When you scaffold something, make it answer to exactly these commands, then remove "(planned)" from the row. If a command has to change, update it here and in the skills.

| Purpose | Command |
|---|---|
| Install | `pnpm install` and `cd backend && uv sync` |
| Design mockups | `python3 design/mockups/build.py`, then open `design/mockups/dist/index.html` |
| Local services | `docker compose up -d postgis redis` (PostGIS on host port 55432, Redis 6379); full stack: `docker compose up` (Django on 8000) |
| Placeholder terms (needed to sign up locally) | `docker compose run --rm -T django sh -c 'for l in hr en de fr; do echo "DRAFT – needs professional translation and lawyer review ($l)" > /tmp/terms-$l.md; done && uv run python manage.py publish_legal_document --kind TERMS --version draft-1 --draft --file hr=/tmp/terms-hr.md --file en=/tmp/terms-en.md --file de=/tmp/terms-de.md --file fr=/tmp/terms-fr.md'`. Sign-up is refused (`TERMS_NOT_ACCEPTED`) while no TERMS version is in force; drafts are refused in production |
| Placeholder cancellation policy (needed for checkout locally) | `docker compose run --rm -T django sh -c 'for l in hr en de fr; do printf "DRAFT – needs professional translation and lawyer review ($l)\n\n<!-- checkout:cancellation_policy_private -->DRAFT: I accept the cancellation policy, private helper ($l).<!-- /checkout:cancellation_policy_private -->\n\n<!-- checkout:early_start_private -->DRAFT: I ask for the service to start before the withdrawal period ends, private helper ($l).<!-- /checkout:early_start_private -->\n\n<!-- checkout:cancellation_policy_trader -->DRAFT: I accept the cancellation policy, registered business ($l).<!-- /checkout:cancellation_policy_trader -->\n\n<!-- checkout:early_start_trader -->DRAFT: I ask for the service to start before the withdrawal period ends, registered business ($l).<!-- /checkout:early_start_trader -->\n\n<!-- email:withdrawal_confirmation_trader -->\nSubject: DRAFT withdrawal confirmation {booking_ref} ($l)\nDRAFT: {name} withdrew from booking {booking_ref} on {date} at {time} ({timezone}); confirmation sent to {email} ($l).\n<!-- /email:withdrawal_confirmation_trader -->\n" > /tmp/cancel-$l.md; done && uv run python manage.py publish_legal_document --kind CANCELLATION --version draft-3 --draft --file hr=/tmp/cancel-hr.md --file en=/tmp/cancel-en.md --file de=/tmp/cancel-de.md --file fr=/tmp/cancel-fr.md'`. Checkout can't be paid (`CONSENT_REQUIRED`, and the screens say it's unavailable) while no CANCELLATION version marking both tick-box pairs, `_private` and `_trader`, is in force; the screens show the pair for the offer's helper type and acceptOffer refuses the other (`CONSENT_WORDING_CHANGED`). A version string is never reused, so bump `draft-N` if one was published before. The lawyer's final text must mark all four sentences in all four languages, and the withdrawal confirmation email (`email:withdrawal_confirmation_trader`, its first line `Subject: …`, with the placeholders listed in `backend/legal/withdrawal_confirmation.py`); without it the withdrawal email confirms the facts only and support is flagged |
| Placeholder cookie policy and privacy notice (needed to switch on analytics or marketing locally) | `docker compose run --rm -T django sh -c 'for k in cookies privacy; do for l in hr en de fr; do echo "DRAFT – needs professional translation and lawyer review ($k, $l)" > /tmp/$k-$l.md; done; done && uv run python manage.py publish_legal_document --kind COOKIES --version draft-1 --draft --file hr=/tmp/cookies-hr.md --file en=/tmp/cookies-en.md --file de=/tmp/cookies-de.md --file fr=/tmp/cookies-fr.md && uv run python manage.py publish_legal_document --kind PRIVACY --version draft-1 --draft --file hr=/tmp/privacy-hr.md --file en=/tmp/privacy-en.md --file de=/tmp/privacy-de.md --file fr=/tmp/privacy-fr.md'`. Every consent (`setConsent`, BEAV-83) records the COOKIES version (analytics) or PRIVACY version (news and offers by email or push) in force; while none is, saying no is still logged but agreeing is refused (`CONSENT_UNAVAILABLE`), and the web banner says the choice couldn't be saved. Bump `draft-N` if one was published before. The web loads PostHog only after a logged yes and only with `NEXT_PUBLIC_POSTHOG_KEY` set (EU host by default, `NEXT_PUBLIC_POSTHOG_HOST` to change it) |
| Demo data and walkthrough | `DEMO_PAYMENTS=1` in `backend/.env`, then `docker compose exec django uv run python manage.py seed_demo --reset` (again whenever the demo is stale), and the web app with `NEXT_PUBLIC_DEMO_PAYMENTS=1` (mobile: `EXPO_PUBLIC_DEMO_PAYMENTS=1`). `DEMO_PAYMENTS` is a demo stub (`backend/payments/demo_stripe.py`), refused in production and next to a live key; the walkthrough is `docs/demo/README.md` |
| Stripe webhooks | Runs in compose as `stripe-cli` (`stripe listen --forward-to django:8000/webhooks/stripe`) with the test-mode `STRIPE_SECRET_KEY` from `backend/.env`; it refuses live keys |
| Dev, all JS apps | `pnpm dev` (`turbo run dev`) |
| Web dev | `pnpm --filter @taskbeavo/web dev` |
| Mobile dev | `pnpm --filter @taskbeavo/mobile start` (`expo start --dev-client`) |
| Mobile local dev build | `pnpm --filter @taskbeavo/mobile ios` (`expo run:ios`, needs Xcode) or `android` (`expo run:android`, needs the Android SDK) |
| Backend dev | `cd backend && uv run python manage.py runserver` (or the `django` compose service) |
| Celery | `cd backend && uv run celery -A config worker -l info` and `uv run celery -A config beat -l info` |
| Backend tests | `cd backend && uv run pytest` |
| JS tests | `pnpm turbo run test` |
| i18n catalogue check (CI) | `pnpm --filter @taskbeavo/i18n check:catalogues` (or `pnpm i18n:check`): exits non-zero when a key is missing from any of hr, en, de, fr, a message isn't valid ICU, a key is unused, or a hr, de or fr message is still the English text (outside the `sameAsEnglish` allowlist in `packages/i18n/catalogue-check.json`); prints a warning when de/fr is much longer than en in a layout marked in `packages/i18n/catalogue-check.json` |
| Web e2e | `pnpm --filter @taskbeavo/web exec playwright test` (first run: `pnpm --filter @taskbeavo/web exec playwright install chromium`) |
| Lint, JS | `pnpm turbo run lint` |
| Format, JS | `pnpm format:check` (fix with `pnpm format`) |
| Lint, backend | `cd backend && uv run ruff check . && uv run ruff format --check .` |
| Typecheck, JS | `pnpm turbo run typecheck` |
| Typecheck, backend | `cd backend && uv run pyright` |
| Migrations | `cd backend && uv run python manage.py makemigrations && uv run python manage.py migrate` |
| Backend translations | `docker compose run --rm -T --no-deps django sh -c "uv run python manage.py makemessages -l hr -l en -l de -l fr --no-obsolete --ignore=.venv && uv run python manage.py compilemessages --ignore=.venv"` (the dev image has gettext); commit the `.po` and `.mo` files in `backend/locale/` |
| Schema export | `cd backend && uv run python manage.py export_schema config.schema --path ../packages/graphql/schema.graphql` |
| Codegen | `pnpm --filter @taskbeavo/graphql codegen` |
| Synthetic identifiers check (CI) | `python3 scripts/check_synthetic_identifiers.py`: exits non-zero when any tracked file holds an 11-digit number with a valid OIB check digit that doesn't start with `00000000`, an IBAN with valid check digits whose account part isn't zeros and a counter, or an email address outside the RFC 2606 reserved domains (`example.com`, `example.org`, `example.net`, `*.test`, `*.example`, `*.invalid`, `*.localhost`). Tests, fixtures and the demo seed take OIBs and IBANs from `backend/core/synthetic_identifiers.py` (`synthetic_oib(n)`, `synthetic_iban(n, country)`) and email addresses from the reserved domains, never a real mail provider; an exception goes in `scripts/synthetic-identifiers-allowlist.txt` with its reason |
| Public tree check (CI) | `scripts/check_public_tree.sh [DIR]` (default `.`): exits non-zero on A4 content, a Stripe `acct_`/`pmc_` ID outside the placeholder allowlist, an internal URL or local path (text and binary files), or build output, OS files, local databases and logs (checks 3d, 3e, 3f, 3h in `docs/public-release.md`); prints file names only. Tests: `bash scripts/test_check_public_tree.sh`. CI's `Public tree` job also runs gitleaks with `.gitleaks.toml` over the tree and the new commits. The repository is public: never push with `--no-verify` |
| GraphQL drift check (CI) | `pnpm --filter @taskbeavo/graphql check:generated`: exits non-zero when `schema.graphql` or the generated types are stale. It runs the schema export, which needs GDAL; without GDAL on the host, set `SCHEMA_EXPORT_CMD="docker compose run --rm -T --no-deps django uv run python manage.py export_schema config.schema"` |
| CI | `.github/workflows/ci.yml` runs the backend, JS, GraphQL drift, public tree and web e2e rows above on every PR to `develop`; jobs and the required status checks are in `docs/ci.md` |
| Backend image | `docker build -t taskbeavo-backend backend/`; smoke test of image and compose stack: `sh docker/smoke-test.sh` |
| EAS dev build (planned) | `cd apps/mobile && eas build --profile development --platform ios` (or `android`) |
| EAS production build (planned) | `cd apps/mobile && eas build --profile production --platform all` |
| EAS submit (planned) | `cd apps/mobile && eas submit --profile production --platform ios` (or `android`) |
| EAS update (planned) | `cd apps/mobile && eas update --channel production --message "<what changed>"` |

## Skills and agents

| Name | Use it for |
|---|---|
| `graphql-feature` | An end-to-end API feature: model → Strawberry → tests → schema → codegen → web/mobile |
| `booking-payments` | Booking state machine, Stripe, webhooks, payouts, refunds, cancellations |
| `realtime-notifications` | A new live event: outbox → Channels → subscription → Apollo, push, email |
| `expo-screen` | A mobile screen in `apps/mobile` |
| `expo-release` | Mobile builds, store submissions and OTA updates |
| `nextjs-page` | A web page in `apps/web` |
| `backend-deploy` | Dockerfile, docker-compose, Render services and environment |
| `observability` | Sentry and Honeycomb instrumentation and PII scrubbing |
| `compliance-check` | A pass/fail compliance review of a diff |
| `legal-decision` | Breaking a deadlock when legal or tax opinions conflict: one ranked research round on Claude and Gemini, citations verified, a decision by probability and expected cost, recorded in `docs/decisions/` |
| `gdpr-data-request` | Account deletion or data export |
| `i18n-strings` | Any user-facing text: catalog keys in hr, en, de, fr, plurals, backend translations, layout length |
| `legal-terms` | Legal pages, LEGAL_DOCUMENT versions, TERMS_ACCEPTANCE, re-acceptance and limited mode |
| `trust-safety` | Category rules, contact-detail filter, report and block, incidents, DSA appeals, no-show and no-access flows |
| `auth-and-accounts` | Sign-in, phone verification, sessions, rate limits, suspension, recovery, DATA_REQUEST flows |
| `tax-jobs` | Fiscalization service (ZKI, JIR, storno) and DAC7 reporting |
| `mobile-platform` | Expo native setup: deep links, push and permissions, cache persistence, store privacy forms, Maestro e2e |
| `taskbeavo-manager` | Run the BEAV Jira epics through Orca: pick unblocked tickets, start employee and reviewer workers, track them to an open PR |
| `taskbeavo-employee` | Dispatched by the manager: implement, fix review findings, open the PR for one BEAV ticket |
| `taskbeavo-reviewer` | Dispatched by the manager: read-only review of one BEAV ticket's branch, report with PASS/FAIL |

The agent `payments-reviewer` runs `compliance-check` plus a security review on any change touching payments, bookings, webhooks or fiscalization.

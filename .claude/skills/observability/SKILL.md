---
name: observability
description: Add or check Sentry (EU) and Honeycomb (EU, OpenTelemetry) instrumentation for a TaskBeavo change, covering spans with safe attributes (booking.id, booking.status, stripe.event_type), W3C traceparent propagation from the Apollo Link through Django, trace context stored in outbox rows so Celery joins the trace, and PII scrubbing via before_send and attribute allow-lists. Use this whenever code adds a span, log, Sentry capture, error boundary, Celery task, webhook handler or new service, when debugging "why is this trace broken", or when reviewing whether telemetry leaks names, OIB, IBAN, addresses, phone numbers or chat text.
---

# Observability

Sentry answers "what broke"; Honeycomb answers "what happened, and how slow was it". Both are in the EU, and **neither may receive PII**: names, email, OIB, IBAN, address, phone or chat text. Once PII is in a telemetry vendor it's hard to delete, and it breaks GDPR deletion (rule h). The default is therefore an allow-list: only known-safe attributes go out.

**Plan:** `docs/PLAN.md`, sections "Observability: Sentry for errors, Honeycomb for tracing, on frontend and backend". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

## The setup

- **Backend**: `sentry-sdk` with the Django, Celery and Redis integrations, `send_default_pii=False`, a `before_send` scrubber, `traces_sample_rate=0` (tracing lives in Honeycomb). Every event is tagged with the current OTel `trace_id`.
- **Backend OTel**: the `opentelemetry-instrumentation-django`, `-celery`, `-psycopg`, `-redis` and `-requests` packages export OTLP to `https://api.eu1.honeycomb.io` (`OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=...`). `OTEL_SERVICE_NAME` is `taskbeavo-api`, `-worker` or `-beat`.
- **Web**: `@sentry/nextjs` (EU DSN, `sendDefaultPii: false`, no Replay integration), plus `@vercel/otel` in `instrumentation.ts` exporting to Honeycomb EU.
- **Mobile**: `@sentry/react-native` (EU DSN, `sendDefaultPii: false`, Session Replay off), with source maps uploaded by EAS (see `expo-release`).
- **Propagation**: `packages/graphql/src/links/traceLink.ts` adds `traceparent` (W3C) to every HTTP request and to the `graphql-ws` `connection_init` payload. Django's OTel middleware continues the trace.
- **Outbox**: each `Outbox` row stores `traceparent`. The Celery publisher extracts it with `TraceContextTextMapPropagator().extract(...)` and starts its span as a child, so push, email and subscription fan-out appear in the same trace.

## Allowed span attributes

Use only these keys without review:

| Key | Example |
|---|---|
| `user.id`, `task.id`, `offer.id`, `booking.id`, `outbox.id` | internal IDs (not emails) |
| `booking.status`, `booking.transition` | `FUNDS_HELD`, `release` |
| `stripe.event_type`, `stripe.event_id`, `stripe.object_id` | `payment_intent.succeeded` |
| `amount.cents`, `currency` | `4500`, `eur` |
| `graphql.operation.name`, `graphql.operation.type` | `AcceptOffer` |
| `notification.channel`, `notification.topic` | `push`, `offer.created` |
| `locale`, `app.platform`, `app.version` | `de`, `ios` |
| `fiscal.receipt_id`, `fiscal.status` | no OIB |

**Never** add GraphQL variables, request or response bodies, headers (except `traceparent`), names, email, phone, address, coordinates finer than the city, OIB, IBAN, chat text or free-text task descriptions. Adding a new key means extending the allow-list in `backend/config/telemetry.py` and `packages/graphql/src/telemetry.ts`, with a reviewer.

## Steps

### 1. Instrument the change

- Wrap meaningful work in a span:
  ```python
  with tracer.start_as_current_span("booking.release") as span:
      span.set_attribute("booking.id", str(booking.id))
      span.set_attribute("booking.status", booking.status)
  ```
- New outbox writes include `traceparent=current_traceparent()`.
- New Celery tasks use the instrumented app (they're traced automatically). Pass IDs as arguments, never personal data.
- Expected business errors (for example `OFFER_ALREADY_ACCEPTED`) are not sent to Sentry. Unexpected errors are, and they carry IDs only.
- **Exception messages are `[Filtered]` by default**, including Django's and Postgres's (an `IntegrityError` quotes the duplicate email). To send a readable message, raise `core.errors.SafeMessageError` or a subclass. Its contract: the message is built only from error codes, IDs (pks, booking, task and Stripe IDs) and enum values, never from user input, request data, model text fields or another exception's message. Pass `code=` and `ids={"booking.id": ...}` (allow-listed keys) to tag the event. Wrap a low-level error with `raise SafeMessageError(...) from exc`: the wrapper's message stays readable, the cause stays filtered, and the event is tagged with the cause's `db.sqlstate`, `db.table` and `db.constraint`.
- Log lines reach Sentry with their format string only, never the arguments; put IDs in `extra={...}` with non-PII keys.

### 2. Scrubbing

- Backend `before_send` and `before_breadcrumb` (`backend/config/scrubbing.py`) drop or replace values for keys naming personal data, access details, free text or credentials (the `core.payloads` words plus cookies, auth and session words), replace emails, IBANs, OIBs and phone numbers in any string, strip `request.data`, `request.cookies`, query strings and all but technical headers, and drop unknown event keys and contexts. The SDK's own `data_collection` is fully off.
- Web and mobile use the same `beforeSend` logic from `@taskbeavo/graphql` telemetry helpers. Apollo error breadcrumbs keep the operation name only, never variables.
- An OTel span processor (`AllowListSpanProcessor`) drops any attribute not on the allow-list before export.

### 3. Test

```bash
cd backend && uv run pytest config/tests/test_telemetry.py -q
```

- Unit tests: `before_send` removes a sample OIB, IBAN, phone number and chat text; the span processor drops an unknown attribute.
- Trace continuity test: a request with a `traceparent` → an outbox row stores it → the publisher span has the same `trace_id`.
- Manual check: trigger an action locally with `OTEL_EXPORTER_OTLP_ENDPOINT` pointing at a dev Honeycomb dataset, and confirm that one trace spans web → api → worker.

## Checklist

- [ ] Spans use allow-listed attributes only; new keys added to both allow-lists with review
- [ ] `traceparent` sent by the Apollo Link (HTTP and WebSocket) and continued by Django
- [ ] Outbox rows store `traceparent`; Celery publisher spans join the trace
- [ ] Sentry: EU DSN, `send_default_pii=False`, `before_send` scrubber, Replay off everywhere
- [ ] No GraphQL variables, bodies, names, email, phone, address, OIB, IBAN or chat text in Sentry or Honeycomb
- [ ] Celery task arguments are IDs only
- [ ] Scrubber and trace-continuity tests pass

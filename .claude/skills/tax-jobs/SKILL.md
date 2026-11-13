---
name: tax-jobs
description: Build or change TaskBeavo's backend tax jobs, covering the fiscalization service (FINA certificate from Render secret files, receipt numbering number/premises/device, ZKI signing, CIS request and JIR storage, QR code and PDF receipt email, storno receipts for refunds, retry queue and alerts for receipts without a JIR, certificate expiry warning) and DAC7 reporting (quarterly totals per helper, OIB check-digit validation, two reminders then payout_hold, the January report export and helper statements). Uses Porezna's test environment; never calls production CIS from tests. Use this whenever someone works on FISCAL_RECEIPT, ZKI, JIR, CIS, storno, the fee receipt email, DAC7_QUARTER, OIB validation, payout holds, or asks "how do receipts work" or "what do we send Porezna", even if tax isn't named.
---

# Tax jobs

Since 1 January 2026 every B2C receipt is fiscalized in real time, whatever the payment method, and TaskBeavo is a DAC7 platform operator for personal services with no de minimis. Both duties carry fines (DAC7: €260–€26,540 per legal person, also for inaccurate reports). Under the agent model TaskBeavo fiscalizes **only its 12% fee**, never the task price.

**Plan:** `docs/PLAN.md`, sections "The fiscalization service issues one receipt per fee, in real time", "The DAC7 job turns payouts into an annual report", "Porezna uprava expects four things: DAC7, a VAT ID, fiscalized receipts and eRačun" and "Helpers pay their own tax, and TaskBeavo should stay out of the payer role". Several details are still unconfirmed there (premises code format, late-delivery deadline, DAC7 retention period, the "fees charged" field). Keep them as settings and mark them **NEEDS REVIEW**; don't invent values.

## Safety rules

- **Tests never call production CIS.** `CIS_ENVIRONMENT` is `test` everywhere except production; the client refuses the production URL unless `APP_ENV=production`. Unit tests mock the CIS client at the boundary; an optional integration test runs against Porezna's test endpoint (`cistest.apis-it.hr`) with the FINA **demo** certificate, and is skipped without it.
- The FINA certificate and its password come from Render secret files (`/etc/secrets/fina.p12`, `FINA_CERT_PASSWORD`), never the repo, never logs. Locally, use the demo certificate path from `.env`.
- Receipts are immutable and numbered without gaps. Never delete or renumber; corrections are stornos.
- No OIB, IBAN or names in Sentry or Honeycomb (`observability`); use `fiscal.receipt_id` and `fiscal.status`.

## Fiscalization

### 1. Receipt row (in the release transaction)

The receipt is issued at release for now: a working assumption, open with the tax adviser on BEAV-30 (PLAN.md 4e); BEAV-128 follows the answer. The transaction that moves the booking to RELEASED (`booking-payments`) writes a `FISCAL_RECEIPT`:
- `number` from a per-(premises, device) sequence taken with `select_for_update` on a counter row (not a Postgres sequence, which can skip numbers on rollback), rendered `1042/WEB1/1`;
- `amount_cents` = the fee, `payment_method = "K"`, `operator_mark`, `issued_at` (Europe/Zagreb);
- `status = PENDING`, plus an outbox row `fiscal.receipt_created`.

The same applies to the 12% kept on late-cancel compensation and call-outs.

### 2. ZKI and CIS (Celery)

- Compute the **ZKI**: sign the concatenation of issuer OIB, issue date-time, receipt number, premises, device and total with the certificate's private key (RSA-SHA1 per the CIS spec), then MD5 the signature, hex. Unit-test it against the reference vector from the CIS technical specification.
- Build the `RacunZahtjev` XML, sign it (XMLDSig, enveloped), send it to CIS over TLS, and store the returned **JIR**. Set `status = FISCALIZED`.
- Idempotent: a retry with the same receipt re-sends with the "naknadna dostava" flag set and never takes a new number.

### 3. Receipt email

Render a PDF in Croatian (fiscal receipts must be) with the number, time, operator mark, payment method, ZKI, JIR, QR code (Porezna's verification URL with JIR or ZKI, date-time and amount) and the čl. 90 VAT exemption note while below the threshold (exact wording from the accountant: a setting). The email around it is in the poster's language (`i18n-strings`). Store the PDF in R2 under a private key.

### 4. Storno

A refund of a fee that already has a receipt creates a new `FISCAL_RECEIPT` with a negative amount and `storno_of` pointing at the original, fiscalized like any other. Under the at-release working assumption (BEAV-30), only fees refunded after release need one. The form for a partial refund (a partial storno of the refunded share, or a full storno plus a new receipt for the 12% kept) is open with the tax adviser (BEAV-30, BEAV-131); settle it before building BEAV-131.

### 5. Retries, alerts, certificate

- CIS unreachable: keep the ZKI, set `status = PENDING_JIR`, retry with backoff from a Beat sweep.
- Alert (Sentry issue + email to the founder) for any receipt without a JIR after 1 hour, and again before the legal late-delivery deadline (`FISCAL_LATE_DELIVERY_HOURS`, NEEDS REVIEW).
- A daily Beat job warns 60 days before the certificate's `notAfter`, then weekly.

## DAC7

### 1. Data at onboarding

- Validate the OIB check digit (ISO 7064 MOD 11,10) on input; reject invalid ones.
- Required: legal name, address, OIB, date of birth (individuals), IBAN (from Stripe), member state of residence, VAT or business registration for obrt and companies. `canSendOffers` stays false until complete. OIB and IBAN encrypted at rest.

### 2. Quarterly totals

Every released payout (and call-out or late-cancel compensation paid to the helper) increments `DAC7_QUARTER(helper, year, quarter, consideration_cents, activity_count, fees_cents)` in the release transaction, idempotently keyed by booking. Refunds after release decrement it. Keep `fees_cents` (probably 0, since the fee is charged to posters: NEEDS REVIEW).

### 3. Missing data: reminders, then payout hold

A Beat job finds helpers with incomplete data: reminder 1, reminder 2 at least 60 days later, then `payout_hold = true`. Release still settles the booking, but the payout is skipped and the helper sees why. Clearing the data lifts the hold and pays out what's waiting. Log each reminder as an event.

### 4. January report and statements

```bash
cd backend && uv run python manage.py dac7_report --year 2026 --out dac7-2026.xml --dry-run
```

- Generates the file in the format of Porezna's DAC7 user manual for upload to dac7.porezna-uprava.hr by **31 January**, and validates it against the XSD before writing.
- Emails each helper their statement in their language by the same date, with totals per quarter (and split by payer country for their own tax return).
- Uploading is done by a person; the command never submits.

## Tests

```bash
cd backend && uv run pytest fiscal dac7 -q
```

- Numbering: concurrent receipts get consecutive numbers, no gaps after a rolled-back transaction.
- ZKI matches the reference vector; signed XML validates.
- CIS mocked: success stores JIR; timeout → `PENDING_JIR` → retry; duplicate retry doesn't renumber.
- Storno references the original; the partial refund case follows the form BEAV-30/BEAV-131 settles.
- Certificate expiry warning at 60 days (frozen clock).
- OIB validator: valid, wrong check digit, wrong length.
- DAC7 totals over a quarter boundary; refund after release; reminders 60 days apart then `payout_hold`; report validates against the XSD.

## Checklist

- [ ] Receipt row written in the release transaction (working assumption, BEAV-30); gapless number/premises/device
- [ ] ZKI signed with the FINA certificate from Render secret files; JIR stored
- [ ] PDF in Croatian with QR, ZKI, JIR and VAT note; email in the poster's language
- [ ] Storno for every refunded fee that already has a receipt, in the form BEAV-30/BEAV-131 settles
- [ ] Retry queue; alerts for receipts without JIR; certificate expiry warning
- [ ] Tests mock CIS; production CIS unreachable from non-production
- [ ] OIB check digit validated; DAC7 fields required before offers
- [ ] DAC7_QUARTER updated on release and refund
- [ ] Two reminders 60+ days apart, then payout_hold
- [ ] January report validated, helper statements emailed; unconfirmed values kept as settings and marked NEEDS REVIEW
- [ ] `compliance-check` areas 3 and 4 pass

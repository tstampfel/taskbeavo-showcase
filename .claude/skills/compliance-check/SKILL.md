---
name: compliance-check
description: Review a TaskBeavo diff, branch or PR and output a pass/fail checklist for hard rules a–k, covering fiscal receipt and storno (ZKI/FINA, JIR/CIS), DAC7 seller data fields and quarterly totals, consumer withdrawal/early-start consent, cancellation amounts, GDPR deletion and retention, agent-not-merchant-of-record Stripe setup, no PII in Sentry/Honeycomb telemetry, terms acceptance records (LEGAL_DOCUMENT, TERMS_ACCEPTANCE), consent logging (CONSENT, CONSENT_RECORD), the Croatian service-area rule, the marketplace-not-work-organiser rule (no price setting, steering or penalties for helpers), and i18n coverage in hr, en, de and fr. Use this before merging anything that touches payments, bookings, cancellations, refunds, receipts, user or helper profiles, personal data, account deletion, sign-up, legal pages, consent, checkout UI, onboarding, task location, user-facing text or telemetry, and whenever someone asks "is this compliant", "check GDPR/fiscalization/DAC7", or "review this payment change". booking-payments and the payments-reviewer agent always run it.
---

# Compliance check

This is a review skill: it reads a change and reports, and changes no code unless asked. TaskBeavo is a Croatian marketplace acting as the helper's agent, so a harmless-looking change can break fiscal, tax, consumer or privacy obligations. Examples: a refund shortcut, a new profile field, a checkout redesign, a log line. Be concrete: cite `file:line` and say what's missing.

**Plan:** `docs/PLAN.md`, sections "Compliance checklist: the launch legal to-do", "Croatian law answers the open questions, mostly with "be an agent"", "Terms and conditions: pages, acceptance and updates", "GDPR flows", "Service area: tasks in Croatia only" and "Languages: Croatian, English, German and French". When a criterion below and the plan disagree, report it as NEEDS REVIEW rather than picking one.

This is an engineering check, not legal advice. When the answer depends on legal interpretation, mark it **NEEDS REVIEW** and write the question for the advisor.

## Steps

1. **Collect the diff.**
   ```bash
   git diff develop...HEAD --stat
   git diff develop...HEAD -- backend apps packages docker-compose.yml
   ```
   Read the touched models, migrations, resolvers, webhook handlers, Celery tasks, `cancellation.py`, checkout UI, and Sentry/OTel setup.
2. **Map changes to areas.** If an area isn't touched, mark it N/A with a one-line reason; don't skip it silently.
3. **Check each area** against the criteria below. Where a criterion is testable, run the related tests (`cd backend && uv run pytest <app> -q`).
4. **Output the report** in the exact format at the end.

## Areas and criteria

### 1. Money flow and idempotency (rules a, c)

- FUNDS_HELD, REFUNDED, PARTIALLY_SETTLED and PAID_OUT are set only from webhook handlers, never from a client mutation. RELEASED is the poster's "Release payment" (or support's decision), as `docs/PLAN.md` says; the payout it starts is a server-side Stripe call with an idempotency key, and only `payout.paid` sets PAID_OUT.
- Every Stripe call has a deterministic idempotency key derived from the action.
- The webhook verifies the signature, and inserts into `stripe_events` with `ON CONFLICT DO NOTHING` before processing.
- Every booking transition uses `atomic` + `select_for_update` and writes `booking_events` + `outbox`.

### 2. Agent, not merchant of record (rule b)

- Destination charge with `on_behalf_of` = the helper's Express account, `transfer_data.destination` = the same account, and `application_fee_amount` = the service fee, 12% of the task price with a €2.50 minimum (`state_machine.service_fee_cents`).
- Payouts are manual, triggered only by "Release payment".
- No charge on the platform account followed by a separate payout; no customer money via TaskBeavo's bank account.
- Copy, receipts and terms present the helper as the provider and TaskBeavo as the intermediary.

### 3. Fiscal receipt and storno (rule f)

- Every service fee charged (including the 12% kept on late-cancel or call-out compensation) creates a fiscal receipt, written in the release transaction under the working assumption (BEAV-30; the tax adviser may move it to the charge). A storno's form for a partial refund is open (BEAV-30, BEAV-131).
- The ZKI is signed with the FINA certificate (loaded from the secret file, never committed), and the JIR is obtained from CIS. If CIS is unavailable, the receipt is stored with its ZKI and a retry task re-sends it within the legal deadline.
- Refunds create a storno receipt that references the original.
- Numbering is sequential per premises and device, without gaps; receipts are immutable.

### 4. DAC7 (rule h retention, reporting)

- Helper onboarding stores the DAC7 seller fields: legal name, primary address, TIN/OIB and issuing country, date of birth (individuals), IBAN/financial account, and VAT or business registration (entities).
- Quarterly totals per seller can be derived from stored records: consideration paid, fees and commissions withheld, and number of activities.
- The fields survive account deletion in the restricted archive.

### 5. Consumer withdrawal and early-start consent (rule g)

- Checkout shows an explicit, **unticked** consent checkbox, not bundled with the terms, in hr, en, de and fr, for starting the service within 14 days, including the acknowledgement that the withdrawal right is lost once the service is fully performed.
- The server stores the timestamp, user, booking, consent text version and locale, and refuses to create the PaymentIntent without it.
- The confirmation email repeats the information (durable medium).

### 6. Cancellation amounts (rule g)

All amounts come from `bookings/cancellation.py`, and the tests cover:

| Case | Expected |
|---|---|
| Before acceptance | free |
| Within 1 h of accepting, or over 24 h before the start | full refund including the fee |
| Late cancel (under 24 h) | helper gets 25% of the price, min €5, max €20; TaskBeavo keeps 12% of that; the rest is refunded. Trader helper inside the 14-day withdrawal period: full refund |
| No access, call-out accepted or no reply in 48 h | helper gets 25% of the price, min €5, max €15; TaskBeavo keeps 12% of that; the rest is refunded. Reschedule: nothing refunded |
| Helper cancel or no-show | full refund, plus a €5 TaskBeavo-funded credit if under 24 h |
| No fault | full refund |

Check rounding (Decimal, integer cents) and the matching receipts and stornos.

### 7. GDPR deletion and retention (rule h)

- New personal-data fields have a purpose and a deletion behaviour (delete, anonymise or archive).
- Deletion completes within one month and covers app data, R2 files, Expo tokens, Resend, PostHog and open WebSockets. See `gdpr-data-request`.
- Accounting and fiscal records are kept 11 years, and DAC7 data for its retention period, in the restricted archive.
- Phone, IBAN and a private individual's OIB are never shown to other users. Exact address and access details reach only the helper of an active accepted booking; a business OIB may appear on its public profile. OIB and IBAN are encrypted at rest (rule e).

### 8. No PII in telemetry

- Sentry: `send_default_pii=False`/`sendDefaultPii: false`, a `before_send` scrubber present, Replay off, and no names, email, OIB, IBAN, address, phone or chat text in tags, extra data, breadcrumbs or messages.
- Honeycomb: span attributes come from the allow-list only (IDs, statuses, event types, amounts in cents); no request or response bodies, no GraphQL variables containing PII.
- Logs and Celery task arguments carry IDs, not personal data.

### 9. Terms acceptance records (plan: "Terms and conditions: pages, acceptance and updates")

- Sign-up (email, Google and Apple alike) is rejected by the **server** unless the unticked terms checkbox was ticked; the client-side disabled button is not enough.
- Every acceptance writes a `TERMS_ACCEPTANCE` row: document kind, version, language, time, channel (app or web). Helper onboarding writes one for helper terms; checkout writes one for the cancellation policy.
- Each version is an immutable `LEGAL_DOCUMENT` row (kind, version, language, effective date, material-change flag). Published text is never edited in place.
- After a material change, a user who hasn't accepted the current version is in limited mode: can see history, finish open bookings, export data and delete the account, but can't post, offer or book. Open bookings keep the version they were booked under.
- Legal texts in each language are marked as professionally translated, not machine-translated. Placeholder drafts never reach production users. See `legal-terms`.

### 10. Consent logging (plan: "GDPR flows", section 3)

- Every consent (analytics, marketing email and push, cookies, waitlist launch email) writes a `CONSENT` row with purpose, granted, version and time. Withdrawal writes a new row; history isn't overwritten.
- Marketing is opt-in and off by default, separate from booking and payment messages. Analytics (PostHog) runs only after consent, or in cookieless mode; the cookie banner's Reject is as easy as Accept.
- Checkout's early-start request and the withdrawal information shown are stored as `CONSENT_RECORD` on the booking (with terms and withdrawal-notice versions, and the helper's trader status as shown).
- Consent rows are archived, not deleted, on account deletion (proof of compliance).

### 11. Service area (rule j; plan: "Service area: tasks in Croatia only")

- `postTask`, task address edits, sending an offer and creating a booking all check the task point server-side against active `SERVICE_AREA` polygons (`ST_Covers`), returning `OUT_OF_SERVICE_AREA`. A client-only check is a FAIL.
- Address search is restricted to Croatia (`hr`).
- Helpers see and bid only on tasks inside their selected areas; helper onboarding requires a Croatian address and a valid OIB.
- Nothing restricts where a **poster** lives.
- Tests cover a point inside, a Croatian point outside active areas, and a point abroad.

### 12. i18n coverage (rule i; plan: "Languages: Croatian, English, German and French")

- Every new user-facing string, error code, push, email, SMS and PDF text has keys in all four catalogs (`hr`, `en`, `de`, `fr`); the missing-key check passes. No hard-coded text in components.
- Croatian plurals use ICU `one`/`few`/`other`.
- Backend messages are rendered in the recipient's locale, not the sender's.
- Legal texts (terms, privacy, withdrawal information, cancellation policy) are flagged for professional translation and lawyer review, never machine-translated. Fiscal receipts are issued in Croatian.

### 13. Marketplace, not a work organiser (rule k)

- No task price is set, suggested or defaulted by TaskBeavo; the helper's offer sets the price.
- No automatic assignment. Offer and task ordering doesn't reward acceptance rate, availability or response time, and matches the "How offers are ranked" page.
- No penalty, down-ranking or deactivation for declining, ignoring a notification or going offline.
- No instructions on how the work is done, no tracking during a task, no exclusivity terms.
- Automated decisions about a helper (fraud flags, suspension) go to a human with a reasoned explanation.
- The private-helper cap (BEAV-99) is enforced server-side in `sendOffer`.

## Report format

Always output exactly this:

```
# Compliance check: <branch or change>

| # | Area | Result | Notes |
|---|---|---|---|
| 1 | Money flow & idempotency (a, c) | PASS / FAIL / NEEDS REVIEW / N/A | ... |
| 2 | Agent not merchant of record (b) | ... | ... |
| 3 | Fiscal receipt & storno (f) | ... | ... |
| 4 | DAC7 fields & quarterly totals | ... | ... |
| 5 | Withdrawal / early-start consent (g) | ... | ... |
| 6 | Cancellation amounts (g) | ... | ... |
| 7 | GDPR deletion & retention (e, h) | ... | ... |
| 8 | No PII in telemetry | ... | ... |
| 9 | Terms acceptance records | ... | ... |
| 10 | Consent logging | ... | ... |
| 11 | Service area (j) | ... | ... |
| 12 | i18n coverage (i) | ... | ... |
| 13 | Marketplace, not a work organiser (k) | ... | ... |

## Failures
- <#>: <problem>, <file:line>, <fix>

## Needs review
- <#>: <question for legal/tax advisor>

## Overall: PASS / FAIL
```

Overall is FAIL if any row is FAIL.

## Checklist

- [ ] Diff read across backend, apps, packages and compose
- [ ] All 13 areas marked, with reasons for N/A
- [ ] Every FAIL has `file:line` and a fix
- [ ] Legal interpretation questions marked NEEDS REVIEW
- [ ] Report in the exact format

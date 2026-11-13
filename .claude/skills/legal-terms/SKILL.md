---
name: legal-terms
description: Build or change TaskBeavo's legal pages and acceptance mechanics, covering LEGAL_DOCUMENT versions (terms, helper terms, privacy, cancellation, cookies, imprint) per language, the required unticked terms checkbox at sign-up enforced on the server for email, Google and Apple, helper terms in onboarding, cancellation policy and early-start consent at checkout, TERMS_ACCEPTANCE records, the 30-day notice for material changes, "We've updated our terms" re-acceptance with limited mode, and open bookings keeping their version. Use this whenever someone touches /terms, /privacy, /helper-terms, /cancellation, /cookies, /imprint, a sign-up or checkout checkbox, a new terms version, or asks "how do we prove they accepted", even if "legal" isn't named.
---

# Legal terms

The final wording comes from the lawyer (checklist item 9 in the plan). What we build now is the **mechanics**: every account is created only after accepting the terms, every acceptance is provable, and a terms change can't silently apply to someone who never saw it. No real users or payments before the lawyer-approved versions are live (checklist item 9a).

**Plan:** `docs/PLAN.md`, sections "Terms and conditions: pages, acceptance and updates", "Sign-up and login", "Becoming a helper", "Consumer withdrawal law turns TaskBeavo's fee into a refundable item" and "Compliance checklist: the launch legal to-do". Read them first.

## Documents

| Kind | Page | Accepted when |
|---|---|---|
| `TERMS` | `/[locale]/terms` | Sign-up, required |
| `PRIVACY` | `/[locale]/privacy` | Shown at sign-up; informs, no consent |
| `HELPER_TERMS` | `/[locale]/helper-terms` | Helper onboarding, required |
| `CANCELLATION` | `/[locale]/cancellation` | Checkout, with the early-start consent |
| `COOKIES` | `/[locale]/cookies` | Cookie banner choice (logged as `CONSENT`) |
| `IMPRINT` | `/[locale]/imprint` | Never accepted; legally required company info (j.d.o.o. name, OIB, address, court register, email) |

All are linked in the web footer, sign-up, checkout and app settings, in hr, en, de and fr.

## Data model

- `LEGAL_DOCUMENT(kind, version, language, body, body_sha256, effective_at, material_change, is_draft, published_at)` (`legal.LegalDocument`). Rows are **immutable** once published (the model and a database trigger refuse updates and deletes); a change is a new version. Unique on `(kind, version, language)`.
- `TERMS_ACCEPTANCE(user, user_reference, document, accepted_at, platform, sign_in_method)` (`legal.TermsAcceptance`). `document` is the exact `LEGAL_DOCUMENT` row (kind, version, language); `platform` is `web`, `ios` or `android`; `sign_in_method` (`email`, `google`, `apple`) is set on the sign-up acceptance only. Append-only. An optional `booking` is added with bookings for the checkout cancellation policy. Kept 11 years like accounting records: account deletion moves them to the restricted archive, detached from the user (`user` NULL, `user_reference` kept).
- `Booking.terms_version` and `Booking.cancellation_version` are set at booking time; the booking is governed by those versions even after a new one takes effect.
- Early-start consent is a `CONSENT_RECORD` on the booking (see `booking-payments` and `compliance-check` area 5).

## Steps

### 1. Sign-up checkbox (all three methods)

- An **unticked** required checkbox: "I agree to the Terms and conditions and have read the Privacy notice", both linked to the current version in the user's language. "Create account" stays disabled until ticked.
- The server enforces it: `signUp` (email code), and the Google and Apple completion step, take `acceptedTermsVersion`. Reject with `TERMS_NOT_ACCEPTED` if it's missing or isn't the current `TERMS` version. Google and Apple sign-in must pass through the same account-details step before the account row exists; never create the user in the OAuth callback.
- In the same transaction as the user row, write the `TERMS_ACCEPTANCE`.

### 2. Helper onboarding

The last onboarding step shows helper terms (own prices, freedom to refuse, no fees, authority to instruct Stripe, tax and DAC7 duties) and requires acceptance before `canSendOffers` turns true. Write a `TERMS_ACCEPTANCE` for `HELPER_TERMS`.

### 3. Checkout

Two separate, unticked checkboxes, not bundled with each other or with the terms: accept the cancellation and refund policy, and the express request to start within 14 days with the acknowledgement that the withdrawal right is lost once the service is fully performed. The server refuses the PaymentIntent without both (`CONSENT_REQUIRED`). Write `TERMS_ACCEPTANCE(CANCELLATION, booking=…)` and the `CONSENT_RECORD`; the confirmation email repeats the information (durable medium).

### 4. Publishing a new version

- **Small fix** (typo, contact details): publish with `material_change=false`; nobody is asked to re-accept.
- **Material change** (fees, cancellations, liability): publish with `effective_at` at least **30 days** ahead. An outbox event sends the email and in-app notice, in each user's language, listing what changes (`realtime-notifications`).
- A management command does this, and refuses a material change with less than 30 days' notice:
  ```bash
  cd backend && uv run python manage.py publish_legal_document --kind TERMS --version 2027-03 \
    --file hr=terms-hr.md --file en=terms-en.md --file de=terms-de.md --file fr=terms-fr.md \
    --material --effective 2027-04-15
  ```
  One version needs all four languages before it can take effect, so the command takes them together and refuses otherwise. `--draft` marks placeholder text; `check_legal_documents` (run in the Render pre-deploy step) fails production while a draft is current or scheduled. A small fix only has to come after the version in force, so a typo can be fixed while a material change waits out its notice (the scheduled version doesn't get the fix). A scheduled version that turns out wrong is withdrawn before it takes effect, if nobody accepted it: `manage.py withdraw_legal_document --kind TERMS --version 2027-03 --reason "..."` adds an immutable `LegalDocumentWithdrawal`; the version stays as published and is skipped from then on, and its version string can't be reused. The models, rules and commands live in `backend/legal/`.

### 5. Re-acceptance and limited mode

- After `effective_at`, a user whose latest `TERMS` acceptance is older than the current material version gets `viewer.mustAcceptTerms = true` and sees "We've updated our terms" (summary + link + Accept) on the next app open or login.
- Until they accept they are in **limited mode**: they can see history, finish open bookings, chat on those bookings, download their data and delete their account. `postTask`, `sendOffer` and `acceptOffer` return `TERMS_UPDATE_REQUIRED`. Enforce this in a shared permission class, not per screen.
- Open bookings continue under `Booking.terms_version`.

### 6. Pages

Build the pages with `nextjs-page` as public server components that render the current `LEGAL_DOCUMENT` for the locale, with a version and effective date at the top and links to previous versions. Mobile opens the same URLs in an in-app browser. Text comes from the lawyer and professional translators; follow `i18n-strings` for the surrounding UI only. Until then, show a clearly marked draft, and make production refuse to start if any current legal document is still a draft (`LEGAL_DRAFTS_ALLOWED=false`).

## Tests

```bash
cd backend && uv run pytest legal accounts bookings -q
```

- Sign-up without the checkbox is rejected for email, Google and Apple; with it, one `TERMS_ACCEPTANCE` row with the right version, language and channel.
- A material version with under 30 days' notice is refused; one missing a language is refused.
- After the effective date, a non-accepting user can't post, offer or book but can view history, finish a booking, export and delete.
- A booking made under v1 still reports v1 after v2 takes effect.
- Checkout without either checkbox: no PaymentIntent.

## Checklist

- [ ] `LEGAL_DOCUMENT` versions immutable, per language, with effective date and material flag
- [ ] Unticked terms checkbox at sign-up; server rejects without it for email, Google and Apple
- [ ] `TERMS_ACCEPTANCE` rows for terms, helper terms and cancellation policy (version, language, time, channel)
- [ ] Helper terms required before sending offers
- [ ] Checkout: separate cancellation-policy and early-start checkboxes, enforced server-side
- [ ] Material changes: 30-day notice in each user's language; re-acceptance screen; limited mode enforced server-side
- [ ] Open bookings keep their version
- [ ] Legal text professionally translated and lawyer-approved before real users; drafts blocked in production
- [ ] `compliance-check` areas 5, 9 and 10 pass

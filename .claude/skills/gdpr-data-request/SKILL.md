---
name: gdpr-data-request
description: Implement, change or run a TaskBeavo GDPR account deletion or data export (access/portability request). Covers blocking while funds are held or a dispute is open, deleting or anonymising app data and Cloudflare R2 files, cleaning third parties (Resend, PostHog, Expo push tokens, open WebSocket connections), moving accounting/fiscal (11 years) and DAC7 records to the restricted archive, the confirmation email, and logging completion within one month. Use this whenever someone works on "delete my account", "export my data", a support ticket asking for erasure or a copy of data, a new model holding personal data, or asks how deletion or retention works.
---

# GDPR data request

Users can ask TaskBeavo to delete their account or export their data. The law gives one month to complete it (rule h). Deletion is not "delete every row": accounting and fiscal records must be kept for 11 years and DAC7 data for its retention period, and money in flight must not be orphaned. The flow is therefore: **check blockers → anonymise and delete → archive what must be kept → clean third parties → confirm → log.**

Running a request against production deletes real data that can't be recovered, so **confirm with the user before executing it outside local or test environments**.

**Plan:** `docs/PLAN.md`, sections "GDPR: deletion requests and what must be kept"; "GDPR flows"; "Company, DSA and GDPR basics". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

The user-facing entry points (Delete my account, Download my data, consent toggles, requests from on-site contacts) are built with `auth-and-accounts`; this skill owns what the background jobs do.

## Data map

Keep `backend/privacy/data_map.py` up to date. Every model with personal data declares one of:

| Action | Examples |
|---|---|
| `DELETE` | push tokens, sessions, drafts, saved searches, chat messages the user sent (replaced with "deleted message"), task photos (R2) |
| `ANONYMISE` | user row (name → "Deleted user", email → `deleted+<uuid>@invalid`), reviews (keep the rating, drop the text and author name), tasks (drop the description and exact address, keep the category, city and price for statistics) |
| `ARCHIVE` | fiscal receipts (launch-only: the showcase issues none, hard rule f), invoices, payment records, `stripe_events` references, DAC7 seller data (legal name, address, OIB/TIN, DOB, IBAN), consent records. Move these to the `archive` schema, readable only by the `archive_reader` role, with a `retain_until` date (11 years for accounting/fiscal; DAC7 retention for DAC7). |

A new model with personal data and no entry in the data map makes `test_data_map_complete` fail. That's intentional.

**Phone number (BEAV-72, decided 2026-11-01): `DELETE`.** `User.phone_number` (encrypted), `User.phone_digest` and `User.phone_verified_at` go with the account, and so do all `PhoneCode` rows. No digest or other fingerprint of the number is kept for fraud or ban-evasion checks. A hard delete of the user cascades; if the deletion job anonymises the user row instead (BEAV-153), it must set `phone_number=""`, `phone_digest=None`, `phone_verified_at=None` and delete the user's `PhoneCode` rows explicitly, because nothing cascades then. The owner's export includes their full number.

## Deletion flow (`privacy.services.delete_account(user_id)`)

1. **Check blockers.** Refuse with a translated reason, and offer to retry later, if the user:
   - has a booking in `AWAITING_PAYMENT`, `FUNDS_HELD`, `PAYMENT_REQUESTED` or `PARTIALLY_RELEASED`;
   - has an open dispute (`DISPUTED`);
   - is a helper with a pending payout or a negative Stripe balance.
   Record the request with status `BLOCKED` so the one-month clock is tracked, and notify support.
2. **Lock the user.** Deactivate login, revoke sessions and refresh tokens, and close open WebSocket connections by sending a `force_disconnect` to the Channels group `user.{id}`.
3. **Archive** the `ARCHIVE` items in one transaction, with `retain_until` set.
4. **Anonymise and delete** the app data according to the data map, in the same transaction, and write an `outbox` row `privacy.account_deleted`.
5. **Third parties** (Celery tasks from the outbox, retried, each logging its result):
   - R2: delete all objects under `users/{id}/` and the task photo keys;
   - Expo: delete the push tokens;
   - Resend: delete or suppress the contact, and keep a suppression entry so no more email is sent;
   - PostHog: delete the person and its events (`distinct_id` = user ID);
   - Stripe: don't delete the connected account or customer while archived records reference them. Redact the customer's personal fields where Stripe allows it, and mark the record NEEDS REVIEW for helper Express accounts;
   - Sentry and Honeycomb should hold no PII. If an incident ever put some there, file a vendor deletion request.
6. **Send the confirmation email** (hr, en, de or fr, in the user's last locale) to the address captured before anonymising. The address is then discarded.
7. **Log completion** on the `DATA_REQUEST` row (type, requester kind, identity check, received, due = received + 1 month, completed, outcome) plus per-step results. Never log personal data. Alert if any request is older than 25 days and not completed.

## Export flow (`privacy.services.export_account(user_id)`)

- Collect the user's data as listed in the data map (profile, tasks, offers, bookings, messages, reviews, consents, payments summary), as JSON plus a README, with photos as R2 copies.
- Zip it, upload it to a private R2 key, and email a presigned link that expires in 7 days, in the user's locale.
- Exclude other users' personal data, for example the other side's phone number or exact address.

## Commands

```bash
cd backend && uv run pytest privacy -q
# local/test only; production requires user confirmation:
uv run python manage.py privacy_delete --user <id> --dry-run
uv run python manage.py privacy_delete --user <id>
uv run python manage.py privacy_export --user <id>
```

`--dry-run` prints the counts per model and action, and the blockers, without changing anything.

## Tests

- Blocked when funds are held or a dispute is open, and the request is recorded as `BLOCKED`.
- After deletion: no personal data remains in app tables; archived rows exist with the right `retain_until`; R2, Expo, Resend and PostHog calls were made (mocked) and are idempotent on retry.
- `test_data_map_complete` covers every model with personal fields.
- The export contains the user's data and no other user's personal data.

## Checklist

- [ ] Blockers enforced: funds held, open dispute, pending payout
- [ ] Data map updated for any new personal-data field; completeness test passes
- [ ] App data deleted or anonymised per the data map; R2 files removed
- [ ] Accounting/fiscal kept 11 years, DAC7 for its retention period, in the restricted archive
- [ ] Sessions revoked and WebSockets force-disconnected; Expo tokens removed
- [ ] Resend and PostHog cleaned; Stripe handled without breaking archived references
- [ ] Confirmation email sent in the user's locale
- [ ] Completion logged without PII; alert for requests older than 25 days
- [ ] User confirmed before running against production

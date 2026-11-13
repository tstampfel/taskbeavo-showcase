---
name: booking-payments
description: Safely change TaskBeavo's booking state machine or Stripe Connect flow under hard rules a, b, c and g. This covers PaymentIntent creation in acceptOffer, the Stripe webhook, FUNDS_HELD, "Request payment", "Release payment" and payouts, partial release, refunds and transfer reversals, cancellation outcomes (late cancel, no access, helper cancel), idempotency keys, booking_events and outbox, and tests with Stripe test-mode fixtures. Use this for ANY change touching booking status, PaymentIntents, the 12% fee, payouts, refunds, cancellations or webhook handling, even small ones like "add a cancelled state" or "handle charge.refunded". Always finishes by running compliance-check.
---

# Booking and payments

TaskBeavo is the helper's agent, not the merchant of record. The poster's card is charged with a **destination charge** to the helper's Stripe Express account, and TaskBeavo takes only the 12% application fee. Three properties must survive every change:
1. nothing says "paid" unless Stripe confirmed it;
2. any Stripe call or webhook can run twice without double effects;
3. every state change leaves an audit row and a reliable notification.

**Plan:** `docs/PLAN.md`, sections "State machine for task, booking and payment"; "Money flow: destination charges keep TaskBeavo out of possession of funds"; "TaskBeavo cancellation policy (revised, consumer-safe)"; ""Couldn't do the job": how Airtasker handles it and the TaskBeavo flow"; "Checkout with card authentication (3D Secure)". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

The no-access and no-show flows on the day are in `trust-safety`; receipts and stornos are built in `tax-jobs`; the checkout consent and policy acceptance in `legal-terms`.

## The state machine

The booking side of `docs/PLAN.md`, "State machine for task, booking and payment". The table of moves and who may make them is `TRANSITIONS` in `backend/bookings/state_machine.py`.

```
poster accepts an offer ─▶ AWAITING_PAYMENT     (offer ACCEPTED, other offers DECLINED)
AWAITING_PAYMENT ─ payment_intent.succeeded ─▶ FUNDS_HELD                    (webhook only)
AWAITING_PAYMENT ─ card declined ─▶ AWAITING_PAYMENT                        (poster tries another card)
AWAITING_PAYMENT ─ offer withdrawn / no card attempt 30 min (60 max) ─▶ PAYMENT_FAILED     (offers reopen, task open again)
FUNDS_HELD ─ helper "Request payment" ─▶ PAYMENT_REQUESTED
PAYMENT_REQUESTED ─ poster "Release payment" (or support) ─▶ RELEASED         (payout created)
RELEASED ─ payout.paid ─▶ PAID_OUT                                           (webhook only)
FUNDS_HELD ─ late / no-fault / helper cancel, no-show ─▶ CANCEL_REQUESTED ─▶ FUNDS_HELD (withdrawn)
FUNDS_HELD ─ helper can't get in ─▶ NO_ACCESS_REPORTED ─▶ FUNDS_HELD (rescheduled)
FUNDS_HELD ─ safety incident ─▶ INCIDENT_PAUSED ─▶ DISPUTED (support)
FUNDS_HELD / CANCEL_REQUESTED / NO_ACCESS_REPORTED / PAYMENT_REQUESTED ─ problem or chargeback ─▶ DISPUTED
DISPUTED ─ staff decide ─▶ RELEASED, or the refund / split confirmed by Stripe
… ─ charge.refunded ─▶ REFUNDED or PARTIALLY_SETTLED                         (webhook only)
… ─ transfer.reversed, settlement with no refund (R = 0) ─▶ PARTIALLY_SETTLED (webhook only)
PAID_OUT / PARTIALLY_SETTLED / REFUNDED ─ review window ends ─▶ CLOSED
```

All transitions live in `backend/bookings/state_machine.py`. Nothing else assigns `booking.status`.

## Steps

### 1. Design the change

- Write down the source state(s), target state, actor (poster, helper, webhook or Celery), and whether money moves.
- Client mutations may only *request* money movement. The money-confirmed states, `FUNDS_HELD`, `REFUNDED`, `PARTIALLY_SETTLED` and `PAID_OUT`, are set only from webhooks, through `apply_stripe_transition` (rule a).
- `RELEASED` is the poster's "Release payment" (or support's decision), per the plan and rule b. The money was already confirmed held by a webhook; releasing starts the server-side payout with key `booking:{id}:release:v1`, and only `payout.paid` sets `PAID_OUT`.

### 2. Transition shape (rule c)

```python
with transaction.atomic():
    booking = Booking.objects.select_for_update().get(pk=booking_id)
    assert_transition_allowed(booking.status, NEW, actor)
    booking.status = NEW
    booking.save(update_fields=["status", "updated_at"])
    BookingEvent.objects.create(booking=booking, type=..., actor=actor, data=...)
    Outbox.objects.create(topic="booking.updated", aggregate_id=booking.id,
                          traceparent=current_traceparent())
```

- `select_for_update` stops a double tap, or a webhook racing a mutation, from passing the check twice.
- Writing the outbox row in the same transaction means a notification exists if and only if the change committed.
- Stripe API calls happen **outside** the DB transaction; store the returned Stripe ID afterwards.

### 3. acceptOffer: creating the PaymentIntent

```python
stripe.PaymentIntent.create(
    amount=price_cents + fee_cents,                # the poster pays the fee on top: €30 task → €33.60
    currency="eur",
    automatic_payment_methods={"enabled": True},   # payment_method_types is rejected by this API version
    application_fee_amount=fee_cents,              # 12%, min €2.50, half up, integer cents
    transfer_data={"destination": helper.stripe_account_id},
    on_behalf_of=helper.stripe_account_id,
    metadata={"booking_id": booking.id},
    idempotency_key=f"booking:{booking.id}:payment_intent:v1",
)
```

- The idempotency key comes from the action, never from a random value per attempt (rule a).
- Don't pass `payment_method_types`: the account's API version rejects it, and payment methods are managed in the Dashboard. Tests that confirm server-side add `automatic_payment_methods={"enabled": True, "allow_redirects": "never"}`. Verified in the sandbox (BEAV-23).
- Stripe's card fee is charged to the platform (`fees.payer=application`) and isn't returned on refunds, so a full refund costs TaskBeavo the card fee. Don't try to recover it from the helper.
- The early-start consent (rule g) must be present on the booking before the PaymentIntent is created. If it's missing, return `CONSENT_REQUIRED`.
- Re-check the task location against active service areas before creating the booking (rule j). A task whose area was deactivated, or whose point is outside, returns `OUT_OF_SERVICE_AREA` and nothing is charged.
- The mutation returns the booking in `AWAITING_PAYMENT` plus the client secret. The clients show "Confirming payment…" until the subscription reports `FUNDS_HELD`.

### 4. Webhook (`POST /webhooks/stripe`)

1. Verify the signature with `STRIPE_WEBHOOK_SECRET`; reject with 400 if it fails.
2. `INSERT INTO stripe_events (id, type, payload) ... ON CONFLICT (id) DO NOTHING`. If nothing was inserted, return 200, because the event was already handled.
3. Dispatch by type and apply the transition through the state machine. If the booking is already past the target state (an out-of-order event), record it and do nothing.
4. Fiscal side effects follow confirmed state only (the fee receipt itself is written in the release transaction, a working assumption: BEAV-30):
   - `payment_intent.succeeded` → FUNDS_HELD; the fee's fiscal receipt is issued at release for now (rule f; PLAN.md 4e, BEAV-128)
   - `charge.refunded` → refund state, plus a storno receipt if the fee already has a receipt (partial-refund form open: BEAV-30, BEAV-131)
5. Return 2xx quickly. Heavy work goes to Celery, keyed by the event ID.

Handle at minimum: `payment_intent.succeeded`, `payment_intent.payment_failed`, `payment_intent.requires_action` (restarts the checkout's 30 minutes), `charge.refunded`, `transfer.reversed` (confirms a settlement with no poster refund, R = 0; `bookings/settlement.py`), `charge.dispute.created`/`closed`, `payout.paid`/`payout.failed` (connected account), and `account.updated`.

### 5. Release and partial release (rule b)

- "Release payment" moves the booking to RELEASED and creates the payout on the helper's account, with manual payouts:
  ```python
  stripe.Payout.create(amount=..., currency="eur",
      stripe_account=helper.stripe_account_id,
      idempotency_key=f"booking:{booking.id}:release:v1")
  ```
- Partial release (agreed lower amount): refund the difference to the poster with `refund_application_fee` scaled proportionally and `reverse_transfer=True`, then pay out the rest. Each step has its own key (`...:partial_refund:v1`, `...:release:v1`).

### 6. Cancellations (rule g)

`backend/bookings/cancellation.py` computes outcomes as pure functions. Use `Decimal` and integer cents.

| Case | Poster refund | Helper gets | TaskBeavo keeps |
|---|---|---|---|
| Before acceptance | nothing charged | 0 | 0 |
| Within 1 h of accepting, or before the late window | full price + fee | 0 | 0 (no fee receipt yet under the at-release working assumption, BEAV-30; a storno only if one was issued) |
| Late cancel by poster | the rest | 25% of price, min €5, max €20 | 12% of the helper compensation (fiscal receipt for that amount; if a receipt for the full fee was already issued, the storno form is open: BEAV-30, BEAV-131) |
| Late cancel, trader helper inside the 14-day withdrawal period | full price + fee | 0 | 0 |
| No access, call-out accepted or no reply in 48 h | the rest | 25% of price, min €5, max €15 | 12% of the call-out |
| No access, poster reschedules | nothing | full price later, on release | the fee, on release |
| Helper cancels or doesn't show | full price + fee | 0 | 0; TaskBeavo funds a €5 credit if under 24 h |
| No fault | full price + fee | 0 | 0 |

Implement the refund with `stripe.Refund.create(payment_intent=..., amount=..., reverse_transfer=True, refund_application_fee=..., idempotency_key=f"booking:{id}:cancel:{case}:v1")`.

The "late" window (hours before task start) is a setting, `LATE_CANCEL_WINDOW_HOURS`. Don't hard-code it.

### 7. Refunds after release

The money is already with the helper. Create a transfer reversal only if the policy case allows it; otherwise route the case to manual support. Never refund from TaskBeavo's own balance (rule b).

### 8. Tests

```bash
cd backend && uv run pytest bookings payments -q
```

- Each allowed transition, plus at least one forbidden transition per state.
- Concurrency: two parallel "Release payment" calls produce exactly one payout. Use a `transaction=True` test with threads.
- Webhook: the same fixture replayed twice applies once; an out-of-order event is ignored; a bad signature returns 400.
- Fixtures: recorded Stripe test-mode events in `backend/payments/tests/fixtures/*.json`. Mock the Stripe client at the boundary, and assert `on_behalf_of`, `transfer_data`, `application_fee_amount` and the idempotency key.
- `cancellation.py`: table-driven tests for every row above, including the €5 and €20 bounds and rounding.
- Optional end-to-end check against Stripe test mode: run `docker compose up django stripe-cli`, then `stripe trigger payment_intent.succeeded`. **Never use live keys.**

### 9. Compliance

Run the `compliance-check` skill on the diff, and fix every FAIL before asking for review.

## Checklist

- [ ] Transition added to `state_machine.py` with explicit source states
- [ ] No client mutation sets a money-confirmed state; FUNDS_HELD only from the webhook (rule a)
- [ ] Every Stripe call has a deterministic idempotency key derived from the action (rule a)
- [ ] Webhook verifies the signature and inserts into `stripe_events` ON CONFLICT DO NOTHING first (rule a)
- [ ] Destination charge with `on_behalf_of` the helper's account, 12% fee in integer cents (rule b)
- [ ] Manual payout only on "Release payment"; no money through TaskBeavo's account (rule b)
- [ ] `atomic` + `select_for_update` + `booking_events` + `outbox` (with traceparent) on every transition (rule c)
- [ ] Cancellation amounts come only from `cancellation.py`, matching the policy table (rule g)
- [ ] Early-start consent required before the PaymentIntent (rule g)
- [ ] Service area re-checked before the booking is created (rule j)
- [ ] Fiscal receipt written in the release transaction (working assumption, BEAV-30), and a storno for a refunded fee that already has one, in the form BEAV-30/BEAV-131 settles (rule f)
- [ ] Tests: transitions, concurrency, webhook replay, fixtures, cancellation table
- [ ] `compliance-check` run, with no FAIL

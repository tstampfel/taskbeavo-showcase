# Demo walkthrough

A local demo of TaskBeavo with fake Dubrovnik data. It runs without Stripe keys or any other account: payments go through a demo stand-in for Stripe (see [Demo payments](#demo-payments-a-demo-stub)). Everything here is fake: `@demo.example.com` addresses, "Demo ulica" streets, OIBs that start with eight zeros (`00000000…`, with a valid check digit), IBANs whose account number is zeros and a counter (with valid check digits), and no phone numbers.

Some steps have no screen yet. They are marked **GraphiQL** (you run a GraphQL mutation by hand) or **seeded** (the seed puts a booking in that state for you). The screens for them are still to come; see [Keeping the demo current](#keeping-the-demo-current).

## Start it

Requirements: Docker, Node.js 22 with pnpm, uv.

```bash
pnpm install
cd backend && uv sync && cp .env.example .env && cd ..
echo "DEMO_PAYMENTS=1" >> backend/.env

docker compose up -d                  # PostGIS, Redis, Django on :8000, Celery
docker compose exec django uv run python manage.py seed_demo --reset

NEXT_PUBLIC_DEMO_PAYMENTS=1 pnpm --filter @taskbeavo/web dev   # web on :3000
EXPO_PUBLIC_DEMO_PAYMENTS=1 pnpm --filter @taskbeavo/mobile start   # mobile, in a development build
```

For the phone, build the iOS development build with `pnpm --filter @taskbeavo/mobile ios`. If you build the simulator app with `xcodebuild` yourself, keep code signing on (ad hoc, `CODE_SIGN_IDENTITY=-`, is enough): with `CODE_SIGNING_ALLOWED=NO` the keychain entitlement is missing, sign-in can't store its session, and the app says it can't reach TaskBeavo.

`seed_demo --reset` prints the demo users and the link to each seeded task and booking. Its times are relative to the moment you run it (a job starting in 2 hours, one in 20 hours, one in 3 days), so **run it again whenever the demo feels stale**, for example the next day. `--reset` retires the previous demo users and seeds a fresh set; nothing is deleted (see [What --reset does](#what---reset-does)). For a completely clean slate, `docker compose down -v`, then start again.

Sign in at `http://localhost:3000/en/sign-in` with one of the demo emails. The one-time code is in the Django log, because local email goes to the console:

```bash
docker compose logs django | grep Subject | tail -1
```

| Email | Role |
|---|---|
| `klaus.beispiel@demo.example.com` | Poster in Germany, booking for his mother (German) |
| `claire.exemple@demo.example.com` | Poster in Switzerland, booking for her father (French) |
| `mara.primjer@demo.example.com` | Poster in Dubrovnik (Croatian) |
| `ivo.primjeric@demo.example.com` | Private helper, a pensioner |
| `petra.uzorak@demo.example.com` | Private helper, a student |
| `luka.testic@demo.example.com` | Registered business (obrt): business name and OIB on offers |

A private window per user saves signing in again and again. Sign-in is rate-limited, as it is in production: 5 codes per address and 20 per computer per hour. If the sign-in page stops offering a code, wait, or clear the sign-in counters in your local Redis (this leaves the Celery queue alone):

```bash
docker compose exec redis sh -c "redis-cli --scan --pattern 'taskbeavo:1:email-code-*' | xargs -r redis-cli del; redis-cli --scan --pattern 'taskbeavo:1:gql-rl:*' | xargs -r redis-cli del"
```

## The walkthrough

Screenshots are from the web app at phone width, and for checkout, the booking, the day of the job and cancelling also from the iOS app, taken from a freshly seeded demo.

### 1. Post a task (GraphiQL)

The post-task wizard isn't built yet (BEAV-86, BEAV-87); the API is. Open GraphiQL at `http://localhost:8000/graphql` and sign in as Mara:

```graphql
mutation { requestEmailCode(email: "mara.primjer@demo.example.com", locale: EN) }
```

```graphql
mutation {
  verifyEmailCode(email: "mara.primjer@demo.example.com", code: "123456", device: { platform: WEB }) {
    ... on AuthSession { accessToken }
  }
}
```

Put the token in GraphiQL's Headers tab as `{"Authorization": "Bearer <accessToken>"}`, then post the task. Pick a start a day or two ahead:

```graphql
mutation {
  postTask(input: {
    category: GARDEN
    title: "Water the tomatoes on the balcony"
    description: "Twelve pots on a balcony in Gruz; the watering can is by the door."
    location: { address: "Demo ulica 2, 20000 Dubrovnik", lat: 42.661, lng: 18.085 }
    windowStart: "2026-11-11T09:00:00Z"
    windowEnd: "2026-11-11T11:00:00Z"
    budgetCents: 2000
  }) { task { id status } warnings }
}
```

A point outside the Dubrovnik service area is refused with `OUT_OF_SERVICE_AREA`.

### 2. Send an offer (GraphiQL)

The task feed and task detail aren't built yet (BEAV-88 to BEAV-91). Sign in as Petra the same way, swap the token, and offer a price. TaskBeavo never suggests one.

```graphql
mutation {
  sendOffer(input: { taskId: "<task id>", priceCents: 1800, message: "I can come after my lectures." }) {
    id status priceCents
  }
}
```

### 3. Compare offers and accept one

Mara now sees Petra's offer at `/en/tasks/<task id>/offers`. The seeded open task (Klaus's, link printed by the seed) already has two: one from a private helper and one from a registered business, which shows its business name, OIB and address as a trader must. Offers are sorted newest first or by price, never by anything that steers work to particular helpers.

<img src="web/01-offers.png" width="260" alt="Two offers on Klaus's task, one from a registered business with its name, OIB and address">

### 4. Pay with the demo card

Accept an offer. Checkout shows the price, the 12% service fee and the two consent boxes the cancellation policy asks for (the seed publishes draft texts, marked as such). After "Book and pay", the demo card form stands in for Stripe's card form: it says plainly that it's a demo payment with no real money, and offers a card that pays and one that is declined.

<img src="web/02-checkout.png" width="260" alt="Checkout with the price and the service fee"> <img src="web/02b-checkout-consent.png" width="260" alt="The consent boxes ticked and the Book and pay button"> <img src="web/03-demo-card.png" width="260" alt="The demo card form: Demo payment, no real money">

"Confirming payment…" stays until the server says the money is held, which happens only when the payment_intent.succeeded event has gone through the webhook handler. The booking page then shows the exact address and access note to the poster and the accepted helper only.

<img src="web/04-funds-held.png" width="260" alt="Payment held, the helper has been notified"> <img src="web/05-booking.png" width="260" alt="Booking page with the payment held">

The same flow on the iOS app (a development build in the simulator, started with `EXPO_PUBLIC_DEMO_PAYMENTS=1`): the offers, checkout, the demo card dialog and the payment held. The booking itself, the day of the job and cancelling follow in steps 5 and 7.

<img src="mobile/01-offers.png" width="200" alt="Offers on the phone"> <img src="mobile/02-checkout.png" width="200" alt="Checkout on the phone"> <img src="mobile/02b-checkout-consent.png" width="200" alt="The consent boxes ticked on the phone">

<img src="mobile/03-demo-card.png" width="200" alt="The demo card dialog: Demo payment, no real money"> <img src="mobile/04-funds-held.png" width="200" alt="Payment held on the phone">

### 5. On my way, arrived

Sign in as Ivo and open the seeded **on-my-way** booking's "Day of the job" page (`/bookings/<id>/arrival`). Its job starts 2 hours after the seed, inside the 6 hours before the start when these buttons open. Pick an arrival time and tap **On my way**; Mara sees it live.

<img src="web/06-helper-day-of-job.png" width="260" alt="The helper's day-of-the-job panel"> <img src="web/07-helper-on-my-way.png" width="260" alt="The helper is on the way"> <img src="web/08-poster-sees-on-the-way.png" width="260" alt="The poster sees the helper is on the way">

On the iOS app, Ivo opens the booking, then "Day of the job", picks an arrival time and taps **On my way**:

<img src="mobile/05-booking.png" width="200" alt="The booking on the phone: the exact address, shown to the helper only while booked"> <img src="mobile/06-day-of-job.png" width="200" alt="The day of the job on the phone, with the arrival times"> <img src="mobile/07-on-my-way.png" width="200" alt="The helper is on the way, on the phone">

### 6. Request and release payment (seeded)

The **Request payment** and **Release payment** buttons arrive with BEAV-116 to BEAV-118. Until then the seed moves two bookings there through the booking state machine, as the helper and then the poster: **payment-requested** (Klaus, sign in as him) and **released** (Mara). The "Release payment" button on the first leads to a placeholder page.

<img src="web/09-payment-requested.png" width="260" alt="A booking with payment requested"> <img src="web/10-released.png" width="260" alt="A booking with the payment released">

### 7. Cancel

Open "Cancel or withdraw" on a held booking. Cancelling takes two steps: pick a reason, then "See refund" shows what happens to the money, with the final button and Back. A no-fault reason (weather, hospital, an emergency) asks the helper to agree on that second step instead, and a registered business's withdrawal button stays on the reason step. The seeded **late-cancel** booking (Klaus) starts under 24 hours away, so the helper keeps 25% of the price (€5 minimum) and TaskBeavo 12% of that. The **funds-held** booking (Claire) is 3 days away: a full refund, and since its helper is a registered business, the 14-day withdrawal right as well. The **cancelled** booking was cancelled by the seed and refunded through the stand-in's charge.refunded event.

<img src="web/11a-cancel-reason.png" width="260" alt="Cancel, step 1: why are you cancelling?"> <img src="web/11-late-cancel-preview.png" width="260" alt="Step 2, late cancel: the helper keeps €5"> <img src="web/12-free-cancel-preview.png" width="260" alt="Step 2, free cancel: a full refund">

The same late cancel on the iOS app, signed in as Klaus: the reasons, then what happens to the money before he confirms.

<img src="mobile/08-cancel.png" width="200" alt="Cancel booking on the phone: why are you cancelling?"> <img src="mobile/09-late-cancel-preview.png" width="200" alt="Late cancel on the phone: the helper keeps €5">

### 8. A contested no-show

In the seeded **no-show-contested** booking, the job started 2 hours ago, Klaus reported that Petra didn't come, and Petra answered that she was there. Nothing happens automatically: the booking waits for support.

<img src="web/13-no-show-contested.png" width="260" alt="The helper says they were there; support is checking">

## Demo payments: a demo stub

**`DEMO_PAYMENTS=1` is a demo stub, not a payment integration.** It exists so the showcase runs without anyone's Stripe account. It replaces only the Stripe SDK's HTTP transport (`backend/payments/demo_stripe.py`); everything above it is the real code:

- Every Stripe call still goes through `StripeGateway` with its idempotency key derived from the action, such as `booking:{id}:payment_intent:v1`. The stand-in replays a repeated key like Stripe does.
- The stand-in reports back through webhook events, which go through the real `process_event`: inserted into `stripe_events` with `ON CONFLICT DO NOTHING`, then handled. A booking is `FUNDS_HELD` only after that, never from the confirm call.
- It keeps destination charges with `on_behalf_of`, the application fee, refunds with the fee refunded and the transfer reversed, and payouts on connected accounts with IDs that start with `pi_demo`, `acct_demo` and so on. No real Stripe ID appears anywhere.
- The demo card form posts to one extra endpoint, `/demo-stripe/v1/payment_intents/<id>/confirm`, which stands in for Stripe.js. It needs the PaymentIntent's client secret, answers only the local web origins (CORS), and is mounted only when `DEMO_PAYMENTS=1`.
- Settings refuse `DEMO_PAYMENTS=1` in production and next to a live Stripe key (`sk_live_`, `rk_live_`), and `seed_demo` refuses both again.

With a Stripe test key in `backend/.env` and `DEMO_PAYMENTS` unset, the same flows run against Stripe's test mode instead (`docker compose up stripe-cli` forwards its webhooks).

## What --reset does

`seed_demo --reset` retires the current demo users: only users on `demo.example.com` whose address is one of the six above. Each is deactivated and anonymised, its open tasks are closed through the task service, its sessions are revoked, its on-site contacts deleted, and a helper's OIB and IBAN cleared, so the fresh set reuses the same demo OIBs. Its bookings, booking events, checkout consent records and terms acceptances stay. They are legal evidence, append-only by design (the consent table refuses `DELETE` in the database), and the reset never works around that. Retired rows pile up harmlessly in a local database; `docker compose down -v` clears everything. The demo payout accounts exist only in the stand-in, so nothing is cleaned in any Stripe account. A fresh set of users, tasks and bookings is then seeded relative to now.

## Keeping the demo current

When a ticket builds a screen this walkthrough marks as GraphiQL or seeded, it updates this page and its screenshots in the same pull request, and moves the step to the real screen:

- the post-task wizard (BEAV-86, BEAV-87) replaces step 1;
- the task feed and task detail (BEAV-88 to BEAV-91) replace step 2;
- request and release payment (BEAV-116 to BEAV-118) replace step 6, and the seed then requests and releases through those services instead of the state machine (the `TODO(BEAV-116)` and `TODO(BEAV-117)` in `backend/bookings/demo_seed.py`).

Screenshots: sign in through the real form, from a fresh `seed_demo --reset`, with fake demo data only. Every image in a set has the same size, so the rows on this page and in the root README line up:

- **Web** (`web/`): one browser viewport of 400 × 860 CSS pixels at 2× scale, so every file is **800 × 1720**. No full-page captures. Hide the Next.js dev indicator and dismiss the cookie banner first.
- **Mobile** (`mobile/`): one simulator screen from an iPhone 17 Pro (or 16 Pro) at half scale, so every file is **603 × 1311**, with the status bar set to 09:41, full signal and a full battery (`xcrun simctl status_bar <udid> override --time 09:41 …`). No dev menu or dev tools button in the shot.
- Where one screen can't show a step, take two images (scroll and capture again), as checkout does with `02-checkout.png` and `02b-checkout-consent.png`. Never stitch or crop.
- Lay images out in rows of one set only: web images at `width="260"` (at most 3 per row), mobile images at `width="200"` (at most 3 per row). The root README shows one row of each, 4 images at `width="180"`. Never mix web and mobile in one row, because their aspect ratios differ.

Check every image for personal data (browser chrome, bookmarks, avatars, local paths, usernames, notifications) and metadata before committing it; the files should hold only the `IHDR`, `IDAT` and `IEND` PNG chunks. `python3 scripts/check_demo_screenshots.py` checks the sizes and the chunks (its tests: `python3 scripts/test_check_demo_screenshots.py`).

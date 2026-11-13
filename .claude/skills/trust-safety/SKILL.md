---
name: trust-safety
description: Build or change TaskBeavo moderation and safety, covering category rules (no rides, medical or personal care), the contact-detail filter on tasks, questions, chat and reviews, report and block, the safety incident flow with a Call 112 button and booking pause, human-decided restrictions with a statement of reasons and one appeal (DSA), "On my way" and helper no-show handling, and the no-access flow with the poster as the bridge, a 15-minute wait, an in-app photo with GPS and the 25% call-out. Use this whenever someone works on moderation, filters, reports, blocks, suspensions, appeals, incidents, late or missing helpers, "couldn't do the job", or asks "how do we stop off-platform deals" or "what if something happens to Mama", even if safety isn't named.
---

# Trust and safety

Helpers meet elderly people alone, and posters are often hundreds of kilometres away. The rules here protect people first, then keep deals on the platform, and they must be fair to helpers too: under the DSA and the Platform Work Directive, **a person decides every restriction**, with written reasons and one appeal. Ratings and algorithms never restrict anyone automatically.

**Plan:** `docs/PLAN.md`, sections "Posting a task and handling offers", "Reviews", "Reporting, blocking, suspension and appeal", "Safety incidents on a job", "Helper no-show and late arrival", "\"Couldn't do the job\": how Airtasker handles it and the TaskBeavo flow", "\"Drive Mum to the doctor\" is a taxi licence problem" and "Company, DSA and GDPR basics". Read the matching one first.

Money outcomes (refunds, call-out, credit) are computed only in `bookings/cancellation.py` and applied through `booking-payments`. This skill decides **when**; that one decides **how much**.

## 1. Category rules

- Categories are a server-side list (`tasks/categories.py`). Excluded: rides or driving people, medical care, personal care (bathing, dressing, medication), anything illegal. Errands where the helper drives alone, and "accompaniment" on a bus or licensed taxi, are allowed.
- `postTask` and task edits reject excluded categories with `CATEGORY_NOT_ALLOWED`, and a keyword classifier on title and description holds suspicious tasks (`"drive my mother to"`, `"give her insulin"`) for review instead of publishing them. Every block returns a translated statement of reasons.

## 2. Contact-detail filter

Applies to task text and photos' captions, public questions and answers, chat **before** acceptance, and reviews.
- Detect phone numbers (including spaced and spelled digits, `+385`, `0 9 1`), emails (including `at`/`dot` obfuscation), URLs and messenger handles (WhatsApp, Viber, Telegram, Instagram). One module, `moderation/contact_filter.py`, used by every write path.
- Clear matches are masked (`•••`) and the author is told why; doubtful ones go to the review queue. After acceptance, chat between the booked poster and helper is not masked (they need to coordinate), but phone numbers are still never exposed through profiles or the API (rule e).
- Table-driven tests with Croatian, German and French examples, including false positives (prices, dates, house numbers).

## 3. Report and block

- `report(targetType, targetId, reason, details)` on profiles, tasks, offers, chat messages and reviews; plus a public web form for non-users (DSA notice-and-action) that sends an acknowledgement.
- `REPORT(target_type, target_id, reason, decision, statement_of_reasons, appealed)` rows feed a staff queue in the admin with the evidence (chat, photos, `booking_events`).
- `block(userId)` writes `BLOCK(blocker, blocked)`. Blocked pairs don't see each other's tasks, offers or profiles, and can't create new offers or bookings together. Open bookings continue unless staff decide otherwise.

## 4. Restrictions and appeal

- Only staff can apply a warning, content removal or account restriction, through an admin action that **requires** a statement of reasons. The user gets it by email and in-app, in their language.
- The user can appeal once; a **different** staff member decides (enforce reviewer ≠ original decider). Outcome: upheld, reduced or reversed, with reasons.
- During a restriction, open bookings are finished or refunded, and earned payouts are still paid unless fraud is suspected. A restricted user is in limited mode (`auth-and-accounts`).

## 5. Safety incident

- A large **Call 112** button (`tel:112`) on every active booking screen, above "Report an incident". The app states that TaskBeavo is not an emergency service.
- `reportIncident(bookingId, type, description, photos)` creates an `INCIDENT` and, in the same transaction, sets `booking.paused = true`: no release, payout, call-out or cancellation fee while it's open. The poster is notified immediately; staff get an urgent alert to contact both sides the same day.
- Incident notes can contain health details: store them in `restricted_notes` (encrypted, staff-only, own retention period), never in Sentry, Honeycomb or logs.
- Outcomes: insurance claim, restriction (section 4), booking settled (release, refund or split), authorities informed where the law requires.

## 6. "On my way" and helper no-show

- Helper buttons "On my way" (optional ETA) and "I've arrived" write `booking_events`. Reminder pushes 2 h and 30 min before the slot.
- A Celery Beat job: 15 min after start with neither event → push and chat "Are you on your way?". Reply "Running late" with ETA → the poster can accept or cancel no-fault. "Can't come", or no reply within 45 min and the poster confirms "No, he didn't come" → helper cancellation (full refund, €5 credit if under 24 h, reliability score drops) and a one-tap re-open of the task.
- Two helper no-shows in 30 days → a human review item, not an automatic restriction.

## 7. No access ("Couldn't do the job?")

1. The helper picks a reason. An automatic chat message and push go to the **poster**, who calls the on-site contact from their own phone. TaskBeavo makes no calls.
2. A **15-minute wait timer** starts (server-side timestamp). "Request call-out pay" stays disabled until it ends, unless the poster replies that nobody is home.
3. Evidence: a photo taken **in the app** (camera only, not the gallery) with timestamp and GPS. GPS more than ~100 m from the task point routes the request to manual review.
4. The poster has 48 h to choose: reschedule (no fee), accept call-out, or dispute. No reply in 48 h accepts the call-out.
5. Call-out = 25% of the price, min €5, max €15, from `cancellation.py`; TaskBeavo keeps 12% of it.
6. "Task not as described" steers both sides to a price-increase request first.
7. Abuse checks: more than two accepted call-outs in 30 days, or a high ratio to completed tasks, creates a human review item.

All evidence (photo key, GPS distance, chat, push delivery times, timer) is stored as `booking_events` for disputes and chargebacks.

## Tests

```bash
cd backend && uv run pytest moderation safety bookings -q
```

- Excluded categories rejected; contact filter table (hr, de, fr, false positives).
- Block hides both directions and prevents new offers and bookings.
- A restriction can't be saved without a statement of reasons; an appeal can't be decided by the original decider.
- An open incident blocks release, payout and cancellation fees.
- No-show timeline with a frozen clock: 15 min, 45 min, poster confirmation.
- Call-out can't be requested before 15 min; a gallery photo is refused; GPS beyond 100 m goes to review; 48 h silence accepts.

## Checklist

- [ ] Category list enforced server-side; doubtful tasks held for review with reasons
- [ ] One contact-detail filter used by tasks, questions, pre-acceptance chat and reviews
- [ ] Report on every profile, task, offer, message and review; public DSA form with acknowledgement
- [ ] Block hides both sides and prevents new deals
- [ ] Restrictions only by a person, with statement of reasons; one appeal to a different reviewer
- [ ] Call 112 button; incident pauses the booking; health details restricted and kept out of telemetry
- [ ] "On my way", reminders and no-show timeline; repeat no-shows go to human review
- [ ] No-access: poster bridge, 15-min timer, in-app GPS photo, 48 h choice, amounts from `cancellation.py`
- [ ] Evidence written to `booking_events`
- [ ] Strings through `i18n-strings`; `compliance-check` run for anything touching money or personal data

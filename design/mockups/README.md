# TaskBeavo mockups

These static HTML mockups are TaskBeavo's designs for now. Each file in `screens/` covers one of the screen prompts from the legal and tax plan and shows every state side by side: 390×844 mobile frames, 1440 desktop pages for web and admin.

## View them

```sh
python3 design/mockups/build.py
open design/mockups/dist/index.html
```

`dist/` is generated and gitignored. Pass names to rebuild only some files, for example `python3 design/mockups/build.py 06-checkout`.

Text is set in Manrope. Install it locally ([Google Fonts](https://fonts.google.com/specimen/Manrope)) or the browser falls back to a default sans-serif.

## Files

| File | Contents |
|---|---|
| `base.css` | Brand tokens and shared classes: buttons, cards, chips, badges, inputs, sheets, tab bar |
| `build.py` | Expands `{{...}}` tokens and writes `dist/` |
| `screens/*.html` | One file per prompt; each `.screen` element is one frame |

Tokens available in screen files:

| Token | Output |
|---|---|
| `{{i:name:size:color:stroke}}` | Lucide-style line icon (names are in `build.py`) |
| `{{sb}}` | iOS status bar |
| `{{tab:home\|tasks\|msg\|profile}}` | Bottom tab bar with the raised green + button |
| `{{beaver:size:stand\|wave\|thumb\|cheer}}` | Beaver mascot |
| `{{stars:count:size}}` | Star rating |
| `{{admin:Section}}` | Admin sidebar with that section active |

## Rules the mockups follow

Checked against the hard rules in `CLAUDE.md` and the built app (BEAV-208). Where a built screen made a rule-driven choice, the mockup follows the app.

- **Payment copy:** says "held via Stripe" and never "held by TaskBeavo" or "escrow".
- **No ranking that steers work (rule k):** offers sort only by **Newest** (the default) or **Lowest price**, as built. "How offers are ranked" opens the text of the built `/ranking` page: open offers first, newest first or lowest price, nothing else changes the order, no paid placement. There is no "Recommended" or "Top rated" sort, and no poster-facing screen shows a helper's response time, completion rate, "Fast responder" or jobs-done count (support's admin view and a helper's own earnings may count their tasks). Ratings with their count stay; BEAV-144 decides what other profile stats may show.
- **No suggested prices (rule k):** TaskBeavo never shows a price range or "similar tasks pay" figure: not when posting, not in the no-offers nudge, not on the landing page, and there is no price-suggestion setting. Prices a helper set for themselves ("Gardening from €15/h" on a profile) are theirs and may show.
- **Helper labels:** every offer and profile shows "Private individual" or "Registered business".
- **Terms:** no account is created until the user ticks the unticked, required Terms checkbox (email, Apple and Google sign-in alike). Helpers also accept the Helper terms during onboarding. Updated terms need re-acceptance; until then the user is in limited mode.
- **Checkout:** shows the fee line from the app (task price + service fee, 12%, min. €2.50), the cancellation summary, the withdrawal information link and two separate unticked consent boxes. `6g` is the demo payment card the showcase uses with `DEMO_PAYMENTS=1`.
- **Amounts:** from `backend/bookings/money.py` (fee 12% of the task price, at least €2.50) and `backend/bookings/cancellation.py`. A €30.00 task: €30.00 + €3.60 fee = €33.60.
  - Minimum fee: a €15.00 task pays €2.50 (€17.50), a €20.00 task €2.50.
  - Late cancellation (under 24 h): €7.50 to the helper (25%, min €5, max €20), €0.90 kept (12% of €7.50), €25.20 back.
  - No access: €7.50 call-out (25%, min €5, max €15), €0.90 kept, €25.20 back. The poster can also reschedule for free or dispute.
  - Partial release or dispute split: TaskBeavo keeps 12% of what the helper gets (€30.00 task, €20.00 to the helper: €2.40 kept, €11.20 back), even on a minimum-fee booking (€15.00 task paid €17.50, €10.00 to the helper: €1.20 kept, €6.30 back).
  - Price increase: a separate charge with its own fee (€25.00 extra + €3.00 fee = €28.00).
  - Helper cancels or doesn't show: €33.60 back, plus a €5 TaskBeavo credit if under 24 h. A no-show the poster reports waits 24 h for the helper to answer.
- **Cancellation (rule g):** a no-fault reason (weather, hospital, emergency, both agree) asks the other side to agree: the poster's request stands after 48 h with no reply, a disagreement goes to support. The other side sees "personal emergency", never the health detail. The withdrawal button appears only on bookings with a registered business, within its 14 days.
- **Privacy (rule e):** users' phone numbers and IBAN are never shown to other users. The exact address, access details and the on-site contact's phone are shown only to the chosen helper while the booking is active, and hidden again once it closes (the phone rule is `backend/care_contacts/policy.py`). The helper's booking view shows the contact with a **Call +385 …** link, as the app does (`5c`, `7b`, `13i`, `18a`, `18g`); the call goes from the helper's own phone, and TaskBeavo itself places no calls. The poster can also call the contact from their own phone. A registered business shows its business name and OIB on its profile and each offer; its business address goes only to the poster of a task it offered on, on that offer and at checkout, and its public profile shows only the town. A private helper's OIB is never shown. OIBs in the mockups are synthetic (`00000000…`).

## Not yet covered

- **German and French copy:** layouts haven't been checked with German or French text, which run 20–30% longer.
- **Large text:** screens haven't been checked at large text sizes for older users.
- **Contrast:** secondary grey text hasn't been checked against WCAG AA.
- **Masked calling:** the plan treats it as a later feature, so there are no mockups for it yet.

When a screen is built in `apps/mobile` or `apps/web`, the app becomes the source of truth. Update or delete the mockup rather than letting the two drift apart.

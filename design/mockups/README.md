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

## Rules the mockups already follow

- **Payment copy:** says "held via Stripe" and never "held by TaskBeavo" or "escrow".
- **Helper labels:** every offer and profile shows "Private individual" or "Registered business". "Recommended" sorting links to "How offers are ranked".
- **Terms:** no account is created until the user ticks the unticked, required Terms checkbox (email, Apple and Google sign-in alike). Helpers also accept the Helper terms during onboarding. Updated terms need re-acceptance; until then the user is in limited mode.
- **Checkout:** shows the cancellation summary, the withdrawal information link and the early-start consent checkbox, unticked by default.
- **Amounts:** match the cancellation table in `CLAUDE.md` (hard rule g): €30.00 task + €3.60 fee = €33.60.
  - Late cancellation: €25.20 back, €7.50 to the helper, €0.90 fee.
  - No access: €25.20 back, €7.50 call-out (25%, min €5, max €15), €0.90 fee. The poster can also reschedule for free or dispute.
  - Helper cancels: €33.60 back, plus a €5 TaskBeavo credit if under 24 h.
- **Privacy (rule e), as the plan sets it:** no phone number or IBAN is shown to other users, and there are no call buttons at launch. The exact address and access details are shown only to the chosen helper while the booking is active, and hidden again once it closes. A registered business shows its obrt OIB on its public profile; a private helper's OIB is never shown.

## Not yet covered

- **German and French copy:** layouts haven't been checked with German or French text, which run 20–30% longer.
- **Large text:** screens haven't been checked at large text sizes for older users.
- **Contrast:** secondary grey text hasn't been checked against WCAG AA.
- **Masked calling:** the plan treats it as a later feature, so there are no mockups for it yet.

When a screen is built in `apps/mobile` or `apps/web`, the app becomes the source of truth. Update or delete the mockup rather than letting the two drift apart.

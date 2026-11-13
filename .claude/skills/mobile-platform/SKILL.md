---
name: mobile-platform
description: Set up or change the native platform layer of TaskBeavo's Expo app, covering deep links and universal links / Android App Links for task, booking and "Release payment" screens, push notification setup and permission prompts, camera and location permissions with explanation screens, apollo3-cache-persist cleared on logout and account deletion, App Store privacy labels and the Google Play data safety form kept in sync with what the app collects, and Maestro end-to-end tests for sign-up, post task, accept and pay, request and release payment on iOS and Android. Use this whenever someone adds a link that should open the app, a permission, a push type, a new kind of collected data, an SDK that collects data, or a mobile e2e test, or asks "why doesn't the link open the app" or "do we need to update the privacy label", even if the native layer isn't named.
---

# Mobile platform

`expo-screen` builds screens; this skill owns what sits under them: how links and pushes open the right screen, how permissions are asked, what the app stores on the device, what the stores are told about data collection, and the end-to-end tests that prove the money flow works on real platforms. Build profiles and shipping are in `expo-release`.

**Plan:** `docs/PLAN.md`, sections "A Django monolith, Expo apps and Stripe fit a solo React developer", "GraphQL subscriptions deliver live updates, push covers the rest", "\"Couldn't do the job\": how Airtasker handles it and the TaskBeavo flow" (in-app photo with GPS), "GDPR flows" (analytics consent, no cross-app tracking) and "Build sequence for one developer".

## 1. Deep links

- Scheme `taskbeavo://` and universal links on `https://taskbeavo.com` (plus the `www` host). Expo Router maps paths directly:

  | Path | Screen |
  |---|---|
  | `/tasks/[id]` | Task detail |
  | `/bookings/[id]` | Booking detail |
  | `/bookings/[id]/release` | "Release payment" confirmation |
  | `/bookings/[id]/no-access` | Poster's reschedule / accept call-out / dispute choice |
  | `/legal/[kind]` | Legal page (`legal-terms`) |

- iOS: `ios.associatedDomains: ["applinks:taskbeavo.com"]` in `app.config.ts`, and the web app serves `apps/web/public/.well-known/apple-app-site-association` (no extension, `application/json`). Android: `intentFilters` with `autoVerify: true` and `/.well-known/assetlinks.json` with the release and upload SHA-256 fingerprints from EAS credentials.
- A link never carries money authority. `/bookings/123/release` only opens the screen; release still needs the logged-in poster's tap and the server's checks. Logged-out users go through sign-in and land back on the target.
- Emails and pushes use the same `https://` links, so they work on web too.
- Test: `npx uri-scheme open "https://taskbeavo.com/bookings/123/release" --ios` (and `--android`), plus Apple's and Google's association validators after deploying the files.

## 2. Push notifications

- `expo-notifications` with an Android channel per category (bookings and payments high importance, chat default, reminders default). Register the Expo push token after sign-in and send it with the device and locale to `registerPushToken`; delete it on logout.
- **Don't ask on first launch.** Ask at a moment of value ("Get notified when helpers send offers?" after posting a task; "Know when payment is released?" after a helper's first offer) with an explanation screen first; if denied, show a settings link later, never re-prompt in a loop.
- Tapping a push routes by its `data.route` (IDs only, see `realtime-notifications`), including when the app was killed (`getLastNotificationResponseAsync`).

## 3. Camera and location

- Each permission has a pre-prompt explanation screen in the user's language before the system dialog, and translated `infoPlist` / Android strings (`expo-release` config).
- Camera: task photos, and the in-app no-access photo, which must come from the camera (not the gallery) with timestamp and GPS (`trust-safety`). Upload to R2 with a presigned URL; strip EXIF location from task photos that other users see.
- Location: **when-in-use only**, requested at the no-access step or "On my way", never in the background. The coordinates sent are for the GPS distance check, not stored as a track.

## 4. Persisted Apollo cache

- `apollo3-cache-persist` with MMKV or AsyncStorage, a max size, and a `CACHE_SCHEMA_VERSION` that purges on upgrade when type policies change.
- **Logout and account deletion** call one `resetClientState()`: `persistor.purge()`, `client.clearStore()`, delete SecureStore tokens, unregister the push token, clear i18n's saved choice only on deletion. Test it: after logout, a fresh start shows no previous user's data.
- Active bookings still refetch on foreground and reconnect (CLAUDE.md "Frontend data").

## 5. Store privacy forms

Keep `apps/mobile/store/privacy.md` as the single source of truth: every data type collected (name, email, phone, precise location at no-access, photos, user content, purchase history, identifiers, crash data), its purpose, whether linked to the user, and which SDK collects it (Sentry, PostHog, Stripe, Expo push).
- App Store Connect privacy labels and the Google Play data safety form must match it. No cross-app tracking, so no ATT prompt; add `PrivacyInfo.xcprivacy` required-reason entries through `expo-build-properties` or the config plugin.
- **When a change collects a new data type, adds an SDK, or changes a purpose:** update `privacy.md` in the same PR, and add "Update App Store privacy label and Play data safety form" to the PR description. `expo-release` checks it before every production submit. The privacy notice (`legal-terms`) may need the same change.

## 6. Maestro end-to-end tests

Flows live in `apps/mobile/.maestro/` and run on the iOS simulator and Android emulator against a development or preview build pointing at a seeded test backend with Stripe test mode:

| Flow | Covers |
|---|---|
| `signup.yaml` | Email code (test inbox or fixed test code in non-production), account details with 18+ and terms, phone verification (test number) |
| `post-task.yaml` | Category, address inside Dubrovnik, an out-of-area address rejected, on-site contact, date, budget |
| `accept-and-pay.yaml` | Accept an offer, both checkout checkboxes, Stripe test card `4242…` and a 3DS card, "Confirming payment…" until FUNDS_HELD |
| `request-and-release.yaml` | Helper "Request payment", poster "Release payment", status RELEASED |

```bash
cd apps/mobile && maestro test .maestro/ --platform ios      # and --platform android
```

Use `testID`s (`expo-screen`), never translated text, for selectors, and run each flow in `hr` and `de` at least. Add a flow when a new money or safety path ships. In CI, run them through EAS Workflows or Maestro Cloud on the preview build.

## 7. Build profiles

Native changes here (associated domains, intent filters, permissions, notification config) need a new build. Use the `development`, `preview` and `production` profiles defined in `expo-release`; don't create new ones here.

## Checklist

- [ ] Task, booking, release and no-access screens open from `https://` links on iOS and Android; AASA and assetlinks served and validated
- [ ] Links open screens only; money actions still need the user's tap and server checks
- [ ] Push token registered after sign-in, removed on logout; permission asked in context with an explanation screen
- [ ] Camera and when-in-use location with explanation screens; no-access photo from the camera with GPS; EXIF stripped from public photos
- [ ] Persisted cache purged on logout and account deletion; schema-version purge
- [ ] `store/privacy.md` updated with any new data or SDK; store forms flagged for update
- [ ] Maestro flows for sign-up, post task, accept and pay, request and release pass on iOS and Android
- [ ] New native config shipped as a new build via `expo-release` profiles

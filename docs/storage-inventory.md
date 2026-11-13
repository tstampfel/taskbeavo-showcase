# Storage inventory: cookies, device storage and push tokens

Everything TaskBeavo stores on a visitor's browser or phone, on web, iOS and Android. It is the source for the `/cookies` page the lawyer writes (BEAV-39) and for the App Store privacy label and Google Play data safety form (`apps/mobile/store/privacy.md`). Keep it current: a change that adds, renames or changes the lifetime of an item updates this file in the same PR.

TaskBeavo launches without a consent banner (pre-launch legal opinion, 2026-11-05; BEAV-190). That only holds while **every item below is strictly necessary** for a service the user asked for (ZEK čl. 43(4), the Croatian transposition of ePrivacy Art. 5(3); AZOP guidance; EDPB Guidelines 2/2023). Nothing here is used for analytics, advertising or profiling. Anything client-side that isn't strictly necessary (PostHog included, cookieless or not) needs opt-in consent with AZOP's equal "Accept all" / "Reject all" banner first.

Last checked: 2026-11-07, against `develop` plus BEAV-190.

**NEEDS REVIEW** marks an item whose "strictly necessary" status is the lawyer's call (BEAV-39). The features stay until the lawyer decides; the answer decides whether the item is fine without consent, needs consent, or goes.

"First party" means stored for and read by TaskBeavo; "third party" means stored by another company's code (Stripe, Google, Apple), even when it sits on our domain.

## Web (taskbeavo.com)

### Cookies

| Name | Purpose | Duration | Party | Set when | Source |
|---|---|---|---|---|---|
| `tb_access` | The signed-in session: the access token the `/api` route handlers send to the API. httpOnly, Secure, SameSite=Lax; page scripts can't read it | Until the access token expires, 30 s early (`ACCESS_TOKEN_TTL_SECONDS`, 15 minutes) | First | Sign-in and every token refresh; deleted at sign-out | `apps/web/src/auth/session.ts` |
| `tb_refresh` | Keeps the user signed in: the refresh token that gets a new access token. httpOnly, Secure, SameSite=Lax | Until the refresh token expires (`REFRESH_TOKEN_TTL_DAYS`, 30 days after its last use) | First | Sign-in and every refresh; deleted at sign-out | `apps/web/src/auth/session.ts` |
| `tb_device` | Security: tells the API this browser signed in before, so the next sign-in isn't reported as a new device in the new-sign-in email. It is no credential. httpOnly, Secure, SameSite=Lax | 183 days, renewed at each sign-in; the API forgets a token unused that long (`DEVICE_TOKEN_MAX_AGE_DAYS`). Kept after sign-out | First | Sign-in | `apps/web/src/auth/session.ts` (decision 2026-11-04, WP194 user-centric security) |
| `NEXT_LOCALE` | Remembers the language the user picked, so `/` opens it. SameSite=Lax | 1 year | First | **Only an active pick**: the language switcher, or the account's saved language at sign-in. Never by the browser-language redirect or by visiting a page in a language (BEAV-190) | `apps/web/src/i18n/routing.ts`, `apps/web/src/proxy.ts`, `apps/web/src/i18n/locale-cookie.ts` |
| `__stripe_mid` | Stripe fraud prevention (device identifier for the payment) | 1 year (Stripe's cookie policy) | Third (Stripe, set on our domain by Stripe.js) | Only on the checkout pages, where Stripe.js loads | Stripe.js; loaded only by `apps/web/src/components/checkout/booking-checkout.tsx` |
| `__stripe_sid` | Stripe fraud prevention (session) | 30 minutes (Stripe's cookie policy) | Third (Stripe, on our domain) | Only on the checkout pages | as above |
| `m` on `m.stripe.com` | Stripe's fraud signals (`advancedFraudSignals`, on): a device identifier set by `POST m.stripe.com/6`. HttpOnly, Secure, SameSite=None | Stripe asks for 2 years; Chrome keeps it 400 days (QA 2026-11-07) | Third (Stripe) | Only on the checkout pages | as above |
| sessionStorage `_ab`, `_mf`, `1`, `id` in Stripe's `m.stripe.network` frame | Stripe's fraud signals inside its own frame | Until the tab closes | Third (Stripe) | Only on the checkout pages | as above |
| `__cf_bm` on `.hcaptcha.com` | Cloudflare's bot management for hCaptcha (Intuition Machines), which Stripe.js loads invisibly inside the Payment Element (`hcaptcha-invisible` frame, `api.hcaptcha.com/checksiteconfig`) to tell people from card-testing bots. HttpOnly, Secure, SameSite=None | 30 minutes | Third (hCaptcha / Cloudflare, loaded by Stripe) | Only on the checkout pages. **NEEDS REVIEW** (BEAV-39): strictly necessary as part of the payment's fraud protection? | Stripe.js; TaskBeavo doesn't configure it |
| Google's cookies on `accounts.google.com` | "Sign in with Google" | Google's | Third (Google) | Only on the sign-in page, when the user uses the Google button. The button is rendered without One Tap, so Google's `g_state` cookie isn't set on our domain | `apps/web/src/auth/providers.ts` |
| Apple's cookies on `appleid.apple.com` | "Sign in with Apple" | Apple's | Third (Apple) | Only on the sign-in page, when the user uses the Apple button | `apps/web/src/auth/providers.ts` |

Stripe.js loads **only on the checkout pages** (`/[locale]/bookings/[id]/checkout`, `/[locale]/tasks/[id]/offers/[offerId]/checkout`). An ESLint rule (`STRIPE_JS_MODULES` in `apps/web/eslint.config.js`) and `apps/web/src/stripe-imports.test.ts` fail if any other module imports it. Stripe Link is hidden in the client (`wallets: { link: 'never' }`). Link is turned off at the source in the Stripe Dashboard's payment method configurations (user decision 2026-11-07, BEAV-190 review R1-2; sandbox now, live account before launch, see `docs/deploy-render.md`). Stripe refuses `link` in `excluded_payment_method_types`, so code can't do it. `create_destination_charge` (`backend/payments/stripe_client.py`) sends Sentry a warning with only the booking and PaymentIntent IDs if a PaymentIntent still lists `link`, and checkout goes on. Two configurations matter, and Link must be off in both: a charge with `on_behalf_of` the helper (every TaskBeavo checkout) is governed by the **Connect configuration for connected accounts** (Dashboard: Connect > Payment methods; sandbox parent configuration, applied to each connected account as its own child configuration), not by the platform's own default configuration (in the sandbox too), which governs only charges without `on_behalf_of`. Verified in the sandbox on 2026-11-07 after Link was set to Blocked (Connect) and Disabled (platform): a destination-charge PaymentIntent with `on_behalf_of` the test connected account listed `bancontact, card, eps, ideal, klarna, mb_way, pay_by_bank, revolut_pay`, no `link`, under the connected account's child of the Connect configuration; a platform PaymentIntent listed no `link` either. Both were cancelled. Stripe.js still calls `merchant-ui-api.stripe.com/link/get-cookie` to ask whether the browser already has a Link session; QA saw no cookie set by it (2026-11-07). Recheck whether turning Link off in the Dashboard's payment method configuration stops that call. The Stripe values above come from Stripe's published cookie policy; check them in a browser before the lawyer quotes durations.

### Local storage and session storage

| Key | Store | Purpose | Duration | Party | Source |
|---|---|---|---|---|---|
| `taskbeavo.pickedLocale` | localStorage | Remembers that the user picked a language with the switcher (a language code only, `hr`/`en`/`de`/`fr`), so sign-in asks before replacing it with the account's language | Until the user clears site data; removed when sign-in applies the account language | First | `apps/web/src/i18n/picked-locale.ts` |
| `taskbeavo.termsUpdateShownTo` | sessionStorage | Opens "We've updated our terms" only once per tab, account and version (`<account id>:<version>`) | Until the tab closes | First | `apps/web/src/components/terms-update/shown.ts` |

The checkout's PaymentIntent client secret is handed between pages in memory only, never in storage (`secret-handoff.ts`).

### Not stored on web

- **Sentry** (`@sentry/nextjs`, EU): no cookies, no local or session storage. Session Replay is off (`replaysSessionSampleRate: 0`, no replay integration), so its `sentryReplaySession` storage key is never written. No user is set and `sendDefaultPii` is false (`apps/web/src/sentry/options.ts`). Checked 2026-11-07: **no persistent device identifier**.
- **Honeycomb**: the browser sends no telemetry of its own; the Apollo link adds a W3C `traceparent` header per request, generated in memory. **Nothing stored, no device identifier.**
- Fonts: next/font serves Manrope from our own domain. The Stripe card form loads Manrope from Google Fonts inside Stripe's iframe (a request, no storage by us).
- No analytics (no Vercel Analytics, no PostHog), no ads, no tag manager.

## Mobile app (iOS and Android)

`expo-secure-store` writes to the iOS Keychain and to Keystore-encrypted SharedPreferences on Android. `@react-native-async-storage/async-storage` writes to the app's private storage (an SQLite file on Android, files on iOS). Uninstalling the app deletes the AsyncStorage data; on iOS, Keychain items can survive a reinstall.

| Key | Store | Purpose | Duration | Party | Platform | Source |
|---|---|---|---|---|---|---|
| `taskbeavo.session` | SecureStore, `WHEN_UNLOCKED_THIS_DEVICE_ONLY` | The signed-in session: access and refresh tokens and their expiry | Until sign-out, account deletion or refresh-token expiry (30 days after last use) | First | iOS, Android | `apps/mobile/src/auth/token-store.ts` |
| `taskbeavo.device` | SecureStore, `WHEN_UNLOCKED_THIS_DEVICE_ONLY` | Security: the API's device token, so the next sign-in from this phone isn't reported as a new device. No credential; kept after sign-out, never synced or restored to another device | Until the app is removed; the API forgets a token unused for 183 days | First | iOS, Android | `apps/mobile/src/auth/token-store.ts` |
| `taskbeavo.pushToken` | SecureStore | This phone's Expo push token as registered with the API, so sign-out can unregister it | Until sign-out or account deletion | First (the token itself is issued by Expo / APNs / FCM) | iOS, Android | `apps/mobile/src/push/token-store.ts`. The app doesn't ask for push permission or fetch a token yet; the key appears once push registration ships |
| `taskbeavo.locale` | SecureStore, `AFTER_FIRST_UNLOCK` | The app's language | Until changed; deleted at account deletion | First | iOS, Android | `apps/mobile/src/i18n/locale-store.ts` |
| `taskbeavo.localePicked` | SecureStore, `AFTER_FIRST_UNLOCK` | That the user picked the language in the app (so sign-in asks before applying the account language) | Until sign-in applies the account language, or account deletion | First | iOS, Android | `apps/mobile/src/i18n/locale-store.ts` |
| `taskbeavo.apolloCache` | AsyncStorage | The offline copy of what the app last loaded (tasks, offers, bookings), without exact addresses, access details, phone numbers, chat text or credentials (`NOT_PERSISTED`), at most 1 MB | Until sign-out or account deletion (purged), or a new cache version | First | iOS, Android | `apps/mobile/src/apollo/cache-persist.ts` |
| `taskbeavo.apolloCacheVersion` | AsyncStorage | The cache format version, so an old cache is purged at launch | As above | First | iOS, Android | `apps/mobile/src/apollo/cache-persist.ts` |
| Expo push token | Held by the OS and Expo; the app keeps its copy under `taskbeavo.pushToken` | Delivering booking notifications | Until the app is removed or the user turns notifications off | Third (Expo, Apple APNs, Google FCM) | iOS, Android | Not requested yet (see above) |
| Stripe SDK data, including the `m` cookie on `m.stripe.com` in the app's cookie store | Stripe's fraud prevention for the native payment sheet (`m.stripe.com/6`) | `m`: 2 years; the rest Stripe's | Third (Stripe) | iOS, Android | Only once a checkout screen opens: importing `@stripe/stripe-react-native` starts the native SDK (on iOS it posted to `m.stripe.com/6` and stored `m` at every launch, QA F4), so `apps/mobile/src/payments/checkout-route.tsx` loads it lazily and `__tests__/stripe-lazy.test.tsx` fails if any route imports it statically. On Android the SDK's client is created only by `StripeProvider` (`initialise`). Stripe Link is hidden in the sheet (`link: { display: 'never' }`); see the web section for turning it off on the account. **NEEDS REVIEW** (BEAV-39): the `m` cookie and fraud signals at checkout as strictly necessary |
| `EAS-Client-ID` (expo-eas-client) | iOS `UserDefaults`, Android SharedPreferences | A random UUID created once per install and sent as the `EAS-Client-ID` header on every update check to `u.expo.dev`; Expo uses it to deliver over-the-air updates and to bucket staged rollouts | Until the app is removed | Third (Expo) | iOS, Android | `expo-updates` (`updates.url` in `apps/mobile/app.config.ts`). **NEEDS REVIEW** (BEAV-39): strictly necessary for keeping the installed app working, or consent / removal? |
| expo-updates cache | App storage (an SQLite database plus downloaded bundles and assets) | The over-the-air update the app runs | Until replaced by a newer update or the app is removed | First (written by Expo's library for the app) | iOS, Android | `expo-updates` |
| Google / Apple sign-in | The OS account frameworks (Credential Manager, AuthenticationServices) | "Sign in with Google / Apple" | The OS's | Third (Google, Apple) | Android (Google), iOS (Apple, Google) | `apps/mobile/src/auth/providers` |

### Sentry and Honeycomb on mobile: device identifiers

Checked 2026-11-07 against `@sentry/react-native` 8.28.0:

- The native Sentry SDKs keep an **installation ID** (a random UUID in a file in the app's storage). On iOS the React Native SDK adds it as `user.id` to every JavaScript event when no user is set (`RNSentry.mm`, `fetchNativeDeviceContexts`); on Android the native SDK adds it to native events the same way; release-health sessions carry it as `did`.
- The iOS SDK also adds `contexts.app.device_app_hash`, a SHA-1 of `identifierForVendor` that stays the same for every event from the install (QA F5); Android's SDK puts its installation ID in `contexts.device.id`.
- BEAV-190 stops them from leaving the phone with JavaScript events: `enableAutoSessionTracking: false` (no sessions), and the app's `beforeSend` drops `user` and the device identifiers (`DEVICE_IDENTIFIERS`: `app.device_app_hash`, `device.id`, `device.device_unique_identifier`, `device.installation_id`) from every JavaScript event, so JavaScript events carry **no user at all**. `initSentry` sets the fixed user `{ id: 'anonymous' }` (the same on every install) on the native scope, so native crash reports don't fall back to the installation ID (`apps/mobile/src/sentry/`). Session Replay, screenshots and view hierarchy are off.
- **Residual:** native crash reports are written by the native SDK and skip the JavaScript `beforeSend`, so they may still carry `device_app_hash` (iOS) and the device context; the native SDK also keeps its installation ID file on the phone. Before launch, confirm on a development build (iOS and Android) what a native crash report shows (`user.id: anonymous`, no `did`), and strip `device_app_hash` natively or keep this as a documented residual. **NEEDS REVIEW** (BEAV-39) if it stays.
- **Honeycomb:** the app sends no telemetry of its own; the Apollo link's per-request `traceparent` is generated in memory. Nothing stored, no device identifier.

## Server side (not on the device, listed for the privacy notice)

- The API stores the coarse city and country of each sign-in on the session, looked up in a local MaxMind GeoLite2 City database; the IP address isn't stored. The API service updates its own copy weekly and deletes one older than 30 days (`backend/accounts/geolite.py`). MaxMind receives no user data: the download sends only TaskBeavo's MaxMind account credentials.

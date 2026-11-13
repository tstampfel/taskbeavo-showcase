---
name: expo-screen
description: Build or rework a React Native screen in apps/mobile (Expo, development builds) with ui-tokens, i18n (hr/en/de/fr), accessibility (44px targets, large text, screen readers), loading/empty/error states, Apollo hooks with generated types, a Sentry error boundary, and notes for testing on iOS and Android via development builds and Expo Orbit. Use this for any new screen, tab, modal, bottom sheet or form in the mobile app, e.g. "add a booking details screen", "build the offers list", "post-a-task form", even when the request only describes the UI.
---

# Expo screen

TaskBeavo's mobile users include older parents in Dubrovnik and their families booking from Germany, Austria and Switzerland. A screen is done when it works in four languages, at large text sizes, with VoiceOver/TalkBack, and while loading or offline, on both iOS and Android.

**Plan:** `docs/PLAN.md`, sections "User journeys end to end, including the unhappy paths"; "Languages: Croatian, English, German and French". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

Native setup (deep links, permission prompts, cache persistence, Maestro tests) lives in `mobile-platform`.

## Steps

### 1. Plan

- Choose the Expo Router path: `apps/mobile/app/<route>.tsx`.
- List the data the screen needs. If the API doesn't provide it, do `graphql-feature` first.
- Define four states: loading, empty (with a next action, such as "No offers yet. We'll notify you."), error, and content.

### 2. Data

- Put the operation in `packages/graphql/src/operations/`, selecting `id` on every object, then run:
  ```bash
  pnpm --filter @taskbeavo/graphql codegen
  ```
- Import the typed document from `@taskbeavo/graphql` and use `useQuery`, `useMutation` or `useSubscription`.
- Use cursor pagination with the cache `merge` policy and `fetchMore` on end reached. Live lists use `subscribeToMore`.
- The cache is persisted, so use `fetchPolicy: "cache-and-network"` where freshness matters. Booking screens refetch when the app returns to the foreground, using `AppState` or the shared `useRefetchOnForeground` hook.
- Disable mutation buttons while a mutation is pending. Use optimistic responses only for non-money actions.
- Payment state comes only from the server. After the Stripe payment sheet closes, show "Confirming payment…" until `FUNDS_HELD` arrives.

### 3. UI

- Import colours, spacing, radii and typography from `@taskbeavo/ui-tokens`: green `#1F5C46`, orange `#F2A33A`, off-white `#F5F4EF`, ink `#17211C`, Manrope. No raw hex values or magic numbers.
- Check AA contrast for any new colour pairing. Body text should be ink on off-white.
- Never show another user's phone, IBAN or private OIB. The exact address, access details and the on-site contact's phone (with a Call link) appear only for the helper of an active accepted booking (rule e).

### 4. i18n

- Use `@taskbeavo/i18n` keys (i18next) for every string, in `hr`, `en`, `de` and `fr`, including accessibility labels and error codes. Follow `i18n-strings`.
- German and French run 20–30% longer, so check buttons and headers for truncation; buttons wrap rather than truncate.
- Format dates, EUR amounts and numbers with the active locale.

### 5. Accessibility

- Every touchable is at least 44×44 (use `minHeight`/`minWidth` or `hitSlop`) and has an `accessibilityRole` and `accessibilityLabel`.
- Test at the largest font scale: no fixed text heights, text wraps, content scrolls.
- Hide the decorative beaver from screen readers. Announce status changes with `AccessibilityInfo.announceForAccessibility`.

### 6. Errors and Sentry

- Wrap the screen in the shared `ScreenErrorBoundary`, which is built on `Sentry.ErrorBoundary`. It shows a translated fallback with a "Try again" button.
- Don't add PII to Sentry tags, extra data or breadcrumbs (no names, phone numbers, addresses or chat text). Tag IDs only, such as `booking.id`. See `observability`.
- Network errors show an offline message. GraphQL error codes map to translated messages.

### 7. Testing on iOS and Android

TaskBeavo uses **development builds**. Expo Go doesn't work, because the app uses native modules (Stripe, Sentry, notifications).

- If a development build is already installed, start Metro:
  ```bash
  pnpm --filter @taskbeavo/mobile start
  ```
  Then open the dev client on the simulator or device.
- If native config changed (a new native module or config plugin), make a new development build:
  ```bash
  cd apps/mobile && eas build --profile development --platform ios      # or android
  ```
  Install it with **Expo Orbit**: open the build from the EAS dashboard or Orbit's menu bar list and choose the simulator or device. Alternatively, run `eas build:run --platform ios --latest`.
- Check the screen on at least one iOS and one Android device or simulator, in `hr`, `de` and `fr`, at the largest font size, and with VoiceOver or TalkBack on for the main action.
- Add unit tests for non-trivial hooks and formatters (`pnpm --filter @taskbeavo/mobile test`). Put `testID`s on key elements.

## Checklist

- [ ] Typed operation from `@taskbeavo/graphql`; `id` selected everywhere
- [ ] Loading, empty, error and content states
- [ ] Booking data refetches on foreground; payment state comes only from the server
- [ ] Tokens only (including ink `#17211C`); AA contrast
- [ ] All strings in hr, en, de and fr; German and French checked for truncation
- [ ] 44px targets, roles and labels; works at the largest text size and with VoiceOver/TalkBack
- [ ] Wrapped in `ScreenErrorBoundary` (Sentry); no PII in Sentry
- [ ] No phone, IBAN or private OIB of other users; exact address only for the booked helper while active
- [ ] Tested on iOS and Android via a development build (Expo Orbit when a new build was needed)
- [ ] `pnpm turbo run typecheck lint test --filter @taskbeavo/mobile` passes

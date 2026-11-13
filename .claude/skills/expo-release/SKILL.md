---
name: expo-release
description: Create and ship TaskBeavo mobile builds with EAS, covering eas.json profiles (development, preview, production), config plugins (Stripe, Sentry, notifications, camera, location), versioning and runtime version, EAS Build for iOS and Android, installing builds with Expo Orbit, EAS Submit to TestFlight and Google Play, EAS Update for JS-only fixes, and Sentry source map upload. Use this whenever someone wants a new dev/preview/production build, to add a native module or config plugin, bump the version, ship to TestFlight or the Play Store, push an OTA fix, or asks "why does the build fail", even if EAS isn't named.
---

# Expo release

TaskBeavo ships through EAS. Native changes need a new build; JS-only changes can go out as an EAS Update. Mixing those up is the main risk: an update that needs a native module crashes on installed builds. Everything that reaches real users (submit, production update) is outward-facing, so **confirm with the user before running those commands**. The project settings make Claude ask before any `eas` command.

**Plan:** `docs/PLAN.md`, sections "A Django monolith, Expo apps and Stripe fit a solo React developer"; "Build sequence for one developer". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

Deep links, permission explanation screens, store privacy labels and Maestro tests are owned by `mobile-platform`; run its store-privacy check before every production submit.

## Configuration

### `apps/mobile/eas.json`

```json
{
  "cli": { "version": ">= 16.0.0", "appVersionSource": "remote" },
  "build": {
    "development": { "developmentClient": true, "distribution": "internal", "channel": "development",
                     "env": { "APP_ENV": "development" } },
    "preview":     { "distribution": "internal", "channel": "preview", "env": { "APP_ENV": "preview" } },
    "production":  { "channel": "production", "autoIncrement": true, "env": { "APP_ENV": "production" } }
  },
  "submit": {
    "production": {
      "ios": { "ascAppId": "<App Store Connect app id>" },
      "android": { "track": "internal", "releaseStatus": "draft" }
    }
  }
}
```

- `appVersionSource: "remote"` with `autoIncrement` lets EAS own the build numbers. You bump only the user-facing `version` in `app.config.ts`.
- Secrets (Sentry auth token, Stripe publishable key per environment, Google services files) are EAS environment variables or secret files, never committed.

### `apps/mobile/app.config.ts` plugins

- `expo-dev-client`
- `@stripe/stripe-react-native`, with `merchantIdentifier` (Apple Pay) and `enableGooglePay`
- `@sentry/react-native/expo`, with `organization`, `project` and the EU `url` (source maps upload during EAS Build when `SENTRY_AUTH_TOKEN` is set)
- `expo-notifications`, with icon and colour `#1F5C46`
- `expo-camera` and `expo-location`, with permission texts
- Permission texts must be translated: use the `locales` field with `hr`, `en`, `de` and `fr` JSON from `packages/i18n`.
- `runtimeVersion: { policy: "fingerprint" }`, so an update only reaches builds with the same native layer.
- `updates.url` set to the project's EAS Update URL.

## Steps

### 1. Decide: build or update?

| Change | Ship as |
|---|---|
| New or updated native module, config plugin, permission, `app.config.ts` native field, Expo SDK upgrade | New build (development first, then preview, then production) |
| JS, TS or assets only | EAS Update to the matching channel |

When unsure, compare fingerprints: `npx expo-updates fingerprint:generate --platform ios` before and after. A changed fingerprint means you need a build.

### 2. Development build (for developers and simulators)

```bash
cd apps/mobile
eas build --profile development --platform ios        # simulator: add "ios": {"simulator": true} to a dev-sim profile if needed
eas build --profile development --platform android
```

To install, open **Expo Orbit** (macOS menu bar), choose the build from the EAS list or drag the `.ipa`/`.apk` in, and pick the simulator, emulator or connected device. From the CLI: `eas build:run --platform ios --latest`.

### 3. Preview build (internal testers)

```bash
eas build --profile preview --platform all
```

Share the install link, or install with Orbit. Test the release checklist below on one iOS and one Android device.

### 4. Production build and submit (confirm with the user first)

```bash
eas build --profile production --platform all
eas submit --profile production --platform ios        # → TestFlight
eas submit --profile production --platform android    # → Google Play internal track, draft
```

Promote from TestFlight or the internal track to production in the store consoles, after review.

### 5. EAS Update for JS-only fixes (confirm before production)

```bash
eas update --channel preview --message "Fix offer list empty state"
# verify on a preview build, then:
eas update --channel production --message "Fix offer list empty state"
npx sentry-expo-upload-sourcemaps dist
```

The last command uploads the update's source maps to Sentry, so stack traces are readable. If an update is bad, roll back with `eas update:rollback`, or republish the previous update group.

### 6. Release checklist on device

On iOS and Android:
- login, post a task, send an offer;
- accept an offer and pay with a Stripe test card in preview, or a real card in production;
- the push notification arrives and deep-links;
- camera upload to R2 works, and location permission works;
- hr, en, de and fr; largest text size;
- Maestro flows from `mobile-platform` pass on both platforms;
- a forced test error appears in Sentry (EU) with readable frames and no PII.

## Checklist

- [ ] Build vs update decided (fingerprint checked for anything unclear)
- [ ] `eas.json` has development, preview and production with channels; `appVersionSource: remote`
- [ ] Config plugins present: dev client, Stripe, Sentry (EU), notifications, camera, location; permission texts in hr, en, de and fr
- [ ] `runtimeVersion` fingerprint policy; user-facing `version` bumped for store releases
- [ ] No secrets committed; EAS env vars/secrets used
- [ ] Development/preview builds installed via Expo Orbit and checked on iOS and Android
- [ ] App Store privacy labels and Google Play data safety form match what the app collects (`mobile-platform`)
- [ ] User confirmed before `eas submit` or a production `eas update`
- [ ] Sentry source maps uploaded (build: automatic; update: `sentry-expo-upload-sourcemaps`)
- [ ] Release checklist on device passed

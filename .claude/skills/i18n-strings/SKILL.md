---
name: i18n-strings
description: Add or change any user-facing text in TaskBeavo, covering ICU keys in packages/i18n for hr, en, de and fr (with Croatian one/few/other plurals), next-intl on web, i18next on mobile, Django translations for emails, push, SMS and PDFs in the recipient's language, locale formatting of money and dates, German and French layout length, and the missing-key CI check. Legal texts are flagged for professional translation, never machine-translated. Use this whenever a change adds a label, button, error code, empty state, notification, email, SMS, PDF or accessibility label, renames a key, or someone says "translate", "add the German/French text", "wording", "copy", even if i18n isn't named.
---

# i18n strings

TaskBeavo speaks four languages from day one: Croatian (default for helpers and on-site contacts), English (source language), German and French (diaspora posters in Germany, Austria, Switzerland, France and Belgium). The same booking is read by Ana in Vienna in German, Marko in Croatian, and Mama Kata by SMS in Croatian, so a missing key or a sender-language message is a real bug, not a cosmetic one.

**Plan:** `docs/PLAN.md`, section "Languages: Croatian, English, German and French". Read it before changing the workflow; the plan is the source of truth.

## Where text lives

| Part | Library | Notes |
|---|---|---|
| Catalogs | `packages/i18n/src/locales/{hr,en,de,fr}.json` (ICU MessageFormat) | One set of keys shared by web, mobile and backend |
| Web | `next-intl` | Locale in the URL (`/hr`, `/en`, `/de`, `/fr`); server components use `getTranslations`, client components `useTranslations` |
| Mobile | `i18next` + `react-i18next` + `i18next-icu` + `expo-localization` | Phone language on first launch, then the saved choice; catalogs bundled, text fixes can ship as EAS Updates |
| Backend | Django gettext (`backend/locale/<lang>/LC_MESSAGES/django.po`) | Only emails, push, SMS and PDFs. The GraphQL API returns enum keys and error codes, never text |
| Money, dates, numbers | `Intl` (web, mobile), Babel (backend) | "€33,60" in hr/de/fr, "€33.60" in en; task times in Europe/Zagreb |

## Steps

### 1. Name the key

- Namespace by feature and screen: `booking.release.confirmTitle`, `errors.OUT_OF_SERVICE_AREA`, `push.offerCreated.title`.
- Error codes from the API map 1:1 to `errors.<CODE>`. Enum values map to `enums.<Enum>.<VALUE>` (for example `enums.BookingStatus.PAYMENT_REQUESTED`).
- Never build sentences by concatenation; word order differs between languages. Use ICU placeholders: `"{helperName} is on the way"`.

### 2. Write all four locales in the same change

- English is the source. Draft `hr`, `de` and `fr` in the same PR, and add the keys to `packages/i18n/REVIEW.md` (or the translation tool once one exists) so a native speaker reviews them before release, Croatian first.
- **Plurals:** Croatian has three forms. Use ICU, never `count === 1 ? … : …`:
  ```json
  "offers.count": "{count, plural, one {# ponuda} few {# ponude} other {# ponuda}}"
  ```
  German and French use `one`/`other`. Note French treats 0 as `one`.
- Use proper characters (č, ć, đ, š, ž; ä, ö, ü, ß; é, è, ç, «  »). Formal address: German `Sie`, French `vous`, Croatian `Vi` in transactional text.

### 3. Use the key

- Web: `const t = useTranslations("booking.release")` / `await getTranslations(...)`. Pages under `app/[locale]/`.
- Mobile: `const { t } = useTranslation(); t("booking.release.confirmTitle")`. Accessibility labels are translated too.
- Backend: render in the recipient's language with `core.i18n`: `recipient_locale(recipient)` returns `hr`/`en`/`de`/`fr` from the recipient's `locale` (the `LocaleRecipient` contract: users have a `locale` field, on-site contacts must expose one too); a missing or unsupported value falls back to Croatian and logs a warning with only the model type, `pk` and reason, and `with in_recipient_locale(recipient):` activates it for gettext. The locale **never** comes from the request that triggered the message. Push and SMS bodies stay short; SMS is plain GSM-friendly text where possible.
- Emails: `core.email.render_email("<name>", recipient, context)` returns subject, HTML and text in the recipient's language, for sending with Resend. Add `core/templates/email/<name>/subject.txt`, `body.html` (extends `email/base.html`, the branded layout) and `body.txt` (extends `email/base.txt`); `email/welcome` is the sample.
- Error codes: a new code is a member of `core.errors.ErrorCode` with a description (it appears in `schema.graphql`), plus `errors.<CODE>` in all four catalogs; `packages/i18n` fails its tests if one is missing.
- Format money and dates with the active locale; never hand-format "€" strings.

### 4. Check layout length

German and French run 20–30% longer than English. Check every new button, header, tab and push preview in `de` and `fr`:
- buttons wrap rather than truncate; no fixed text heights;
- mobile at the largest font size; web at 375px;
- push titles fit the lock-screen preview (about 40 characters).

**Mark tight layouts** in `packages/i18n/catalogue-check.json` under `"layouts"`, so the catalogue check warns when a translation outgrows them. Each entry is `{ "pattern": "<key>", "maxRatio"?: <number>, "maxLength"?: <number>, "note"?: "<where it shows>" }`. The pattern is a dotted key where `*` matches one segment (`push.*.title`). The check warns when a `de` or `fr` message is longer than `maxRatio` × the English one (default 1.3, the top of the usual 20–30%) or longer than `maxLength` characters. Length is the visible text: placeholders count nothing, `#` counts one, and plurals and selects count their longest option. Mark buttons, tabs, headers and push titles (`{ "pattern": "push.*.title", "maxLength": 40 }`). A warning never fails CI; fix the layout or the wording, or raise `maxRatio` for that key if it is checked and fine on screen.

### 5. Run the checks

```bash
pnpm --filter @taskbeavo/i18n check:catalogues   # missing, extra and unused keys, ICU syntax, layout length warnings
pnpm --filter @taskbeavo/i18n test          # plural categories, error codes, the check's own tests
pnpm turbo run lint                         # blocks hard-coded strings in components
# Backend catalogs. Needs gettext and GDAL, so run it in the compose dev image; commit the .po and the .mo files
docker compose run --rm -T --no-deps django sh -c "uv run python manage.py makemessages -l hr -l en -l de -l fr --no-obsolete --ignore=.venv && uv run python manage.py compilemessages --ignore=.venv"
cd backend && uv run pytest core/tests/test_locale_catalogs.py   # every msgid translated, .mo matches .po
```

`check:catalogues` (`packages/i18n/scripts/check-catalogues.mjs`) runs in the CI `JS` job and fails it when:
- a key exists in one catalog but not all four (Croatian `few` lives inside the ICU message, so it never counts as a key);
- a message isn't valid ICU;
- a `hr`, `de` or `fr` message is identical to `en`, i.e. left untranslated. Strings of 12 visible characters or fewer (placeholders count nothing, as for layout length, so `Version {version}` is short) and strings with no space are ignored. When a longer text is genuinely the same in a language (a licence's English original), list it under `"sameAsEnglish"` in `catalogue-check.json` as `"<key>": { "locales": ["de", "fr"], "reason": "…" }`; the check warns once a listed locale differs, so the entry can shrink;
- a key isn't used by `apps/web/src`, `apps/mobile/app` or `apps/mobile/src` (tests and e2e don't count). A use is a string literal equal to the full key, or to its tail after a namespace the file opens (`useTranslations('ns')`, `getTranslations({ namespace: 'ns' })`, i18next `keyPrefix`). A template literal marks every key under its static start, so `` t(`enums.BookingStatus.${status}`) `` covers all `enums.BookingStatus.*`; build dynamic keys that way, never by joining strings with `+`. Every `errors.<CODE>` whose code is in `enum ErrorCode` in `schema.graphql` counts as used. Comments don't count. It's a heuristic: in a file that opens a namespace, any same-named literal (`testID = 'title'`) also counts as a use of `<ns>.title`, so delete keys by hand when you remove the code that used them. Delete an unused key from all four catalogs (and `REVIEW.md`), or, if it must stay, list it under `"keep"` in `catalogue-check.json` with the reason; the check warns once a kept key is used again, so the entry can go.

Backend `.po` catalogs aren't part of it: their msgids come from the code through `makemessages`, so they can't drift from it, and `core/tests/test_locale_catalogs.py` already fails the `Backend` job when a msgid is untranslated in hr, de or fr or a `.mo` is stale.

## Legal texts are different

Terms, helper terms, privacy notice, withdrawal information, cancellation policy and cookie policy are **never machine-translated** and never drafted here. Put placeholder text clearly marked `DRAFT – needs professional translation and lawyer review` and list the document in the PR description under "Needs professional translation". The versions themselves live in `LEGAL_DOCUMENT` (see `legal-terms`). At a real launch, fiscal receipts are issued in Croatian (the showcase issues none); the email around them can include a translated summary.

User content (chat, task descriptions, reviews) is shown as written; there is no auto-translation at launch.

## Checklist

- [ ] Keys in all four catalogs (hr, en, de, fr); `check:catalogues` passes
- [ ] ICU placeholders, no concatenation; Croatian plurals use one/few/other
- [ ] API returns codes and enums; clients translate them
- [ ] Backend messages rendered in the recipient's locale
- [ ] Money and dates formatted with `Intl`/Babel
- [ ] German and French checked for length on mobile (largest font) and web (375px); tight layouts marked in `catalogue-check.json`
- [ ] New keys listed for native-speaker review
- [ ] Legal texts flagged for professional translation, not machine-translated

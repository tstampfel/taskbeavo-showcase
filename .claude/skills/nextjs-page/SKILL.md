---
name: nextjs-page
description: Build or rework a page in apps/web (Next.js App Router on Vercel), choosing server components for public SEO pages (task pages, helper profiles, diaspora landing pages) and client components with Apollo for logged-in pages, with metadata, i18n (hr/en/de/fr), a responsive layout down to 375px, accessibility, Sentry and OpenTelemetry tracing. Use this for any new route, page, layout or landing page on the website, e.g. "help for parents in Dubrovnik landing page in German", "public helper profile", "my bookings page", even if SEO, i18n or tracing aren't mentioned.
---

# Next.js page

There are two kinds of web page, and mixing them up is the most common mistake.

- **Public pages** must rank in search and load fast: public task pages, helper profiles, category and city pages, and the diaspora landing pages (for example a German "Hilfe für Ihre Eltern in Dubrovnik"). These are **server components**.
- **Logged-in pages** are personal and live: dashboard, my tasks, offers, bookings, messages, payments, settings. These are **client components** with Apollo.

**Plan:** `docs/PLAN.md`, sections "Languages: Croatian, English, German and French"; "User journeys end to end, including the unhappy paths"; "Service area: tasks in Croatia only". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

Legal pages (`/terms`, `/privacy`, `/helper-terms`, `/cancellation`, `/cookies`, `/imprint`) also follow `legal-terms`.

## Steps

### 1. Classify the page

| | Public / SEO | Logged-in |
|---|---|---|
| Indexed | yes | no (`robots: { index: false }`) |
| Personal data | never | yes |
| Rendering | Server Component, static or ISR (`revalidate`) | `"use client"` + Apollo hooks |
| Data | RSC Apollo client (`registerApolloClient` from `@apollo/client-integration-nextjs`) | `useSuspenseQuery`/`useQuery` + `subscribeToMore` |

If a page needs both, keep the page a server component and put the interactive part in a client island.

### 2. Route and locale

- Place the page at `apps/web/app/[locale]/<route>/page.tsx`, where `locale` is one of `hr`, `en`, `de` or `fr` (default `hr`), resolved by next-intl middleware from the URL, a saved cookie or `Accept-Language`.
- Public pages: use `generateStaticParams` over locales (and cities, categories or helper slugs), and add `hreflang` alternates for all four locales and a translated sitemap.
- Diaspora landing pages are written primarily in `de` (also `en` and `fr`). Use content that speaks to booking for a parent remotely, not a machine translation of the Croatian page. French marketing aimed at France, Belgium or Switzerland counts as directing activities there, so a French landing page needs the legal review in the Languages section first; flag it rather than publishing.
- Pages about posting or browsing tasks say clearly that tasks must be in an active Croatian city; posters can be anywhere (rule j).

### 3. Data

- Put the operation in `packages/graphql/src/operations/`, selecting `id` everywhere, then run `pnpm --filter @taskbeavo/graphql codegen` and import the document from `@taskbeavo/graphql`.
- Public pages fetch only public fields: approximate location, the helper's first name and initial, ratings. Never fetch personal data in a server component that may be cached across users.
- Logged-in pages: mutations return full objects. Payment state comes only from the server ("Confirming payment…" until `FUNDS_HELD`). Booking pages refetch on `visibilitychange` back to visible.
- If the API doesn't provide the data yet, do `graphql-feature` first.

### 4. Metadata

- Public pages: `generateMetadata` provides a translated title and description, `alternates.canonical` and `alternates.languages`, an Open Graph image and a Twitter card. Add JSON-LD: `Service` or `LocalBusiness` for category and landing pages, `Person` with ratings for helper profiles (first name only), and `BreadcrumbList`.
- Logged-in pages: a title and `robots: { index: false }`.

### 5. UI

- Use tokens from `@taskbeavo/ui-tokens` (green `#1F5C46`, orange `#F2A33A`, off-white `#F5F4EF`, ink `#17211C`) and Manrope via `next/font/google`.
- Build mobile-first: no horizontal scroll at **375px**, then check 768px and 1280px.
- Every string comes from `@taskbeavo/i18n` (next-intl) in hr, en, de and fr, following `i18n-strings`. Check German and French at 375px.
- Accessibility: landmarks, one `h1`, 44px targets, visible focus, AA contrast, `alt` text, labelled form fields with `aria-describedby` errors.
- Use `next/image`, with the R2 host in `images.remotePatterns`.

### 6. Errors, Sentry and tracing

- Add `error.tsx` (calls `Sentry.captureException`, shows a translated fallback), `not-found.tsx` and `loading.tsx`.
- `@sentry/nextjs` is configured with the EU DSN, with Replay disabled and `sendDefaultPii: false`. Don't add names, email or address to Sentry context; use IDs only.
- OpenTelemetry: `instrumentation.ts` registers `@vercel/otel`, exporting to Honeycomb EU. For a page with notable server work, wrap it in a span with safe attributes (`page.route`, `task.id`). The Apollo Link from `@taskbeavo/graphql` adds `traceparent` to every GraphQL request. See `observability`.

### 7. Test

```bash
pnpm --filter @taskbeavo/web exec playwright test <spec>
pnpm turbo run typecheck lint test --filter @taskbeavo/web
```

- Add a Playwright spec for the main path at 375px and 1280px wide, using `data-testid` selectors.
- For public pages, assert the `<title>`, the meta description, the `hreflang` links and the JSON-LD.

## Checklist

- [ ] Classified as public (server component) or logged-in (client + Apollo)
- [ ] Public pages fetch public fields only; no personal data in cached server components
- [ ] Typed operations; `id` selected everywhere
- [ ] Metadata: translated title and description, canonical, hreflang, OG, JSON-LD; or `noindex` for logged-in pages
- [ ] hr, en, de and fr strings; diaspora pages written natively in de; French marketing pages flagged for legal review
- [ ] No horizontal scroll at 375px; checked at 768px and 1280px
- [ ] Tokens + Manrope; AA; 44px targets; visible focus
- [ ] `error.tsx` reports to Sentry with no PII; `loading.tsx` and `not-found.tsx` present
- [ ] OTel spans have safe attributes only; traceparent sent through the Apollo Link
- [ ] Playwright spec at mobile and desktop widths passes

---
name: graphql-feature
description: Build a TaskBeavo API feature end to end, from Django model and migration, Strawberry type/query/mutation/subscription with permissions, pytest tests, schema.graphql export and GraphQL Code Generator, to Apollo operations, cache type policy or subscribeToMore handler, and the web/mobile code that uses it. Use this whenever a task needs a new or changed field, type, query, mutation or subscription, touches a model the API exposes, or says "expose X to the app", "add an endpoint", "the screen needs Y", even if GraphQL isn't named. For booking/payment state use booking-payments too; for live events use realtime-notifications too.
---

# GraphQL feature

A TaskBeavo feature crosses the monorepo: `backend/` defines the API, exports `packages/graphql/schema.graphql`, and `packages/graphql` holds the operations, generated types and type policies that `apps/web` and `apps/mobile` import. Doing the steps in order keeps the clients in step with the server, keeps Apollo's cache normalising, and stops a resolver without a permission check from leaking data. Land the whole feature in one branch.

**Plan:** `docs/PLAN.md`, sections "A Django monolith, Expo apps and Stripe fit a solo React developer"; "GraphQL subscriptions deliver live updates, push covers the rest"; "Data model and the reliability patterns that matter"; "Service area: tasks in Croatia only". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

## Steps

### 1. Model and migration

- Edit the model in the relevant app under `backend/`. Use PostGIS fields (`PointField`) for locations.
- For personal data (address, phone, OIB, IBAN, date of birth), decide now:
  - Is it encrypted? OIB and IBAN must be (rule e).
  - Who may see it?
  - What happens on deletion: delete, anonymise, or archive? See `gdpr-data-request`.
- Create the migration:
  ```bash
  cd backend && uv run python manage.py makemigrations <app>
  uv run python manage.py sqlmigrate <app> <number>   # read the SQL
  uv run python manage.py migrate
  ```
  Check that the migration is reversible and won't lock a big table: add nullable columns first, then backfill.

### 2. Strawberry type

- Use `@strawberry_django.type(Model)` with fields listed **explicitly**. Never `fields="__all__"`, because a new model field would leak automatically.
- Include `id: strawberry.ID` on every type (rule d). Apollo normalises on `__typename:id`.
- Other users get approximate data, for example `approximate_location` (a fuzzed point or neighbourhood). Exact address and access details are visible only to the helper of an active accepted booking, behind a permission, and hidden again when it closes (rule e). Phone numbers and IBAN are never exposed to anyone but the owner; OIB neither, except a registered business's OIB, which its public profile and offers show with its business name; its registered street address goes only to the poster of a task it offered on.

### 3. Resolvers

- Each query, mutation and subscription has an **object-level** permission check: `permission_classes=[...]` or an explicit check that raises a typed error.
- Mutations return the full updated object (or a payload containing it), never just `ok: Boolean`.
- Errors shown to users are codes, for example `OFFER_ALREADY_ACCEPTED`, which the clients translate through `packages/i18n`.
- Add the new mutation to the rate-limit configuration (rule d). Keep it within the existing depth and complexity limits; if a legitimate query exceeds them, raise it rather than loosening the limit silently.
- Avoid N+1 queries: use the `strawberry_django` optimizer, or `select_related`/`prefetch_related`.
- If the mutation changes booking state or money, implement the transition through `booking-payments`.
- **Service area (rule j).** `postTask`, any mutation that changes a task's address, `sendOffer` and booking creation call the shared service-area check (`areas.services.assert_in_service_area(point)`, PostGIS `ST_Covers` against active `SERVICE_AREA` rows) and raise `OUT_OF_SERVICE_AREA`. Helper-facing task queries filter to the helper's selected service areas. Never rely on the client's check.
- Subscriptions: authenticate on connect, re-check permission for every event, and yield the full object. See `realtime-notifications`.

### 4. Tests

Write these in `backend/<app>/tests/`:
- the happy path returns the expected fields, including `id`;
- an unauthenticated request is rejected;
- another user is rejected, or sees redacted fields only (no exact address, phone, OIB or IBAN);
- for task, offer and booking mutations: a point outside every active service area (for example Osijek, or Vienna) is rejected with `OUT_OF_SERVICE_AREA`;
- the mutation returns the full object;
- the rate limit applies to the new mutation;
- for subscriptions, an unauthorised user gets no event.

```bash
cd backend && uv run pytest <app> -q && uv run pytest -q
uv run ruff check . && uv run pyright
```

### 5. Export the schema

```bash
cd backend && uv run python manage.py export_schema config.schema --path ../packages/graphql/schema.graphql
git diff packages/graphql/schema.graphql
```

A removed or renamed field breaks mobile builds already installed on phones. Deprecate the field (`deprecation_reason=`) instead of removing it, and remove it later.

### 6. Operations and codegen

- Add the operation to `packages/graphql/src/operations/<feature>.graphql`, selecting `id` on every object.
- Generate types:
  ```bash
  pnpm --filter @taskbeavo/graphql codegen
  ```
- Add an `InMemoryCache` policy in `packages/graphql/src/typePolicies.ts` if needed: `keyFields` for types without a plain `id`, `merge` for cursor-paginated lists, `read` for computed fields.
- For live data, add a `subscribeToMore` helper next to the operation. Its `updateQuery` only adds **new** list items, de-duplicated by `id`; updated objects normalise on their own.

### 7. Web and mobile

- Import the generated document from `@taskbeavo/graphql` in `apps/web` and/or `apps/mobile`. Build the UI with `nextjs-page` or `expo-screen`.
- Add every new string and error code to `packages/i18n` in `hr`, `en`, `de` and `fr` with `i18n-strings`.
- Run the checks:
  ```bash
  pnpm turbo run typecheck lint test
  ```

## Checklist

- [ ] Migration created, SQL read, reversible, safe on large tables
- [ ] Personal data: encryption, visibility and deletion behaviour decided
- [ ] Type lists fields explicitly and exposes `id` (rule d)
- [ ] No exact address, phone, OIB or IBAN visible to other users; OIB and IBAN encrypted (rule e)
- [ ] Object-level permission on every resolver
- [ ] Mutations and subscription events return the full object (rule d)
- [ ] Subscriptions check permissions on connect and on every event (rule d)
- [ ] Mutation rate-limited; depth and complexity limits respected; introspection still off in production (rule d)
- [ ] User-facing errors are codes translated in hr, en, de and fr (rule i)
- [ ] Task location re-checked against active service areas on post, address edit, offer and booking (rule j)
- [ ] No N+1 queries
- [ ] pytest covers happy path, unauthenticated, wrong user and redaction, rate limit
- [ ] `schema.graphql` exported and diff reviewed; nothing removed without deprecation
- [ ] Codegen run; type policy or `subscribeToMore` added where needed
- [ ] `pnpm turbo run typecheck lint test` passes

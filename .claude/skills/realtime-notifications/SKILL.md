---
name: realtime-notifications
description: Add a live event to TaskBeavo end to end, from the outbox row, through the Celery publisher to a Django Channels group and the Strawberry subscription with permission checks, to the Apollo cache update, Expo push and email fallback, with i18n strings and an explicit rule for which users may receive it. Use this whenever something should "update live", "notify the poster/helper", "send a push", "email the user", "show a badge", or a new offer, message, booking status or reminder must reach users, even if only one channel is mentioned.
---

# Real-time notifications

Every notification starts from one **outbox row**, written in the same transaction as the change (rule c). A Celery publisher fans it out to three channels:
- the GraphQL subscription, for when the app is open;
- an Expo push, for when the app is in the background;
- an email, as the fallback and for money events.

The hard part is usually **who may receive it**, so decide that first.

**Plan:** `docs/PLAN.md`, sections "GraphQL subscriptions deliver live updates, push covers the rest"; "Contacting people on the day: chat and push at launch, masked calling later"; "Languages: Croatian, English, German and French". Read the matching section before changing behaviour; the plan is the source of truth for product rules, and this skill is how to build them.

Push setup, permission prompts and deep-link routing on the device live in `mobile-platform`.

## Steps

### 1. Define the event

Write down:
- the topic, for example `offer.created`, `booking.updated` or `message.created`;
- the object it carries (the full object, with `id`);
- the recipient rule, for example "the task's poster" or "the accepted helper". Recipients are participants only;
- the channels. Money events (FUNDS_HELD, RELEASED, refunds) always add email;
- what must stay off the lock screen and out of inboxes: exact address, phone, OIB, IBAN, chat text in push bodies.

### 2. Outbox row

```python
Outbox.objects.create(topic="offer.created", aggregate_type="Offer",
                      aggregate_id=offer.id, payload={"task_id": task.id},
                      traceparent=current_traceparent())
```

The payload holds IDs only; the publisher reloads the current object. `traceparent` lets the Celery work join the request's trace (see `observability`).

### 3. Celery publisher

- `transaction.on_commit(lambda: publish_outbox.delay())`. A Beat task also sweeps for unsent rows every minute.
- Claim rows with `select_for_update(skip_locked=True)`, restore the trace context from `traceparent`, publish, and mark the row sent. Delivery is at-least-once, so every consumer must be idempotent: pass `core.outbox.outbox_key(row, channel)` (e.g. `outbox:17:push:v1`) as the Expo/Resend idempotency key or dedup record. Handlers run while the row lock is held, so bound every network call with a timeout.
- In the topic's handler (`@core.outbox.register_handler(topic)`), compute the recipients and call `core.realtime.send_to_users(row, user_ids)`. It sends the row's IDs and trace context to each user's Channels group `user.{user_id}`.
- Enqueue push and email as separate tasks with retries, so one failing channel doesn't block the others.

### 4. Strawberry subscription (rule d)

- On connect, `config/ws.py` authenticates the `graphql-ws` `connection_init` payload through `config/ws_auth.py` (`{"authToken": ...}`) and closes unauthenticated connections with 4403. Nothing to add per subscription.
- The resolver iterates `core.realtime.viewer_events(info, {topic}, load)`. It joins only `user.{user_id}` (never a group named after a guessable resource such as a task or booking ID), drops other topics and redelivered rows, and ends the stream if the user was deactivated.
- `load(user, event)` reloads the object from `event.aggregate_id` and re-checks that this user may still see it, returning `None` to skip. This is the per-event permission check.
- Yield the full object with `id`. `core/tests/test_realtime.py` shows the whole pattern with a test-only subscription.
- Test with Channels' `WebsocketCommunicator`: an authorised user receives the event, an unauthorised user doesn't, and a duplicate publish is harmless.
  ```bash
  cd backend && uv run pytest notifications -q
  ```

### 5. Apollo cache (web and mobile)

- Export the schema and run codegen (see `graphql-feature`), then add the subscription document in `packages/graphql/src/operations/`.
- Updated objects merge into the cache on their own. For **new** objects, use `subscribeToMore` on the list query, prepend the item, and de-duplicate by `id`.
- Reconnect and foreground refetches are already wired: `createSubscriptionsClient` (packages/graphql) refetches active queries when the socket reconnects, and mobile's `useRefetchOnForeground` does on return from the background. Keep booking screens' queries active so they are included.

### 6. Expo push

- Send to the recipient's stored Expo push tokens. Delete a token when Expo returns `DeviceNotRegistered`.
- Translate the title and body from `packages/i18n` keys, in the recipient's locale (`hr`, `en`, `de` or `fr`), never the sender's. Diaspora posters often use `de`; on-site contacts usually `hr`.
- `data` holds only a route and IDs, for example `{ "route": "/bookings/123" }`. The app fetches the details after the tap.
- Respect the user's notification preferences, and don't notify a user about their own action.

### 7. Email fallback

- Use a translated (hr, en, de, fr) Django template, with no personal data beyond what the recipient already sees, and a deep link back into the app.
- Send email for money events, and for anything that matters when push is disabled.

### 8. i18n

Add every push title, push body, email subject and email body key to `packages/i18n` in all four locales with `i18n-strings`, and check that the German and French text fits a push preview.

## Checklist

- [ ] Topic, payload object, recipient rule and channels written down
- [ ] Recipients are participants only; permission re-checked per event
- [ ] Outbox row in the same transaction, with `traceparent`
- [ ] Publisher idempotent, uses `skip_locked`, with a Beat sweep
- [ ] Channels group is per user (`user.{id}`)
- [ ] Subscription authenticates on connect, checks every event, yields the full object with `id`
- [ ] Apollo `subscribeToMore` de-duplicates by `id`; refetch on reconnect and foreground
- [ ] Push: translated, no private data on the lock screen, `data` = route + IDs
- [ ] Email fallback for money events, translated
- [ ] i18n keys added in hr, en, de and fr
- [ ] Tests: authorised receives, unauthorised doesn't, duplicates harmless

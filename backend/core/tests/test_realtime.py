"""Live events end to end: outbox row -> Celery publisher -> user.{id} group -> subscription.

The schema here has a test-only `accountUpdated` subscription; real subscriptions are built
the same way on `core.realtime.viewer_events`. Users are real accounts, authenticated with
their access token in connection_init (config/ws_auth.py).
"""

import asyncio
import datetime as dt
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import strawberry
from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.urls import re_path
from django.utils import timezone
from strawberry.types import Info

from accounts import sessions
from accounts.models import RefreshToken, Session
from accounts.sessions import issue_websocket_token
from accounts.tests.factories import access_token, make_user, sign_in
from config.graphql_limits import MUTATIONS_REQUIRE_HTTP
from config.schema import build_schema
from config.ws import SubscriptionConsumer, WSContext
from core import outbox
from core.errors import ApiError
from core.models import Outbox
from core.realtime import LiveEvent, send_to_users, session_group, user_group, viewer_events

TOPIC = "test.realtime.account_updated"
SUBSCRIBE = "subscription { accountUpdated { id } }"

pytestmark = pytest.mark.django_db(transaction=True)


@outbox.register_handler(TOPIC)
def _publish_account_updated(row: Outbox) -> None:
    # The recipient rule: the account's owner. `extra_user_ids` stands in for a recipient
    # rule that is too broad, to prove the per-event check still protects the object.
    send_to_users(row, [int(row.aggregate_id), *row.payload.get("extra_user_ids", [])])


@strawberry.type
class Account:
    id: strawberry.ID


async def _load_account(viewer: AbstractBaseUser, event: LiveEvent) -> Account | None:
    account = await get_user_model().objects.filter(pk=event.aggregate_id).afirst()
    if account is None or account.pk != viewer.pk:
        return None
    return Account(id=strawberry.ID(str(account.pk)))


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def account_updated(self, info: Info) -> AsyncGenerator[Account, None]:
        async for account in viewer_events(info, {TOPIC}, _load_account):
            yield account


account_schema = build_schema(app_env="test", subscription=Subscription)
application = URLRouter(
    [
        # django-types only knows view functions, not Channels consumers.
        re_path(r"^graphql/?$", SubscriptionConsumer.as_asgi(schema=account_schema)),  # pyright: ignore[reportCallIssue, reportArgumentType]
    ]
)


def publish(account: AbstractBaseUser, **payload: Any) -> Outbox:
    """A committed state change: the outbox row is published by Celery (eager in tests)."""
    with transaction.atomic():
        return outbox.enqueue(TOPIC, account, payload)


async def connect(params: dict[str, Any] | None) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(
        application, "/graphql", subprotocols=["graphql-transport-ws"]
    )
    connected, _ = await communicator.connect()
    assert connected
    message: dict[str, Any] = {"type": "connection_init"}
    if params is not None:
        message["payload"] = params
    await communicator.send_json_to(message)
    return communicator


async def subscribed(user: AbstractBaseUser, token: str | None = None) -> WebsocketCommunicator:
    """A signed-in connection with `accountUpdated` running and its group joined."""
    token = token or await sync_to_async(access_token)(user)
    communicator = await connect({"authToken": token})
    assert await communicator.receive_json_from(timeout=5) == {"type": "connection_ack"}
    await communicator.send_json_to(
        {"id": "1", "type": "subscribe", "payload": {"query": SUBSCRIBE}}
    )
    layer = get_channel_layer()
    assert layer is not None
    for _ in range(100):
        if layer.groups.get(user_group(user.pk)):  # pyright: ignore[reportAttributeAccessIssue]
            return communicator
        await asyncio.sleep(0.02)
    raise AssertionError("The subscription never joined the user's group.")


def account_event(user: AbstractBaseUser) -> dict[str, Any]:
    return {
        "id": "1",
        "type": "next",
        "payload": {"data": {"accountUpdated": {"id": str(user.pk)}}},
    }


def test_outbox_event_reaches_only_its_recipient() -> None:
    alice, bob = make_user("alice"), make_user("bob")

    async def run() -> None:
        alice_ws, bob_ws = await subscribed(alice), await subscribed(bob)

        await sync_to_async(publish)(alice)

        assert await alice_ws.receive_json_from(timeout=5) == account_event(alice)
        assert await bob_ws.receive_nothing(timeout=0.3)
        await alice_ws.disconnect()
        await bob_ws.disconnect()

    async_to_sync(run)()


def test_per_event_check_drops_an_event_the_user_may_not_see() -> None:
    """Even if a recipient rule names the wrong user, the object is re-checked per event."""
    alice, bob = make_user("alice"), make_user("bob")

    async def run() -> None:
        alice_ws, bob_ws = await subscribed(alice), await subscribed(bob)

        await sync_to_async(publish)(alice, extra_user_ids=[bob.pk])

        assert await alice_ws.receive_json_from(timeout=5) == account_event(alice)
        assert await bob_ws.receive_nothing(timeout=0.3)
        await alice_ws.disconnect()
        await bob_ws.disconnect()

    async_to_sync(run)()


def test_redelivered_row_reaches_the_subscriber_once() -> None:
    alice = make_user("alice")

    async def run() -> None:
        alice_ws = await subscribed(alice)
        row = await sync_to_async(publish)(alice)
        assert await alice_ws.receive_json_from(timeout=5) == account_event(alice)

        # At-least-once delivery: the publisher may send the same row again.
        await sync_to_async(send_to_users)(row, [alice.pk])

        assert await alice_ws.receive_nothing(timeout=0.3)
        await alice_ws.disconnect()

    async_to_sync(run)()


def test_other_topics_are_not_delivered() -> None:
    alice = make_user("alice")

    async def run() -> None:
        alice_ws = await subscribed(alice)
        other = await sync_to_async(Outbox.objects.create)(
            topic="test.realtime.other", aggregate_type="auth.User", aggregate_id=str(alice.pk)
        )

        await sync_to_async(send_to_users)(other, [alice.pk])

        assert await alice_ws.receive_nothing(timeout=0.3)
        await alice_ws.disconnect()

    async_to_sync(run)()


def test_deactivated_user_gets_no_more_events() -> None:
    alice = make_user("alice")

    async def run() -> None:
        alice_ws = await subscribed(alice)
        await get_user_model().objects.filter(pk=alice.pk).aupdate(is_active=False)

        await sync_to_async(publish)(alice)

        assert await alice_ws.receive_json_from(timeout=5) == {"id": "1", "type": "complete"}
        await alice_ws.disconnect()

    async_to_sync(run)()


def _refused(params: dict[str, Any] | None) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        communicator = await connect(params)
        output = await communicator.receive_output(timeout=5)
        await communicator.wait()
        return output

    return async_to_sync(run)()


FORBIDDEN = {"type": "websocket.close", "code": 4403, "reason": "Forbidden"}


def test_connection_without_a_token_is_refused() -> None:
    assert _refused(None) == FORBIDDEN
    assert _refused({}) == FORBIDDEN
    assert _refused({"authToken": "not-a-token"}) == FORBIDDEN


def test_a_bare_user_id_from_the_client_is_not_trusted() -> None:
    alice = make_user("alice")

    assert _refused({"userId": alice.pk}) == FORBIDDEN
    assert _refused({"authToken": str(alice.pk)}) == FORBIDDEN


def test_expired_token_is_refused(settings) -> None:
    alice = make_user("alice")
    settings.ACCESS_TOKEN_TTL_SECONDS = -1

    assert _refused({"authToken": access_token(alice)}) == FORBIDDEN


def test_deactivated_user_cannot_connect() -> None:
    alice = make_user("alice", is_active=False)

    assert _refused({"authToken": access_token(alice)}) == FORBIDDEN


def test_revoked_session_cannot_connect() -> None:
    alice = make_user("alice")
    issued = sign_in(alice)
    Session.objects.filter(pk=issued.session.pk).update(revoked_at=issued.session.created_at)

    assert _refused({"authToken": issued.access_token}) == FORBIDDEN


def test_token_signed_with_another_key_is_refused(settings) -> None:
    alice = make_user("alice")
    settings.JWT_SIGNING_KEY = "some-other-key-that-is-long-enough-for-hs256"
    token = access_token(alice)
    settings.JWT_SIGNING_KEY = "the-real-key-that-is-also-long-enough-for-hs256"

    assert _refused({"authToken": token}) == FORBIDDEN


def test_refresh_token_is_not_a_connection_token() -> None:
    alice = make_user("alice")

    assert _refused({"authToken": sign_in(alice).refresh_token}) == FORBIDDEN


def test_the_served_websocket_authenticates_on_connect() -> None:
    from config.asgi import application as served

    alice = make_user("alice")

    async def handshake(params: dict[str, Any]) -> dict[str, Any]:
        communicator = WebsocketCommunicator(
            served, "/graphql", subprotocols=["graphql-transport-ws"]
        )
        connected, subprotocol = await communicator.connect()
        assert connected
        assert subprotocol == "graphql-transport-ws"
        await communicator.send_json_to({"type": "connection_init", "payload": params})
        output = await communicator.receive_output(timeout=5)
        await communicator.disconnect()
        return output

    issued = sign_in(alice, Session.Platform.WEB)
    with_access_token = async_to_sync(handshake)({"authToken": issued.access_token})
    # The web app's short-lived token from the webSocketToken mutation.
    with_ws_token = async_to_sync(handshake)({"authToken": issue_websocket_token(issued.session)})
    refused = async_to_sync(handshake)({})

    for accepted in (with_access_token, with_ws_token):
        assert accepted["type"] == "websocket.send"
        assert json.loads(accepted["text"]) == {"type": "connection_ack"}
    assert refused == FORBIDDEN


def test_expired_websocket_token_is_refused(settings) -> None:
    alice = make_user("alice")
    settings.WS_TOKEN_TTL_SECONDS = -1

    assert _refused({"authToken": issue_websocket_token(sign_in(alice).session)}) == FORBIDDEN


@strawberry.type
class _Mutation:
    @strawberry.mutation
    def touch(self) -> int:
        return 1


def test_mutations_sent_over_the_websocket_are_refused() -> None:
    """The served consumer's real context gets MUTATIONS_REQUIRE_HTTP, not a rate limit."""
    schema = build_schema(app_env="test", mutation=_Mutation)
    app = URLRouter(
        [re_path(r"^graphql/?$", SubscriptionConsumer.as_asgi(schema=schema))]  # pyright: ignore[reportCallIssue, reportArgumentType]
    )
    token = access_token(make_user("alice"))

    async def run() -> dict[str, Any]:
        communicator = WebsocketCommunicator(app, "/graphql", subprotocols=["graphql-transport-ws"])
        assert (await communicator.connect())[0]
        await communicator.send_json_to(
            {"type": "connection_init", "payload": {"authToken": token}}
        )
        assert await communicator.receive_json_from(timeout=5) == {"type": "connection_ack"}
        await communicator.send_json_to(
            {"id": "1", "type": "subscribe", "payload": {"query": "mutation { touch }"}}
        )
        message = await communicator.receive_json_from(timeout=5)
        await communicator.disconnect()
        return message

    message = async_to_sync(run)()

    errors = message["payload"] if message["type"] == "error" else message["payload"]["errors"]
    assert [e["extensions"]["code"] for e in errors] == [MUTATIONS_REQUIRE_HTTP]


def test_context_repr_hides_the_token() -> None:
    context = WSContext(request=None, ws=None, connection_params={"authToken": "secret-token"})  # pyright: ignore[reportArgumentType]

    assert "secret-token" not in repr(context)


# --- Revoked sessions close their sockets (BEAV-76) ----------------------------------

REVOKED = {"type": "websocket.close", "code": 4403}


@pytest.mark.parametrize(
    "revoke",
    [
        pytest.param(lambda issued: sessions.revoke_session(issued.refresh_token), id="sign-out"),
        pytest.param(
            lambda issued: sessions.revoke_all_sessions(issued.session.user), id="everywhere"
        ),
        pytest.param(
            lambda issued: sessions.revoke_all_from_link(
                sessions.sign_out_link_token(issued.session)
            ),
            id="this-wasnt-me",
        ),
    ],
)
def test_revoking_a_session_closes_its_open_websocket(revoke: Any) -> None:
    alice = make_user("alice")
    issued = sign_in(alice)

    async def run() -> None:
        alice_ws = await subscribed(alice, issued.access_token)

        await sync_to_async(revoke)(issued)

        assert await alice_ws.receive_output(timeout=5) == REVOKED
        await alice_ws.wait()

    async_to_sync(run)()


def test_refresh_token_reuse_closes_the_websocket(settings) -> None:
    settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS = 0
    alice = make_user("alice")
    issued = sign_in(alice)

    def reuse() -> None:
        sessions.refresh_session(issued.refresh_token)
        RefreshToken.objects.update(used_at=timezone.now() - dt.timedelta(seconds=1))
        with pytest.raises(ApiError):
            sessions.refresh_session(issued.refresh_token)

    async def run() -> None:
        alice_ws = await subscribed(alice, issued.access_token)
        await sync_to_async(reuse)()

        assert await alice_ws.receive_output(timeout=5) == REVOKED
        await alice_ws.wait()

    async_to_sync(run)()


def test_signing_out_one_device_leaves_the_others_connected() -> None:
    alice = make_user("alice")
    phone, laptop = sign_in(alice), sign_in(alice, Session.Platform.WEB)

    async def run() -> None:
        phone_ws = await subscribed(alice, phone.access_token)
        laptop_ws = await subscribed(alice, laptop.access_token)

        await sync_to_async(sessions.revoke_session)(phone.refresh_token)

        assert await phone_ws.receive_output(timeout=5) == REVOKED
        await sync_to_async(publish)(alice)
        assert await laptop_ws.receive_json_from(timeout=5) == account_event(alice)
        await laptop_ws.disconnect()

    async_to_sync(run)()


def test_a_revoked_session_gets_no_more_events_even_without_the_disconnect() -> None:
    """If the force_disconnect message is lost, each event still checks the session."""
    alice = make_user("alice")
    issued = sign_in(alice)

    async def run() -> None:
        alice_ws = await subscribed(alice, issued.access_token)
        # Revoked without _announce_revoked: no force_disconnect is sent.
        await Session.objects.filter(pk=issued.session.pk).aupdate(
            revoked_at=issued.session.created_at
        )

        await sync_to_async(publish)(alice)

        assert await alice_ws.receive_json_from(timeout=5) == {"id": "1", "type": "complete"}
        await alice_ws.disconnect()

    async_to_sync(run)()


def test_a_connection_joins_its_sessions_group_until_it_closes() -> None:
    alice = make_user("alice")
    issued = sign_in(alice)
    layer = get_channel_layer()
    assert layer is not None
    group = session_group(issued.session.pk)

    async def run() -> None:
        alice_ws = await subscribed(alice, issued.access_token)
        assert layer.groups.get(group)  # pyright: ignore[reportAttributeAccessIssue]
        await alice_ws.disconnect()

    async_to_sync(run)()
    assert not layer.groups.get(group)  # pyright: ignore[reportAttributeAccessIssue]


def test_the_connect_span_continues_the_clients_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    from config import ws

    seen: list[str] = []
    real = ws.context_from_traceparent

    def spy(traceparent: str, tracestate: str = "") -> Any:
        seen.append(traceparent)
        return real(traceparent, tracestate)

    monkeypatch.setattr(ws, "context_from_traceparent", spy)
    traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    token = access_token(make_user("alice"))

    async def run() -> None:
        communicator = await connect({"authToken": token, "traceparent": traceparent})
        assert await communicator.receive_json_from(timeout=5) == {"type": "connection_ack"}
        await communicator.disconnect()

    async_to_sync(run)()
    assert seen == [traceparent]

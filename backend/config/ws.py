"""The subscriptions WebSocket at /graphql: graphql-transport-ws over Channels.

Authentication happens once, on `connection_init` (hard rule d): `config.ws_auth` resolves
the payload to an active user and live session, and anything else is closed with 4403
before a subscription can start. Each event is then re-checked by
`core.realtime.viewer_events`, which also ends subscriptions once the session is revoked.

An authenticated connection joins its session's group (`core.realtime.session_group`) for
its whole life, so a `force_disconnect` there (accounts/sessions.py: sign-out, log out of
all devices, "This wasn't me", refresh-token reuse) closes it with 4403. The client doesn't
retry 4403 with the same token (packages/graphql/src/realtime.ts).

The client sends `traceparent` in the `connection_init` payload; the connect span
continues that trace.
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from opentelemetry import trace
from strawberry.channels import GraphQLWSConsumer
from strawberry.exceptions import ConnectionRejectionError
from strawberry.schema.base import BaseSchema
from strawberry.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL
from strawberry.types.unset import UNSET, UnsetType

from accounts.models import Session
from config import ws_auth
from core.realtime import FORCE_DISCONNECT, session_group
from core.tracing import context_from_traceparent

tracer = trace.get_tracer(__name__)

# Close code for a rejected or revoked connection: the graphql-ws "Forbidden".
WS_FORBIDDEN = 4403


@dataclass
class WSContext:
    """The context of every operation on one WebSocket connection."""

    request: GraphQLWSConsumer
    ws: GraphQLWSConsumer
    # The connection_init payload holds the token: kept out of repr so logs never show it.
    connection_params: Any = field(default=None, repr=False)
    user: AbstractBaseUser | AnonymousUser = field(default_factory=AnonymousUser, repr=False)
    session: Session | None = field(default=None, repr=False)


class SubscriptionConsumer(GraphQLWSConsumer):
    def __init__(self, schema: BaseSchema, **kwargs: Any) -> None:
        # Only graphql-transport-ws: it's what the graphql-ws client speaks. A client that
        # hasn't sent connection_init within 10 s is closed (4408).
        kwargs.setdefault("subscription_protocols", (GRAPHQL_TRANSPORT_WS_PROTOCOL,))
        kwargs.setdefault("connection_init_wait_timeout", dt.timedelta(seconds=10))
        super().__init__(schema, **kwargs)
        self.joined_group: str | None = None

    async def get_context(self, request: GraphQLWSConsumer, response: GraphQLWSConsumer) -> Any:
        return WSContext(request=request, ws=request)

    async def on_ws_connect(self, context: Any) -> UnsetType | dict[str, object] | None:
        params = context.connection_params
        traceparent = params.get("traceparent") if isinstance(params, dict) else None
        parent = context_from_traceparent(traceparent if isinstance(traceparent, str) else "")
        with tracer.start_as_current_span("graphql.ws.connect", context=parent) as span:
            signed_in = await ws_auth.resolve_connection_session(params)
            span.set_attribute("ws.authenticated", signed_in is not None)
        if signed_in is None:
            raise ConnectionRejectionError
        context.user, context.session = signed_in
        if self.channel_layer is not None:
            self.joined_group = session_group(context.session.pk)
            await self.channel_layer.group_add(self.joined_group, self.channel_name)
        return UNSET

    async def dispatch(self, message: Any) -> None:
        if message.get("type") == FORCE_DISCONNECT:
            await self.close(code=WS_FORBIDDEN)
            # Stop the protocol handler now, ending its subscriptions, rather than when
            # the server reports the socket closed.
            self.message_queue.put_nowait({"message": None, "disconnected": True})
            return
        await super().dispatch(message)

    async def websocket_disconnect(self, message: Any) -> None:
        if self.joined_group is not None and self.channel_layer is not None:
            await self.channel_layer.group_discard(self.joined_group, self.channel_name)
            self.joined_group = None
        await super().websocket_disconnect(message)

"""Who is on the other end of a subscriptions WebSocket.

The client sends `{"authToken": "<token>"}` in the graphql-ws `connection_init` payload:
mobile sends its access token, the web app a short-lived WebSocket token from the
`webSocketToken` mutation (its access token is in an httpOnly cookie). Both are JWTs of
accounts/sessions.py, accepted only while their session is live and the user active.
A bare user ID from the client is never trusted.
"""

from typing import Any

from accounts import sessions
from accounts.models import Session, User

CONNECTION_PARAM = "authToken"
_ACCEPTED_TYPES = (sessions.ACCESS, sessions.WEBSOCKET)


async def resolve_connection_session(params: Any) -> tuple[User, Session] | None:
    """The active user and live session the `connection_init` payload proves, or None."""
    token = params.get(CONNECTION_PARAM) if isinstance(params, dict) else None
    if not isinstance(token, str):
        return None
    return await sessions.authenticate(token, _ACCEPTED_TYPES)

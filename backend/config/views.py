"""The GraphQL HTTP view and the context every resolver receives."""

from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import HttpRequest, HttpResponse
from strawberry.django.context import StrawberryDjangoContext
from strawberry.django.views import AsyncGraphQLView

from accounts import sessions
from accounts.models import Session

_BEARER = "bearer "


@dataclass
class Context(StrawberryDjangoContext):
    # Resolved once per request from the bearer token, so async resolvers never touch the
    # database for it.
    user: AbstractBaseUser | AnonymousUser
    # The session the access token belongs to; None when signed out.
    session: Session | None = None


async def authenticate_request(request: HttpRequest) -> tuple[AbstractBaseUser, Session] | None:
    """The user and session of the request's `Authorization: Bearer` access token, or None.

    Only the bearer token counts. A Django session cookie (the admin's) is ignored: the
    endpoint is exempt from Django's CSRF check, so a cookie must never authenticate it.
    An invalid or expired token is treated as signed out; the client refreshes and retries
    when a resolver answers UNAUTHENTICATED.
    """
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith(_BEARER):
        return None
    return await sessions.authenticate(header[len(_BEARER) :].strip(), (sessions.ACCESS,))


class GraphQLView(AsyncGraphQLView):
    async def get_context(self, request: HttpRequest, response: HttpResponse) -> Any:
        signed_in = await authenticate_request(request)
        if signed_in is None:
            return Context(request=request, response=response, user=AnonymousUser())
        user, session = signed_in
        return Context(request=request, response=response, user=user, session=session)

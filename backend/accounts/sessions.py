"""Sessions: short JWT access tokens and rotating, single-use refresh tokens.

- The access token (ACCESS_TOKEN_TTL_SECONDS, about 15 minutes) is a JWT sent as
  `Authorization: Bearer` on GraphQL HTTP requests and, on mobile, as `authToken` in the
  graphql-ws `connection_init`. It names the user (`sub`) and the session (`sid`), and is
  accepted only while that session is live, so revoking a session ends its tokens at once.
- The WebSocket token (WS_TOKEN_TTL_SECONDS, about a minute) is the same JWT with
  `typ: ws`. The web app's access token sits in an httpOnly cookie its page code can't
  read, so the page asks for one of these (`webSocketToken`) to open a subscription socket.
  It is accepted only in `connection_init`, never as a bearer token.
- The refresh token is a random string stored as a hash (`RefreshToken`). Refreshing
  marks it used and issues the next one. Presenting a used token again is reuse: someone
  else may hold a copy, so the whole session is revoked. One exception: within
  REFRESH_TOKEN_REUSE_GRACE_SECONDS of its use the token still refreshes (to a new token,
  revoking nothing), because a browser's parallel requests can race to refresh with the
  same cookie.

Revoking a session (sign-out, "Log out of all devices", the "This wasn't me" link,
refresh-token reuse) goes through `_announce_revoked`. After the commit it sends
`session_revoked`, so other apps drop what they tied to the session (notifications
deletes the device's push tokens), and a `force_disconnect` to the session's Channels
group, which closes its open WebSockets (config/ws.py).

New-device notice (BEAV-76): every sign-in hands the client a random device token
(`IssuedTokens.device_token`), which it keeps (an httpOnly cookie on web, SecureStore on
mobile) and sends back on its next sign-in. A sign-in to an account that has signed in
before, without a token one of its earlier sessions was started with, writes a
`session.new_device` outbox row. The client chooses its platform and device name, so
they never make a device known; only the token, which can't be guessed, does. Its
handler emails "New login to TaskBeavo from iPhone, Munich" with a "This wasn't me" link
(accounts/outbox_handlers.py) that logs out everywhere. This covers email codes, Google
and Apple alike, since every sign-in starts here; the first session of a new account
sends nothing.
"""

import dataclasses
import datetime as dt
import hashlib
import logging
import re
import secrets
import uuid
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any

import jwt
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.dispatch import Signal
from django.utils import timezone

from accounts import geo
from accounts.models import RefreshToken, Session, User
from core import outbox
from core.errors import ApiError, ErrorCode
from core.realtime import FORCE_DISCONNECT, session_group

logger = logging.getLogger(__name__)

ACCESS = "access"
WEBSOCKET = "ws"
_ALGORITHM = "HS256"
_ISSUER = "taskbeavo"
_AUDIENCE = "taskbeavo-api"
_SIGN_OUT_LINK_SALT = "taskbeavo.accounts.sign-out-link.v1"
NEW_DEVICE_TOPIC = "session.new_device"
# What start_session hands out (token_urlsafe(32)); anything else is replaced.
_DEVICE_TOKEN = re.compile(r"[A-Za-z0-9_-]{40,64}")

# Sent once a revocation commits, with `session_ids`: a list of revoked Session IDs.
session_revoked = Signal()


@dataclass(frozen=True)
class IssuedTokens:
    session: Session
    access_token: str
    access_token_expires_at: dt.datetime
    refresh_token: str
    refresh_token_expires_at: dt.datetime
    # Set by sign-in only (None on refresh): the client keeps it and sends it next time.
    device_token: str | None = None


def start_session(
    user: User,
    platform: str,
    device_name: str = "",
    client_ip: str = "",
    device_token: str | None = None,
) -> IssuedTokens:
    """A new session for `user` with its first access and refresh tokens.

    `device_token` is the one this device kept from an earlier sign-in, if any; a
    malformed one is ignored. The result carries the token to keep (the same one, or a
    new one). Emails a new-device notice (after the commit, through the outbox) when the
    account has signed in before but never with this token.
    """
    location = geo.coarse_location(client_ip, user.locale) if client_ip else ""
    with transaction.atomic():
        recent = Session.objects.filter(created_at__gte=_device_token_cutoff())
        if (
            device_token is None
            or not _DEVICE_TOKEN.fullmatch(device_token)
            or not recent.filter(device_hash=_hash(device_token)).exists()
        ):
            # Unknown, or unused for DEVICE_TOKEN_MAX_AGE_DAYS: a fresh token, so none
            # lives longer than that without a sign-in renewing it.
            device_token = secrets.token_urlsafe(32)
        device_hash = _hash(device_token)
        # last_login, not the sessions: ended sessions are purged after 6 months, which
        # must not make a sign-in look like the account's first.
        signed_in_before = (
            User.objects.filter(pk=user.pk, last_login__isnull=False).exists()
            or Session.objects.filter(user=user).exists()
        )
        is_new_device = (
            signed_in_before and not recent.filter(user=user, device_hash=device_hash).exists()
        )
        user.last_login = timezone.now()
        User.objects.filter(pk=user.pk).update(last_login=user.last_login)
        session = Session.objects.create(
            user=user,
            platform=platform,
            device_name=device_name.strip()[:100],
            device_hash=device_hash,
            location=location,
        )
        if is_new_device:
            outbox.enqueue(NEW_DEVICE_TOPIC, session)
        return dataclasses.replace(_issue(session), device_token=device_token)


def refresh_session(refresh_token: str) -> IssuedTokens:
    """Rotate `refresh_token`: the next access and refresh tokens of the same session.

    Raises SESSION_EXPIRED for an unknown, expired or reused token, a revoked session or
    an inactive user. Reuse after the grace period revokes the session first.
    """
    now = timezone.now()
    with transaction.atomic():
        token = (
            RefreshToken.objects.select_for_update()
            .select_related("session__user")
            .filter(token_hash=_hash(refresh_token))
            .first()
        )
        if token is None:
            raise _session_expired()
        session = token.session
        if session.revoked_at is not None or not session.user.is_active:
            raise _session_expired()
        if token.expires_at <= now:
            raise _session_expired()
        grace = dt.timedelta(seconds=settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS)
        if token.used_at is not None and now - token.used_at > grace:
            # Reuse: revoke the family. The error is raised after the block so this commits.
            session.revoked_at = now
            session.save(update_fields=["revoked_at"])
            _announce_revoked([session.pk])
        else:
            if token.used_at is None:
                token.used_at = now
                token.save(update_fields=["used_at"])
            session.last_used_at = now
            session.save(update_fields=["last_used_at"])
            return _issue(session)
    raise _session_expired()


def is_known_device(email: str, device_token: str | None) -> bool:
    """Whether `device_token` is a device of the active account with this address (a
    session in the last DEVICE_TOKEN_MAX_AGE_DAYS was started with it).

    One query whether or not the address has an account, so its timing can't tell.
    """
    if device_token is None or not _DEVICE_TOKEN.fullmatch(device_token):
        return False
    return Session.objects.filter(
        user__email=email,
        user__is_active=True,
        device_hash=_hash(device_token),
        created_at__gte=_device_token_cutoff(),
    ).exists()


def device_digest(device_token: str) -> str:
    """The stored form of a device token (Session.device_hash)."""
    return _hash(device_token)


def _device_token_cutoff() -> dt.datetime:
    """Sessions started before this no longer make their device token known.

    Decision 2026-11-04 (BEAV-76): the device token (web cookie tb_device, mobile
    SecureStore) is a strictly necessary security identifier (ePrivacy Art. 5(3)
    exemption, WP194 "user-centric security"), so it has a limited lifetime: 6 months
    from the last sign-in that used it. Each such sign-in renews it. It is used only to
    recognise known devices for the new-device email.
    """
    return timezone.now() - dt.timedelta(days=settings.DEVICE_TOKEN_MAX_AGE_DAYS)


def revoke_session(refresh_token: str) -> None:
    """Sign out: revoke the session `refresh_token` belongs to. Unknown tokens are ignored."""
    with transaction.atomic():
        live = Session.objects.filter(
            refresh_tokens__token_hash=_hash(refresh_token), revoked_at__isnull=True
        )
        session_ids = list(live.values_list("pk", flat=True))
        Session.objects.filter(pk__in=session_ids).update(revoked_at=timezone.now())
        _announce_revoked(session_ids)


def revoke_sessions(session_ids: Iterable[uuid.UUID]) -> int:
    """Revoke these sessions (support, in the admin); live ones only. Returns how many."""
    with transaction.atomic():
        live = Session.objects.select_for_update().filter(
            pk__in=list(session_ids), revoked_at__isnull=True
        )
        revoked = list(live.values_list("pk", flat=True))
        Session.objects.filter(pk__in=revoked).update(revoked_at=timezone.now())
        _announce_revoked(revoked)
    return len(revoked)


def revoke_all_sessions(user: User) -> int:
    """Log out of all devices: revoke every live session of `user`. Returns how many.

    It also forgets every device the account has signed in from: whoever prompted this
    (a stranger in "This wasn't me", or a suspicion) may hold one of their device tokens,
    so the next sign-in from any device sends the new-device email again.
    """
    with transaction.atomic():
        live = Session.objects.select_for_update().filter(user=user, revoked_at__isnull=True)
        session_ids = list(live.values_list("pk", flat=True))
        Session.objects.filter(pk__in=session_ids).update(revoked_at=timezone.now())
        Session.objects.filter(user=user).exclude(device_hash="").update(device_hash="")
        _announce_revoked(session_ids)
    return len(session_ids)


def sign_out_link_token(session: Session) -> str:
    """The token of the "This wasn't me" link in the new-device email about `session`."""
    return signing.dumps({"sid": str(session.pk)}, salt=_SIGN_OUT_LINK_SALT)


def revoke_all_from_link(token: str) -> int:
    """ "This wasn't me": log the account of the link's session out of all devices.

    Raises SIGN_OUT_LINK_EXPIRED for a forged token, one older than
    SIGN_OUT_LINK_MAX_AGE_SECONDS or one whose account is gone. Using a link again just
    logs out again, which ends any session started since.
    """
    try:
        data = signing.loads(
            token, salt=_SIGN_OUT_LINK_SALT, max_age=settings.SIGN_OUT_LINK_MAX_AGE_SECONDS
        )
        session_id = uuid.UUID(str(data["sid"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise _link_expired() from None
    session = Session.objects.select_related("user").filter(pk=session_id).first()
    if session is None:
        raise _link_expired()
    return revoke_all_sessions(session.user)


def _announce_revoked(session_ids: list[uuid.UUID]) -> None:
    """After the commit: `session_revoked`, then close the sessions' open WebSockets."""
    if session_ids:

        def announce() -> None:
            session_revoked.send(sender=Session, session_ids=session_ids)
            _disconnect_sockets(session_ids)

        transaction.on_commit(announce)


def _disconnect_sockets(session_ids: list[uuid.UUID]) -> None:
    """Send `force_disconnect` to each session's group; its open WebSockets close. Best
    effort: a socket whose session is revoked also stops getting events (core.realtime)
    and can't reconnect with its token."""
    layer = get_channel_layer()
    if layer is None:
        return
    for session_id in session_ids:
        try:
            async_to_sync(layer.group_send)(session_group(session_id), {"type": FORCE_DISCONNECT})
        except Exception as exc:  # the revocation itself is committed: don't fail it
            logger.warning("force_disconnect not sent: %s", type(exc).__name__)


def issue_websocket_token(session: Session) -> str:
    """A short-lived token for one graphql-ws `connection_init`, for the web app."""
    token, _ = _encode(session, WEBSOCKET, settings.WS_TOKEN_TTL_SECONDS)
    return token


async def authenticate(token: str, token_types: Collection[str]) -> tuple[User, Session] | None:
    """The active user and live session a token of one of `token_types` proves, or None."""
    claims = _decode(token)
    if claims is None or claims.get("typ") not in token_types:
        return None
    try:
        session_id = uuid.UUID(str(claims.get("sid")))
        user_id = int(claims.get("sub", ""))
    except (TypeError, ValueError):
        return None
    session = (
        await Session.objects.select_related("user")
        .filter(pk=session_id, user_id=user_id, revoked_at__isnull=True, user__is_active=True)
        .afirst()
    )
    if session is None:
        return None
    return session.user, session


def _issue(session: Session) -> IssuedTokens:
    access_token, access_expires = _encode(session, ACCESS, settings.ACCESS_TOKEN_TTL_SECONDS)
    refresh_token = secrets.token_urlsafe(32)
    refresh_expires = timezone.now() + dt.timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
    RefreshToken.objects.create(
        session=session, token_hash=_hash(refresh_token), expires_at=refresh_expires
    )
    return IssuedTokens(
        session=session,
        access_token=access_token,
        access_token_expires_at=access_expires,
        refresh_token=refresh_token,
        refresh_token_expires_at=refresh_expires,
    )


def _encode(session: Session, token_type: str, ttl_seconds: int) -> tuple[str, dt.datetime]:
    now = timezone.now()
    expires = now + dt.timedelta(seconds=ttl_seconds)
    claims = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": str(session.user_id),  # pyright: ignore[reportAttributeAccessIssue]
        "sid": str(session.pk),
        "typ": token_type,
        "jti": secrets.token_urlsafe(8),
        "iat": now,
        "exp": expires,
    }
    return jwt.encode(claims, settings.JWT_SIGNING_KEY, algorithm=_ALGORITHM), expires


def _decode(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(
            token,
            settings.JWT_SIGNING_KEY,
            algorithms=[_ALGORITHM],
            audience=_AUDIENCE,
            issuer=_ISSUER,
            options={"require": ["exp", "iat", "sub", "sid", "typ"]},
        )
    except jwt.InvalidTokenError:
        return None


def _hash(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode()).hexdigest()


def _session_expired() -> ApiError:
    return ApiError(ErrorCode.SESSION_EXPIRED, "Sign in again.")


def _link_expired() -> ApiError:
    return ApiError(ErrorCode.SIGN_OUT_LINK_EXPIRED, "The link is invalid or too old.")

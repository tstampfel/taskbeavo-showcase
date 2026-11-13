"""Which push tokens are live, and dropping the ones that aren't.

A token is live while the session that registered it is: not revoked (sign-out, refresh
token reuse) and still holding an unexpired refresh token. Only live tokens get pushes
and count as "has a push device" for the email fallback. The others are personal data
without a purpose, so they are deleted: at once when a session is revoked
(`accounts.sessions.session_revoked`), and daily for sessions that simply expired.
"""

from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from accounts.models import RefreshToken
from notifications.models import PushToken


def live_tokens() -> QuerySet[PushToken]:
    refresh = RefreshToken.objects.filter(
        session=OuterRef("session"), expires_at__gt=timezone.now()
    )
    return PushToken.objects.filter(session__revoked_at__isnull=True).filter(Exists(refresh))


def live_tokens_of(user: AbstractBaseUser | int) -> QuerySet[PushToken]:
    user_id = user if isinstance(user, int) else user.pk
    return live_tokens().filter(user_id=user_id)


def has_live_device(user: AbstractBaseUser) -> bool:
    return live_tokens_of(user).exists()


def delete_dead_tokens(user: AbstractBaseUser | int | None = None) -> int:
    """Delete tokens whose session is revoked or expired; all users unless `user` is given."""
    dead = PushToken.objects.exclude(pk__in=live_tokens().values("pk"))
    if user is not None:
        dead = dead.filter(user_id=user if isinstance(user, int) else user.pk)
    deleted, _ = dead.delete()
    return deleted


def forget_revoked_sessions(sender: Any, session_ids: list[Any], **kwargs: Any) -> None:
    """`session_revoked` receiver: a signed-out device stops getting the account's pushes."""
    PushToken.objects.filter(session_id__in=session_ids).delete()

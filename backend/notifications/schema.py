"""GraphQL API of push tokens and notification preferences."""

import re
from enum import Enum
from typing import Any

import strawberry
from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from accounts.models import Session
from core.errors import ApiError, ErrorCode
from notifications import preferences
from notifications.devices import live_tokens
from notifications.models import Category, Channel, PushToken

# Expo push tokens look like "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]".
_EXPO_TOKEN = re.compile(r"Expo(nent)?PushToken\[[A-Za-z0-9_-]{8,200}\]")


@strawberry.enum(description="What a notification is about.")
class NotificationCategory(Enum):
    BOOKING = Category.BOOKING.value
    PAYMENT = Category.PAYMENT.value
    OFFERS = Category.OFFERS.value
    MESSAGES = Category.MESSAGES.value


@strawberry.enum(description="How a notification reaches the user.")
class NotificationChannel(Enum):
    PUSH = "PUSH"
    EMAIL = "EMAIL"


_CHANNELS = {NotificationChannel.PUSH: Channel.PUSH, NotificationChannel.EMAIL: Channel.EMAIL}


@strawberry.enum(description="The mobile platform of a push device.")
class PushPlatform(Enum):
    IOS = PushToken.Platform.IOS.value
    ANDROID = PushToken.Platform.ANDROID.value


@strawberry.type(name="NotificationPreference")
class NotificationPreferenceType:
    id: strawberry.ID = strawberry.field(description='"<category>:<channel>", e.g. "OFFERS:PUSH".')
    category: NotificationCategory
    channel: NotificationChannel
    enabled: bool
    locked: bool = strawberry.field(
        description="Booking and payment notifications can't be switched off."
    )

    @classmethod
    def of(cls, preference: preferences.Preference) -> "NotificationPreferenceType":
        channel = NotificationChannel(preference.channel.name)
        return cls(
            id=strawberry.ID(f"{preference.category}:{channel.value}"),
            category=NotificationCategory(preference.category.value),
            channel=channel,
            enabled=preference.enabled,
            locked=preference.locked,
        )


@strawberry.type(description="A device registered for push notifications.")
class PushDevice:
    id: strawberry.ID
    platform: PushPlatform


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            "The signed-in user's notification settings: every category on every channel. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def notification_preferences(self, info: Info) -> list[NotificationPreferenceType]:
        session = _signed_in_session(info)
        found = await sync_to_async(preferences.preferences)(session.user)
        return [NotificationPreferenceType.of(preference) for preference in found]


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Switch a category on or off for one channel. Errors: UNAUTHENTICATED, "
            "NOTIFICATION_PREFERENCE_LOCKED (booking and payment)."
        )
    )
    async def set_notification_preference(
        self,
        info: Info,
        category: NotificationCategory,
        channel: NotificationChannel,
        enabled: bool,
    ) -> NotificationPreferenceType:
        session = _signed_in_session(info)
        try:
            saved = await sync_to_async(preferences.set_preference)(
                session.user, Category(category.value), _CHANNELS[channel], enabled
            )
        except preferences.PreferenceLockedError:
            raise ApiError(
                ErrorCode.NOTIFICATION_PREFERENCE_LOCKED, "This category can't be switched off."
            ) from None
        return NotificationPreferenceType.of(saved)

    @strawberry.mutation(
        description=(
            "Register this device's Expo push token for the signed-in session. A token "
            "still live for another account is refused: sign out there first, which "
            "removes it. Errors: UNAUTHENTICATED, INVALID_PUSH_TOKEN."
        )
    )
    async def register_push_token(
        self, info: Info, token: str, platform: PushPlatform
    ) -> PushDevice:
        session = _signed_in_session(info)
        if not _EXPO_TOKEN.fullmatch(token):
            raise ApiError(ErrorCode.INVALID_PUSH_TOKEN, "Not an Expo push token.")
        try:
            device = await sync_to_async(_register)(session, token, platform.value)
        except TokenTakenError:
            raise ApiError(
                ErrorCode.INVALID_PUSH_TOKEN, "Token in use by another account."
            ) from None
        return PushDevice(id=strawberry.ID(str(device.pk)), platform=PushPlatform(device.platform))

    @strawberry.mutation(
        description=(
            "Stop pushes to this token, e.g. when the user turns notifications off on the "
            "device. Returns whether the token was registered to the signed-in user. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def unregister_push_token(self, info: Info, token: str) -> bool:
        session = _signed_in_session(info)
        deleted, _ = await PushToken.objects.filter(user=session.user, token=token).adelete()
        return deleted > 0


class TokenTakenError(Exception):
    """The token is a live device of another account."""


def _register(session: Session, token: str, platform: str) -> PushToken:
    with transaction.atomic():
        current = PushToken.objects.select_for_update().filter(token=token).first()
        # A device moves to another account only after its old session ended (sign-out
        # deletes the token, expiry makes it dead); otherwise anyone who learnt a token
        # could redirect that device's pushes, or silence them for its owner.
        if (
            current is not None
            and current.user_id != session.user.pk
            and live_tokens().filter(pk=current.pk).exists()
        ):
            raise TokenTakenError
        device, _ = PushToken.objects.update_or_create(
            token=token,
            defaults={
                "user": session.user,
                "session": session,
                "platform": platform,
                "registered_at": timezone.now(),
            },
        )
        return device


def _signed_in_session(info: Info[Any, Any]) -> Session:
    session = getattr(info.context, "session", None)
    if not isinstance(session, Session):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return session

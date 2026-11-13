"""Per-user notification preferences.

Offers and chat messages can be switched off per channel. Booking and payment messages
can't: they are part of the service (plan, GDPR section "Correction, objection and
consent"). Marketing will be a separate opt-in with its own consent record.
"""

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser

from notifications.models import MANDATORY_CATEGORIES, Category, Channel, NotificationPreference


class PreferenceLockedError(Exception):
    """Someone tried to switch off a booking or payment notification."""


@dataclass(frozen=True)
class Preference:
    category: Category
    channel: Channel
    enabled: bool
    locked: bool


def is_enabled(user: AbstractBaseUser, category: Category, channel: Channel) -> bool:
    if category in MANDATORY_CATEGORIES:
        return True
    stored = (
        NotificationPreference.objects.filter(user=user, category=category, channel=channel)
        .values_list("enabled", flat=True)
        .first()
    )
    return True if stored is None else stored


def preferences(user: AbstractBaseUser) -> list[Preference]:
    """Every category on every channel, with the user's choice or the default (on)."""
    stored = {
        (row.category, row.channel): row.enabled
        for row in NotificationPreference.objects.filter(user=user)
    }
    return [
        Preference(
            category=category,
            channel=channel,
            enabled=category in MANDATORY_CATEGORIES or stored.get((category, channel), True),
            locked=category in MANDATORY_CATEGORIES,
        )
        for category in Category
        for channel in Channel
    ]


def set_preference(
    user: AbstractBaseUser, category: Category, channel: Channel, enabled: bool
) -> Preference:
    if category in MANDATORY_CATEGORIES:
        raise PreferenceLockedError
    NotificationPreference.objects.update_or_create(
        user=user, category=category, channel=channel, defaults={"enabled": enabled}
    )
    return Preference(category=category, channel=channel, enabled=enabled, locked=False)

"""The recipient's language for emails, push, SMS and PDFs (hard rule i).

Backend text is rendered in the language of whoever receives it, never the language
of the request that triggered it: Ana in Vienna gets German and Mama Kata's SMS is
Croatian, for the same booking.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from django.utils import translation

SUPPORTED_LOCALES = ("hr", "en", "de", "fr")
# Croatian is the default for helpers and on-site contacts.
DEFAULT_LOCALE = "hr"

logger = logging.getLogger(__name__)


class LocaleRecipient(Protocol):
    """What a message recipient must provide: its saved language as `locale`.

    A user's `locale` field satisfies it, and so must an on-site contact
    (CARE_CONTACT), e.g. with a `locale` field or property over its language.
    `None` or "" means no language was saved.
    """

    @property
    def locale(self) -> str | None: ...


def recipient_locale(recipient: LocaleRecipient) -> str:
    """The recipient's `locale` as one of hr, en, de or fr.

    Regional and mixed-case values map to their language ("de-AT" → "de"). A missing
    or unsupported value falls back to Croatian and logs a warning holding only the
    recipient's model type, its `pk` if it has one, and the reason ("missing" or
    "unsupported"): never the value or any personal data.
    """
    stored = (recipient.locale or "").strip()
    language = stored.lower().replace("_", "-").split("-")[0]
    if language in SUPPORTED_LOCALES:
        return language
    reason = "unsupported" if stored else "missing"
    recipient_type = type(recipient).__name__
    recipient_id = getattr(recipient, "pk", None)
    logger.warning(
        "Recipient locale %s for %s %s; falling back to %s",
        reason,
        recipient_type,
        recipient_id,
        DEFAULT_LOCALE,
        extra={"reason": reason, "recipient_type": recipient_type, "recipient_id": recipient_id},
    )
    return DEFAULT_LOCALE


@contextmanager
def in_recipient_locale(recipient: LocaleRecipient) -> Iterator[str]:
    """Activate the recipient's language for gettext and templates; yields the locale."""
    locale = recipient_locale(recipient)
    with translation.override(locale):
        yield locale

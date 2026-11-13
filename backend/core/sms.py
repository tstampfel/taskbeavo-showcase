"""Text messages (SMS) and the interface that sends them.

SMS is kept for one-time codes and the few reminders to on-site contacts without a
smartphone (plan: "Real-time updates"); it costs money per message, so every caller
rate-limits its own sends. Bodies are rendered by the caller in the recipient's language
(`core.i18n.in_recipient_locale`) and stay short, plain GSM text where possible.

SMS_BACKEND names the backend class: `core.sms.ConsoleSmsBackend` locally (prints the
message with the number masked), `core.sms.LocmemSmsBackend` in tests (keeps messages in
`core.sms.outbox`), and `core.infobip.InfobipSmsBackend` when deployed.

A phone number is personal data: backends never log it, put it in an exception message
or a span attribute.
"""

import functools
import logging
from dataclasses import dataclass
from typing import Protocol

import phonenumbers
from django.conf import settings
from django.utils.module_loading import import_string

from core.errors import SafeMessageError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmsMessage:
    to: str  # E.164, e.g. "+385912345678"
    text: str
    sender: str  # an alphanumeric sender ID or a number, from `sender_for`


class SmsSendError(SafeMessageError):
    """The provider didn't accept the message. The message holds no number or text."""


class SmsBackend(Protocol):
    def send(self, message: SmsMessage) -> None:
        """Hand the message to the provider. Raises SmsSendError when it is refused."""
        ...


def mask_phone(e164: str) -> str:
    """ "+385912345678" → "+385 ••• 678": enough for its owner to recognise it, no more."""
    try:
        number = phonenumbers.parse(e164)
    except phonenumbers.NumberParseException:
        return "•••"
    return f"+{number.country_code} ••• {str(number.national_number)[-3:]}"


class ConsoleSmsBackend:
    """Local development: logs the message, with the number masked, instead of sending it."""

    def send(self, message: SmsMessage) -> None:
        logger.info("SMS to %s: %s", mask_phone(message.to), message.text)


outbox: list[SmsMessage] = []


class LocmemSmsBackend:
    """Tests: keeps every message in `core.sms.outbox`, like Django's mail.outbox."""

    def send(self, message: SmsMessage) -> None:
        outbox.append(message)


# North American Numbering Plan: carriers there drop alphanumeric senders silently.
_NANP_COUNTRY_CODE = 1


def sender_for(region: str) -> str | None:
    """The sender for a number of `region` (ISO 3166-1 alpha-2), or None if it can't get SMS.

    INFOBIP_SENDER_BY_REGION wins; otherwise the alphanumeric INFOBIP_SENDER, except for
    +1 countries, which need a numeric sender configured first.
    """
    configured = settings.INFOBIP_SENDER_BY_REGION.get(region.upper())
    if configured:
        return configured
    if phonenumbers.country_code_for_region(region) == _NANP_COUNTRY_CODE:
        return None
    return settings.INFOBIP_SENDER


@functools.cache
def _backend(path: str) -> SmsBackend:
    return import_string(path)()


def send_sms(to: str, text: str, sender: str) -> None:
    """Send one SMS to an E.164 number. Raises SmsSendError if the provider refuses it."""
    _backend(settings.SMS_BACKEND).send(SmsMessage(to=to, text=text, sender=sender))

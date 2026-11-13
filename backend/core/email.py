"""Transactional emails rendered in the recipient's language, and the interface that sends them.

Each email is a folder `core/templates/email/<name>/` holding `subject.txt`,
`body.html` (extends `email/base.html`) and `body.txt` (extends `email/base.txt`).
Templates translate with `{% translate %}` / `{% blocktranslate %}`; the strings live
in backend/locale/<lang>/LC_MESSAGES/django.po.

`send_email` goes through Django's EMAIL_BACKEND: the console locally, memory in tests,
and Resend when deployed (core/resend.py).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from core.i18n import LocaleRecipient, in_recipient_locale


@dataclass(frozen=True)
class RenderedEmail:
    locale: str
    subject: str
    html: str
    text: str


def render_email(
    name: str, recipient: LocaleRecipient, context: Mapping[str, Any] | None = None
) -> RenderedEmail:
    """Render email `name` in the recipient's language (see `core.i18n.recipient_locale`).

    `context` must not carry anything the recipient may not see; the HTML part escapes
    it, the text part doesn't need to.
    """
    with in_recipient_locale(recipient) as locale:
        ctx = {**(context or {}), "locale": locale}
        subject = render_to_string(f"email/{name}/subject.txt", ctx)
        return RenderedEmail(
            locale=locale,
            # Header injection guard: a subject is always one line.
            subject=" ".join(subject.split()),
            html=render_to_string(f"email/{name}/body.html", ctx),
            text=render_to_string(f"email/{name}/body.txt", ctx).strip() + "\n",
        )


@dataclass(frozen=True)
class Attachment:
    """A text file sent with an email, such as the legal document a confirmation names."""

    filename: str
    content: str
    mimetype: str = "text/plain"


class Email(EmailMultiAlternatives):
    """An email with an optional idempotency key, which the Resend backend passes on."""

    idempotency_key: str | None = None


def send_email(
    to: str,
    email: RenderedEmail,
    *,
    idempotency_key: str | None = None,
    attachments: Sequence[Attachment] = (),
) -> str:
    """Send a rendered email to one address. Raises if the backend can't send it.

    `idempotency_key` (e.g. from `core.outbox.outbox_key`) makes a retried send a no-op
    with Resend, for 24 hours; other backends ignore it. Returns the provider's ID for the
    email (Resend's), or "" when the backend gives none.
    """
    message = Email(
        subject=email.subject,
        body=email.text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
        headers={"Content-Language": email.locale},
    )
    message.idempotency_key = idempotency_key
    message.attach_alternative(email.html, "text/html")
    for attachment in attachments:
        message.attach(attachment.filename, attachment.content, attachment.mimetype)
    message.send()
    return getattr(message, "provider_message_id", "")

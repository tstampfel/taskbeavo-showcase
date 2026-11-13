"""Django email backend that sends through Resend's HTTP API (https://resend.com/docs/api-reference/emails/send-email).

Set `EMAIL_BACKEND=core.resend.ResendEmailBackend` and `RESEND_API_KEY`. Each message is
one API call bounded by `EMAIL_TIMEOUT`. A `core.email.Email` with an `idempotency_key`
sends it as Resend's `Idempotency-Key`, so a retried send isn't delivered twice. The ID
Resend gives the email is set on the message as `provider_message_id`, as proof of the
send (`core.email.send_email` returns it).

Errors carry the HTTP status only: Resend's error body can quote the recipient.
"""

import base64
from collections.abc import Iterable
from typing import Any

import requests
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend

API_URL = "https://api.resend.com/emails"


class ResendError(Exception):
    """Resend refused or failed a send. The message is the HTTP status code."""


class ResendEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages: Iterable[EmailMessage]) -> int:
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
            except (requests.RequestException, ResendError):
                if not self.fail_silently:
                    raise
            else:
                sent += 1
        return sent

    def _send(self, message: EmailMessage) -> None:
        headers = {
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        }
        key = getattr(message, "idempotency_key", None)
        if key:
            headers["Idempotency-Key"] = key
        response = requests.post(
            API_URL, json=_payload(message), headers=headers, timeout=settings.EMAIL_TIMEOUT
        )
        if response.status_code >= 400:
            raise ResendError(str(response.status_code))
        message.provider_message_id = _message_id(response)  # type: ignore[attr-defined]


def _message_id(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    found = body.get("id") if isinstance(body, dict) else None
    return found if isinstance(found, str) else ""


def _payload(message: EmailMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "from": message.from_email,
        "to": list(message.to),
        "subject": message.subject,
        "text": message.body,
    }
    if message.cc:
        payload["cc"] = list(message.cc)
    if message.bcc:
        payload["bcc"] = list(message.bcc)
    if message.reply_to:
        payload["reply_to"] = list(message.reply_to)
    for content, mimetype in getattr(message, "alternatives", []):
        if mimetype == "text/html":
            payload["html"] = content
    if message.extra_headers:
        payload["headers"] = dict(message.extra_headers)
    if message.attachments:
        payload["attachments"] = [_attachment(item) for item in message.attachments]
    return payload


def _attachment(item: Any) -> dict[str, str]:
    """Resend takes each attachment as a file name and base64 content."""
    filename, content, _mimetype = item
    data = content.encode() if isinstance(content, str) else bytes(content)
    return {"filename": filename, "content": base64.b64encode(data).decode("ascii")}

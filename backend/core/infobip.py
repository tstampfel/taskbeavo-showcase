"""SMS backend that sends through Infobip's HTTP API (https://www.infobip.com/docs/api/channels/sms).

Set `SMS_BACKEND=core.infobip.InfobipSmsBackend`, `INFOBIP_BASE_URL` (the account's own
API host, e.g. https://xxxxx.api.infobip.com) and `INFOBIP_API_KEY`. Each message names
its sender (`core.sms.sender_for`: `INFOBIP_SENDER`, `INFOBIP_SENDER_BY_REGION`). Each
message is one API call bounded by `SMS_TIMEOUT_SECONDS`.

Errors carry the HTTP status or Infobip's status group only: the response body quotes
the recipient's number.
"""

from typing import Any

import requests
from django.conf import settings

from core.sms import SmsMessage, SmsSendError

SEND_PATH = "/sms/2/text/advanced"
# Infobip's status groups for a message it refused outright; PENDING and the delivery
# groups mean it was accepted.
_REFUSED_GROUPS = frozenset({"REJECTED", "UNDELIVERABLE", "EXPIRED"})


class InfobipSmsBackend:
    def send(self, message: SmsMessage) -> None:
        try:
            response = requests.post(
                f"{settings.INFOBIP_BASE_URL}{SEND_PATH}",
                json=_payload(message),
                headers={
                    "Authorization": f"App {settings.INFOBIP_API_KEY}",
                    "Accept": "application/json",
                },
                timeout=settings.SMS_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SmsSendError(f"Infobip request failed: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise SmsSendError(f"Infobip answered HTTP {response.status_code}")
        group = _status_group(response)
        if group in _REFUSED_GROUPS:
            raise SmsSendError(f"Infobip refused the message: {group}")


def _payload(message: SmsMessage) -> dict[str, Any]:
    return {
        "messages": [
            {
                "from": message.sender,
                # Infobip takes the international number without the leading "+".
                "destinations": [{"to": message.to.removeprefix("+")}],
                "text": message.text,
            }
        ]
    }


def _status_group(response: requests.Response) -> str | None:
    try:
        group = response.json()["messages"][0]["status"]["groupName"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    # Only a known group name goes into an error message.
    return group if isinstance(group, str) and group.isalpha() else None

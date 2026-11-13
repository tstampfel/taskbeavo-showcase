"""Outbox handlers of accounts: the new-device email (accounts/sessions.py, BEAV-76).

"New login to TaskBeavo from iPhone, Munich": device, coarse location and time, in the
account's language and time zone, with a "This wasn't me" button that opens the web
page /<locale>/this-wasnt-me, which logs out everywhere (`signOutEverywhereFromEmail`).
It is a security notice, so it can't be switched off and goes out whether or not the
account has the app.

The device name comes from the client, so only names shaped like the apps' own ("iPhone",
"Firefox on Mac") are shown; anything else becomes the platform's translated label, and a
stranger can't write their own text into someone else's inbox.
"""

import re
import zoneinfo
from typing import cast
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext

from accounts.models import Session
from accounts.sessions import NEW_DEVICE_TOPIC, sign_out_link_token
from core.email import render_email, send_email
from core.i18n import LocaleRecipient, in_recipient_locale
from core.models import Outbox
from core.outbox import outbox_key, register_handler

_SAFE_DEVICE_NAME = re.compile(r"^[A-Za-z]{2,20}( on [A-Za-z]{2,20})?$")


@register_handler(NEW_DEVICE_TOPIC)
def email_new_device(row: Outbox) -> None:
    session = Session.objects.select_related("user").filter(pk=row.aggregate_id).first()
    # A session revoked meanwhile still gets its notice: "This wasn't me" may matter most.
    if session is None or not session.user.is_active:
        return
    user = session.user
    recipient = cast(LocaleRecipient, user)
    query = urlencode({"token": sign_out_link_token(session)})
    with in_recipient_locale(recipient) as locale:
        device = device_label(session)
        link = f"{settings.WEB_APP_URL}/{locale}/this-wasnt-me?{query}"
    email = render_email(
        "new_device",
        recipient,
        {
            "device": device,
            "location": session.location,
            "signed_in_at": timezone.localtime(session.created_at, _zone(user.time_zone)),
            "link": link,
        },
    )
    send_email(user.email, email, idempotency_key=outbox_key(row, "email"))


def device_label(session: Session) -> str:
    """The device as the email names it; call in the recipient's language."""
    if _SAFE_DEVICE_NAME.match(session.device_name):
        return session.device_name
    labels = {
        Session.Platform.IOS: gettext("iPhone"),
        Session.Platform.ANDROID: gettext("Android phone"),
    }
    return labels.get(Session.Platform(session.platform), gettext("Web browser"))


def _zone(name: str) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return zoneinfo.ZoneInfo("Europe/Zagreb")

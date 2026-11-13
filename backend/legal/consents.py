"""Analytics and marketing consent (BEAV-83; plan, GDPR section "Correction, objection and
consent").

Every purpose is off until the user agrees. Each change writes a `Consent` row naming the
legal document version the choice referred to, so TaskBeavo can prove what was agreed and
when. Code that sends marketing or runs analytics for a signed-in user asks `has_consent`.
Booking and payment messages are part of the service and need no consent
(notifications.preferences).
"""

import uuid
from typing import Any

from django.contrib.auth.models import AbstractBaseUser

from core.errors import ApiError, ErrorCode
from legal.models import Consent, LegalDocument
from legal.services import current_document

# The document that describes each purpose: the cookie policy covers analytics cookies,
# the privacy notice covers news and offers by email or push.
DOCUMENT_FOR: dict[str, str] = {
    Consent.Purpose.ANALYTICS: LegalDocument.Kind.COOKIES,
    Consent.Purpose.MARKETING_EMAIL: LegalDocument.Kind.PRIVACY,
    Consent.Purpose.MARKETING_PUSH: LegalDocument.Kind.PRIVACY,
}


def record_consent(
    *,
    purpose: str,
    granted: bool,
    language: str,
    platform: str,
    user: AbstractBaseUser | None = None,
    visitor_id: uuid.UUID | None = None,
) -> Consent:
    """Records one choice about `purpose`, given in `language` on `platform`.

    A signed-out caller is a web visitor answering the cookie banner: it needs a
    `visitor_id` and can only answer analytics (UNAUTHENTICATED otherwise). Agreeing needs
    the purpose's document in force (CONSENT_UNAVAILABLE); saying no is always recorded.
    """
    if user is None and (visitor_id is None or purpose != Consent.Purpose.ANALYTICS):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    document = current_document(DOCUMENT_FOR[purpose], language)
    if granted and document is None:
        raise ApiError(ErrorCode.CONSENT_UNAVAILABLE, "No policy in force to agree to.")
    return Consent.objects.create(
        user=user,
        visitor_id=visitor_id,
        purpose=purpose,
        granted=granted,
        document=document,
        version=document.version if document else "",
        language=language,
        platform=platform,
    )


def current_consents(user: AbstractBaseUser) -> dict[str, Consent | None]:
    """Every purpose with the user's latest choice, or None while they never chose (off)."""
    latest: dict[str, Consent | None] = dict.fromkeys(Consent.Purpose.values)
    for row in Consent.objects.filter(user=user).order_by("given_at", "pk"):
        latest[row.purpose] = row
    return latest


def has_consent(user: AbstractBaseUser, purpose: str) -> bool:
    """Whether `user`'s latest choice about `purpose` is yes. Off when they never chose."""
    row = Consent.objects.filter(user=user, purpose=purpose).order_by("-given_at", "-pk").first()
    return row is not None and row.granted


def export_consents(user: AbstractBaseUser) -> list[dict[str, Any]]:
    """`user`'s whole consent history, oldest first, for their own data export."""
    return [
        {
            "purpose": row.purpose,
            "granted": row.granted,
            "version": row.version,
            "language": row.language,
            "platform": row.platform,
            "given_at": row.given_at.isoformat(),
        }
        for row in Consent.objects.filter(user=user).order_by("given_at", "pk")
    ]

import datetime as dt

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from accounts.models import (
    EmailCode,
    PendingProviderName,
    PhoneCode,
    RefreshToken,
    Session,
    SignInNonce,
)


@shared_task(name="accounts.purge_expired")
def purge_expired() -> int:
    """Beat task: delete codes, sign-in nonces, pending Apple names and refresh tokens a day
    past their expiry.

    Codes hold the email or phone number of people who may never have made an account or
    verified that number, so they don't outlive their use (GDPR data minimisation). A
    used refresh token is kept until it expires, so its reuse is still detected until then.
    """
    cutoff = timezone.now() - dt.timedelta(days=1)
    codes, _ = EmailCode.objects.filter(expires_at__lt=cutoff).delete()
    phone_codes, _ = PhoneCode.objects.filter(expires_at__lt=cutoff).delete()
    nonces, _ = SignInNonce.objects.filter(expires_at__lt=cutoff).delete()
    names, _ = PendingProviderName.objects.filter(expires_at__lt=cutoff).delete()
    tokens, _ = RefreshToken.objects.filter(expires_at__lt=cutoff).delete()
    return codes + phone_codes + nonces + names + tokens


@shared_task(name="accounts.purge_ended_sessions")
def purge_ended_sessions() -> int:
    """Beat task: delete sessions SESSION_RETENTION_DAYS after they ended.

    Decision 2026-11-04 (BEAV-76): a session's record, with its coarse sign-in location
    and device hash, is kept 6 months after it ends, following the CNIL's recommendation
    for access logs (Délibération 2021-122: 6 months to 1 year). A session ends when it
    is revoked (sign-out, log out everywhere, refresh-token reuse, support) or when its
    last refresh token expires, REFRESH_TOKEN_TTL_DAYS after it was last used. Live
    sessions are never touched. Its refresh and push tokens go with it (CASCADE); a
    pending new-device email for it is skipped (accounts/outbox_handlers.py).
    """
    now = timezone.now()
    cutoff = now - dt.timedelta(days=settings.SESSION_RETENTION_DAYS)
    expired_cutoff = cutoff - dt.timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
    _, deleted = Session.objects.filter(
        Q(revoked_at__lt=cutoff) | Q(revoked_at__isnull=True, last_used_at__lt=expired_cutoff)
    ).delete()
    return deleted.get(Session._meta.label, 0)

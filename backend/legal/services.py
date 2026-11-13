"""Publishing legal document versions and finding the current one.

The rules a new version must meet (plan: "When the terms change"):

- It comes in all four languages at once (hr, en, de, fr): one version can't take effect
  in some languages only. The texts come from the lawyer and professional translators;
  nothing here generates or translates them (hard rule i).
- It never takes effect in the past, nor at the same moment as another version.
- A material change takes effect at least 30 days after it is published, so users get
  the notice, and after every version already scheduled. The very first real
  (non-draft) version of a kind is exempt from the notice: nobody has accepted anything
  yet.
- A small fix only has to come after the version in force, so a typo can be fixed while
  a material change waits out its notice. It doesn't change the scheduled version: if
  that needs the fix too, withdraw it and publish a corrected one.
- A draft can be published only where `LEGAL_DRAFTS_ALLOWED` is on (never in production).
- A CANCELLATION version marks the two checkout tick-box texts for private and for trader
  helpers in every language (legal/checkout_consent.py), drafts included, since checkout
  can't be shown without them.
- A version string is never reused, even after a withdrawal.

A scheduled version can be withdrawn until it takes effect, if nobody has accepted it
(`withdraw`). Withdrawn versions are skipped everywhere else here.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Exists, F, Max, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone

from core.i18n import SUPPORTED_LOCALES
from legal.checkout_consent import MARKERS, checkout_consent_texts
from legal.models import LegalDocument, LegalDocumentWithdrawal, TermsAcceptance

MATERIAL_CHANGE_NOTICE = timedelta(days=30)
_VERSION = re.compile(r"[A-Za-z0-9._-]{1,32}")


class PublishRefused(Exception):
    """The version breaks a publishing rule; the message says which."""


class WithdrawRefused(Exception):
    """The version can't be withdrawn; the message says why."""


def _not_withdrawn(documents: QuerySet[LegalDocument]) -> QuerySet[LegalDocument]:
    withdrawals = LegalDocumentWithdrawal.objects.filter(
        kind=OuterRef("kind"), version=OuterRef("version")
    )
    return documents.exclude(Exists(withdrawals))


def _lock_kind(kind: str) -> None:
    """One publish or withdrawal per kind at a time, so two can't both pass their checks;
    a row lock wouldn't do, as the first version has no rows and inserts stay unseen."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"legal:{kind}"])


@dataclass(frozen=True)
class NewVersion:
    kind: str
    version: str
    # Language code → text, for every supported language.
    bodies: dict[str, str]
    effective_at: datetime
    material_change: bool
    is_draft: bool


def publish(new: NewVersion, *, now: datetime | None = None) -> list[LegalDocument]:
    """Store every language of `new`, or nothing; `PublishRefused` says why not."""
    now = now or timezone.now()
    if new.kind not in LegalDocument.Kind.values:
        raise PublishRefused(f"Unknown kind {new.kind}.")
    if not _VERSION.fullmatch(new.version):
        raise PublishRefused(
            "The version must be 1 to 32 letters, digits, dots, hyphens or underscores."
        )
    missing = [code for code in SUPPORTED_LOCALES if not new.bodies.get(code, "").strip()]
    if missing:
        raise PublishRefused(
            f"Every language is needed before a version can take effect; missing: "
            f"{', '.join(missing)}."
        )
    unknown = sorted(set(new.bodies) - set(SUPPORTED_LOCALES))
    if unknown:
        raise PublishRefused(f"Unsupported languages: {', '.join(unknown)}.")
    if new.kind == LegalDocument.Kind.CANCELLATION:
        unmarked = [
            code for code in SUPPORTED_LOCALES if not checkout_consent_texts(new.bodies[code])
        ]
        if unmarked:
            raise PublishRefused(
                "A cancellation policy marks each checkout tick-box text once, for private and "
                "trader helpers "
                f"({', '.join(f'checkout:{name}' for name in MARKERS)}); missing in: "
                f"{', '.join(unmarked)}."
            )
    if new.is_draft and not settings.LEGAL_DRAFTS_ALLOWED:
        raise PublishRefused("Drafts can't be published here (LEGAL_DRAFTS_ALLOWED is off).")
    if new.effective_at < now:
        raise PublishRefused("The effective date is in the past.")

    with transaction.atomic():
        _lock_kind(new.kind)
        if LegalDocument.objects.filter(kind=new.kind, version=new.version).exists():
            raise PublishRefused(f"{new.kind} version {new.version} already exists.")
        rows = list(
            _not_withdrawn(LegalDocument.objects.filter(kind=new.kind, language="hr")).only(
                "effective_at", "is_draft"
            )
        )
        # `effective_at` is never in the past, so it is always after the version in force.
        if any(row.effective_at == new.effective_at for row in rows):
            raise PublishRefused("Another version takes effect at that exact moment.")
        latest = max((row.effective_at for row in rows), default=None)
        if new.material_change and latest is not None and new.effective_at < latest:
            raise PublishRefused(
                "A material change must take effect after every scheduled version "
                f"({latest.isoformat()})."
            )
        has_final = any(not row.is_draft for row in rows)
        if new.material_change and has_final and new.effective_at < now + MATERIAL_CHANGE_NOTICE:
            raise PublishRefused(
                "A material change needs at least 30 days' notice; set the effective date "
                f"on or after {_first_midnight_from(now + MATERIAL_CHANGE_NOTICE).isoformat()}."
            )
        # TODO(BEAV-182): a material change also queues the outbox event that emails and notifies
        # every user, in their language, 30 days ahead (realtime-notifications).
        return [
            LegalDocument.objects.create(
                kind=new.kind,
                version=new.version,
                language=code,
                body=new.bodies[code],
                body_sha256=hashlib.sha256(new.bodies[code].encode()).hexdigest(),
                effective_at=new.effective_at,
                material_change=new.material_change,
                is_draft=new.is_draft,
                published_at=now,
            )
            for code in SUPPORTED_LOCALES
        ]


def withdraw(
    kind: str, version: str, reason: str, *, now: datetime | None = None
) -> LegalDocumentWithdrawal:
    """Withdraw a scheduled version before it takes effect; `WithdrawRefused` says why not.

    The version's rows stay as published; only the withdrawal record is added.
    """
    now = now or timezone.now()
    if not reason.strip():
        raise WithdrawRefused("Give a reason for the withdrawal.")
    with transaction.atomic():
        _lock_kind(kind)
        document = LegalDocument.objects.filter(kind=kind, version=version).first()
        if document is None:
            raise WithdrawRefused(f"{kind} version {version} doesn't exist.")
        if LegalDocumentWithdrawal.objects.filter(kind=kind, version=version).exists():
            raise WithdrawRefused(f"{kind} version {version} is already withdrawn.")
        if document.effective_at <= now:
            raise WithdrawRefused(
                f"{kind} version {version} is already in effect; publish a new version instead."
            )
        if TermsAcceptance.objects.filter(document__kind=kind, document__version=version).exists():
            raise WithdrawRefused(f"Someone has accepted {kind} version {version}.")
        return LegalDocumentWithdrawal.objects.create(
            kind=kind, version=version, reason=reason, withdrawn_at=now
        )


def _first_midnight_from(moment: datetime) -> date:
    """The first date whose midnight in Croatia (how the command reads a date) is at or
    after `moment`."""
    local = timezone.localtime(moment)
    return local.date() if local.time() == time() else local.date() + timedelta(days=1)


def current_document(
    kind: str, language: str, *, at: datetime | None = None
) -> LegalDocument | None:
    """The version of `kind` in force at `at` (now by default), in `language`."""
    return (
        _not_withdrawn(
            LegalDocument.objects.filter(
                kind=kind, language=language, effective_at__lte=at or timezone.now()
            )
        )
        .order_by("-effective_at")
        .first()
    )


def readable_documents(kind: str, language: str) -> QuerySet[LegalDocument]:
    """Every version of `kind` in `language` a reader may open on the legal pages, newest
    first: past, current and scheduled, never withdrawn ones. Drafts only where
    `LEGAL_DRAFTS_ALLOWED` is on, so production never serves placeholder text."""
    documents = _not_withdrawn(LegalDocument.objects.filter(kind=kind, language=language))
    if not settings.LEGAL_DRAFTS_ALLOWED:
        documents = documents.filter(is_draft=False)
    return documents.order_by("-effective_at")


def live_drafts(*, at: datetime | None = None) -> list[LegalDocument]:
    """Drafts that are current or scheduled at `at`: what production must never show.

    A draft superseded by a later version is history and doesn't count, nor does a
    withdrawn one.
    """
    at = at or timezone.now()
    current_from = (
        _not_withdrawn(
            LegalDocument.objects.filter(
                kind=OuterRef("kind"), language=OuterRef("language"), effective_at__lte=at
            )
        )
        .values("kind", "language")
        .annotate(latest=Max("effective_at"))
        .values("latest")
    )
    return list(
        _not_withdrawn(LegalDocument.objects.all())
        .annotate(current_from=Subquery(current_from))
        .filter(is_draft=True)
        .filter(Q(effective_at__gt=at) | Q(effective_at=F("current_from")))
        .order_by("kind", "language", "effective_at")
    )

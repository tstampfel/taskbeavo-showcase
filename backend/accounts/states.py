"""Account states and the limited mode they put an account in (plan: "Sign-up and login",
"Reporting, blocking, suspension and appeal", "Terms and conditions").

| State                   | Can still                                                     |
|-------------------------|---------------------------------------------------------------|
| ACTIVE                  | everything their role allows                                  |
| TERMS_UPDATE_REQUIRED   | view history, finish open bookings, export data, delete       |
| SUSPENDED               | the same, and see the reason and appeal (trust-safety)        |
| DELETION_PENDING        | the same, while open bookings and payouts close               |

None of them can create new work: `require_can_create_work` refuses postTask,
publishTaskDraft, sendOffer, updateOffer, renewOffer and acceptOffer with the state's
error code, and updateTask beyond the photos and access details. The other side is
covered too: an offer from a SUSPENDED or DELETION_PENDING helper can't be accepted
(`require_helper_takes_bookings`), and a SUSPENDED or DELETION_PENDING poster's tasks
take no offers (`takes_offers`), each with a neutral code that doesn't reveal the state.
A TERMS_UPDATE_REQUIRED account stays bookable and keeps receiving offers: it accepts
the terms before it can accept one. Everything else (reading, chat and payment steps on
open bookings, cancellations, data export, deletion, sign-in and sessions) stays open,
and so do subscriptions: a limited account keeps receiving its open bookings' events.

SUSPENDED and DELETION_PENDING are stored on `User.status`. TERMS_UPDATE_REQUIRED is
worked out each time from the TERMS versions in force and the user's TERMS_ACCEPTANCE
rows, so publishing a material version puts everyone who hasn't accepted it in limited
mode at its effective date, with no job to run and no flag to drift. The way out is
`accept_current_terms` (the "We've updated our terms" screen): it only adds the
TERMS_ACCEPTANCE row. Accepting a booking's checkout doesn't count (user decision
2026-11-05): that acceptance is the record of the one booking.
"""

from datetime import datetime
from enum import Enum

from django.db import transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from accounts.models import User
from core.errors import ApiError, ErrorCode
from legal.models import LegalDocument, LegalDocumentWithdrawal, TermsAcceptance
from legal.services import current_document


class AccountState(Enum):
    ACTIVE = "ACTIVE"
    TERMS_UPDATE_REQUIRED = "TERMS_UPDATE_REQUIRED"
    SUSPENDED = "SUSPENDED"
    DELETION_PENDING = "DELETION_PENDING"


_REFUSALS = {
    AccountState.TERMS_UPDATE_REQUIRED: (
        ErrorCode.TERMS_UPDATE_REQUIRED,
        "Accept the updated terms before creating new work.",
    ),
    AccountState.SUSPENDED: (ErrorCode.ACCOUNT_SUSPENDED, "The account is suspended."),
    AccountState.DELETION_PENDING: (
        ErrorCode.ACCOUNT_DELETION_PENDING,
        "The account is waiting to be deleted.",
    ),
}


def _withdrawals(kind: str, version: str) -> QuerySet[LegalDocumentWithdrawal]:
    """Withdrawals of the version the outer query's `kind` and `version` name."""
    return LegalDocumentWithdrawal.objects.filter(kind=OuterRef(kind), version=OuterRef(version))


def terms_baseline(*, at: datetime | None = None) -> datetime | None:
    """The effective date of the oldest TERMS version a user may have accepted and still
    have full access at `at`; None while no TERMS version is in force.

    That is the latest version in force that needed acceptance: a material change, the
    first version, or the first final version after drafts (the first real terms are
    exempt from the 30-day notice, so they may not be marked material). A small fix
    after it doesn't ask anyone to accept again.
    """
    # Every version has all four languages with the same dates and flags; one is enough.
    versions = (
        LegalDocument.objects.filter(
            kind=LegalDocument.Kind.TERMS, language="hr", effective_at__lte=at or timezone.now()
        )
        .exclude(Exists(_withdrawals("kind", "version")))
        .order_by("effective_at")
        .values_list("effective_at", "material_change", "is_draft")
    )
    baseline: datetime | None = None
    previous_is_draft = True
    for effective_at, material_change, is_draft in versions:
        if material_change or baseline is None or (previous_is_draft and not is_draft):
            baseline = effective_at
        previous_is_draft = is_draft
    return baseline


def must_accept_terms(user: User, *, at: datetime | None = None) -> bool:
    """Whether `user` hasn't accepted the TERMS version in force at `at`, nor a later one.

    An acceptance of a withdrawn version doesn't count: the user never saw the version
    that replaced it (review BEAV-77 R1-4)."""
    baseline = terms_baseline(at=at)
    if baseline is None:
        return False
    return not (
        TermsAcceptance.objects.filter(
            user=user,
            document__kind=LegalDocument.Kind.TERMS,
            document__effective_at__gte=baseline,
        )
        .exclude(Exists(_withdrawals("document__kind", "document__version")))
        .exists()
    )


def terms_to_accept(user: User, language: str) -> LegalDocument | None:
    """The document "We've updated our terms" shows `user`, in `language`: the TERMS
    version in force while they must accept it, None otherwise. Its version and
    effective date are what the tick box names and what `accept_current_terms` records.

    Helper terms re-acceptance comes with helper onboarding (BEAV-96); the screens already
    show a HELPER_TERMS document by its own name and link when this returns one."""
    if not must_accept_terms(user):
        return None
    return current_document(LegalDocument.Kind.TERMS, language)


def accept_current_terms(user: User, version: str, language: str, platform: str) -> User:
    """Records that `user` accepted TERMS `version`, as shown in `language` on `platform`.

    `version` must be the one in force now (TERMS_NOT_ACCEPTED otherwise, also while none
    is): the screen shows what applies, and a scheduled version may still be withdrawn.
    Accepting a version already accepted, in any language, adds nothing. The user row is
    locked so that two taps can't both add one.
    """
    with transaction.atomic():
        User.objects.select_for_update().only("pk").get(pk=user.pk)
        document = current_document(LegalDocument.Kind.TERMS, language)
        if document is None or document.version != version:
            raise ApiError(ErrorCode.TERMS_NOT_ACCEPTED, "Accept the terms version in force.")
        accepted = TermsAcceptance.objects.filter(
            user=user, document__kind=document.kind, document__version=document.version
        ).exists()
        if not accepted:
            TermsAcceptance.objects.create(user=user, document=document, platform=platform)
    return user


def account_state(user: User, *, at: datetime | None = None) -> AccountState:
    """The state that limits `user` most: deletion, then suspension, then the terms."""
    if user.status == User.Status.DELETION_PENDING:
        return AccountState.DELETION_PENDING
    if user.status == User.Status.SUSPENDED:
        return AccountState.SUSPENDED
    if must_accept_terms(user, at=at):
        return AccountState.TERMS_UPDATE_REQUIRED
    return AccountState.ACTIVE


def require_helper_takes_bookings(helper: User) -> None:
    """Refuses accepting an offer from a SUSPENDED or DELETION_PENDING helper with
    OFFER_NOT_AVAILABLE, which never tells the poster why (user decision 2026-11-04,
    review BEAV-77 R1-2). A helper who only has to accept updated terms can still be
    booked: their existing offer stands. Reads the stored status only, no query."""
    if helper.status != User.Status.ACTIVE:
        raise ApiError(ErrorCode.OFFER_NOT_AVAILABLE, "The offer is no longer available.")


def takes_offers(poster: User) -> bool:
    """Whether helpers may send, change or renew offers on `poster`'s tasks: not while the
    poster is SUSPENDED or DELETION_PENDING. Callers answer TASK_NOT_OPEN_FOR_OFFERS
    otherwise, so a helper never learns the poster's state (review BEAV-77 R1-6). A poster
    who only has to accept updated terms keeps receiving offers: acceptOffer asks them to
    accept the terms first (user decision 2026-11-04). Reads the stored status only."""
    return poster.status == User.Status.ACTIVE


def require_can_create_work(user: User) -> None:
    """Raises the state's error code unless `user` is ACTIVE. Runs queries: call it from
    sync code (the `CanCreateWork` permission wraps it for async resolvers)."""
    state = account_state(user)
    if state is not AccountState.ACTIVE:
        code, message = _REFUSALS[state]
        raise ApiError(code, message)

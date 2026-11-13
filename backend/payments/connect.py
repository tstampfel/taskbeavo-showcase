"""Helpers' Stripe Express accounts (plan: "Becoming a helper"; "Payout failures and
account problems").

- `start_onboarding` opens the helper's Express account on first use and returns a
  single-use link to Stripe's hosted onboarding, where they prove their identity and give
  the IBAN payouts go to. The same link type serves when Stripe later asks for more.
- `apply_account_update` copies an `account.updated` webhook onto our row (hard rule a:
  the account's state comes from Stripe, never from a client) and derives `status`. Only
  ACTIVE, an account that can receive destination charges, lets the helper send offers.
- Entering NEEDS_INFO or REJECTED tells the helper by push and email (a service message
  about their own account, not marketing), and `send_info_reminders` repeats the NEEDS_INFO
  notice a few times while nothing changes.

Every Stripe call happens outside a database transaction, with an idempotency key from
`payments.idempotency` (`helper:{id}:express-account:v1`). Account IDs and Stripe's
requirement keys may appear on spans and in logs; nothing the helper typed does.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue
from core.transitions import locked_for_update
from helpers.models import HelperProfile
from helpers.services import Requirement, missing_requirements
from payments.idempotency import idempotency_key
from payments.models import ConnectedAccount, ConnectedAccountEvent
from payments.stripe_client import get_gateway

logger = logging.getLogger(__name__)

Status = ConnectedAccount.Status

# The outbox topic of a status change; its handler notifies the helper
# (payments/outbox_handlers.py).
STATUS_CHANGED_TOPIC = "payout_account.status_changed"
INFO_REMINDER_TOPIC = "payout_account.info_reminder"

# Requirements that must be met before Stripe onboarding makes sense: a Croatian address
# and a valid OIB (plan flowchart "Address in Croatia and OIB valid?").
_ONBOARDING_PREREQUISITES = frozenset({Requirement.ADDRESS, Requirement.OIB})


@dataclass(frozen=True)
class AccountState:
    """What TaskBeavo keeps of a Stripe account object."""

    details_submitted: bool
    charges_enabled: bool
    payouts_enabled: bool
    card_payments_capability: str
    transfers_capability: str
    requirements_due: list[str]
    current_deadline: dt.datetime | None
    disabled_reason: str
    pending_verification: bool

    @property
    def status(self) -> ConnectedAccount.Status:
        if self.disabled_reason.startswith("rejected."):
            return Status.REJECTED
        if not self.details_submitted:
            return Status.INCOMPLETE
        if self.requirements_due:
            return Status.NEEDS_INFO
        if (
            self.charges_enabled
            and self.card_payments_capability == "active"
            and self.transfers_capability == "active"
        ):
            return Status.ACTIVE
        if self.pending_verification or self.disabled_reason in (
            "",
            "requirements.pending_verification",
        ):
            return Status.VERIFYING
        return Status.RESTRICTED


def account_state(account: dict[str, Any]) -> AccountState:
    """The state of a Stripe account object (a webhook payload's `data.object`)."""
    requirements = account.get("requirements") or {}
    capabilities = account.get("capabilities") or {}
    due = [*(requirements.get("past_due") or []), *(requirements.get("currently_due") or [])]
    deadline = requirements.get("current_deadline")
    return AccountState(
        details_submitted=bool(account.get("details_submitted")),
        charges_enabled=bool(account.get("charges_enabled")),
        payouts_enabled=bool(account.get("payouts_enabled")),
        card_payments_capability=str(capabilities.get("card_payments") or ""),
        transfers_capability=str(capabilities.get("transfers") or ""),
        requirements_due=list(dict.fromkeys(str(key) for key in due)),
        current_deadline=(
            dt.datetime.fromtimestamp(deadline, tz=dt.UTC) if isinstance(deadline, int) else None
        ),
        disabled_reason=str(requirements.get("disabled_reason") or ""),
        pending_verification=bool(requirements.get("pending_verification")),
    )


@dataclass(frozen=True)
class OnboardingLink:
    account: ConnectedAccount
    url: str
    expires_at: dt.datetime


def start_onboarding(user: User) -> OnboardingLink:
    """A link to Stripe's Express onboarding for `user`'s helper profile.

    Raises HELPER_PROFILE_INCOMPLETE until the profile has a Croatian address and an OIB,
    and PAYOUT_ACCOUNT_REJECTED once Stripe rejected the account (support handles that).
    """
    profile = HelperProfile.objects.filter(user=user).first()
    if profile is None or _ONBOARDING_PREREQUISITES & set(missing_requirements(user, profile)):
        raise ApiError(
            ErrorCode.HELPER_PROFILE_INCOMPLETE,
            "Add a Croatian address and the OIB before setting up payouts.",
        )
    account = _account_of(profile)
    if account.status == Status.REJECTED:
        raise ApiError(ErrorCode.PAYOUT_ACCOUNT_REJECTED, "Stripe rejected this payout account.")

    with locked_for_update(ConnectedAccount.objects, account.pk) as locked:
        locked.onboarding_links_created += 1
        locked.save(update_fields=["onboarding_links_created", "updated_at"])
        number = locked.onboarding_links_created
    # Each request gets a new link (a link is single-use and expires), so the key carries
    # the link's number; a network retry of this request reuses it.
    link = get_gateway().create_onboarding_link(
        connected_account=account.stripe_account_id,
        return_url=settings.STRIPE_CONNECT_RETURN_URL,
        refresh_url=settings.STRIPE_CONNECT_REFRESH_URL,
        idempotency_key=idempotency_key("helper", profile.pk, "account-link", str(number)),
    )
    account.refresh_from_db()
    return OnboardingLink(
        account=account,
        url=link.url,
        expires_at=dt.datetime.fromtimestamp(link.expires_at, tz=dt.UTC),
    )


def _account_of(profile: HelperProfile) -> ConnectedAccount:
    """The profile's connected account, created at Stripe and stored on first use."""
    account = ConnectedAccount.objects.filter(helper=profile).first()
    if account is not None:
        return account
    # Same key for every attempt: a double tap or a retry gets the same Stripe account.
    created = get_gateway().create_express_account(
        helper_profile_id=profile.pk,
        idempotency_key=idempotency_key("helper", profile.pk, "express-account"),
    )
    try:
        with transaction.atomic():
            account, _ = ConnectedAccount.objects.get_or_create(
                helper=profile, defaults={"stripe_account_id": created.id}
            )
    except IntegrityError:
        # A webhook for the new account stored it first (`apply_account_update`).
        account = ConnectedAccount.objects.get(helper=profile)
    return account


def apply_account_update(event: dict[str, Any]) -> None:
    """Copy an `account.updated` event onto the helper's connected account.

    Runs in the webhook's transaction (payments/webhooks.py). Stripe doesn't guarantee
    delivery order, so the event's `created` decides:

    - older than the state already stored: ignored, so a late event can't undo a newer
      state;
    - the same second as the stored state: `created` has whole seconds only, so which of
      the two is newer is unknown. Nothing is applied; after commit the account is read
      back from Stripe (`reconcile_account`), which gives the current state;
    - newer: applied.

    An account TaskBeavo doesn't know is ignored, unless its metadata names a helper
    profile: then the webhook beat `start_onboarding` to storing it.
    """
    obj = event["data"]["object"]
    stripe_account_id = obj["id"]
    event_at = dt.datetime.fromtimestamp(event["created"], tz=dt.UTC)
    account = (
        ConnectedAccount.objects.select_for_update()
        .filter(stripe_account_id=stripe_account_id)
        .first()
    ) or _adopt(obj)
    if account is None:
        logger.info(
            "account.updated for an account no helper owns",
            extra={"stripe.account_id": stripe_account_id},
        )
        return
    if account.stripe_state_at is not None and event_at <= account.stripe_state_at:
        tie = event_at == account.stripe_state_at
        logger.info(
            "account.updated from the same second as the stored state, reconciling"
            if tie
            else "account.updated older than the stored state, ignored",
            extra={"stripe.account_id": stripe_account_id, "stripe.event_id": event["id"]},
        )
        if tie:
            _reconcile_after_commit(account.pk)
        return
    _apply_state(
        account,
        account_state(obj),
        event_at,
        actor_kind=ActorKind.STRIPE_WEBHOOK,
        data={"stripe_event_id": event["id"]},
    )


# Delay before reading an account again after a reconcile hit a same-second tie; the next
# read then starts in a later second, so the loop ends.
RECONCILE_RETRY_SECONDS = 2
# Statuses Stripe may still change without us hearing about it (a lost event, a reconcile
# that ran out of retries); the daily sweep reads these back. ACTIVE and REJECTED are
# settled until Stripe sends a new event.
_SWEPT_STATUSES = (Status.INCOMPLETE, Status.NEEDS_INFO, Status.VERIFYING, Status.RESTRICTED)


def reconcile_account(account_id: int) -> None:
    """Read the account from Stripe and store its current state.

    Runs as a Celery task, outside any transaction while Stripe is called. `read_at` is the
    second the read started, compared with the stored state's `created` second:

    - earlier: a newer webhook was stored meanwhile; nothing is applied;
    - the same second: a webhook from that second may have been applied during the read,
      and which is newer is unknown. Nothing is applied and another read is queued;
    - later: the read is applied.

    A reconcile never moves `stripe_state_at`: the stamp stays the `created` of the last
    webhook applied, so our clock never makes a later Stripe event look older than it is.
    """
    account = ConnectedAccount.objects.get(pk=account_id)
    read_at = timezone.now().replace(microsecond=0)
    obj = get_gateway().retrieve_account(connected_account=account.stripe_account_id)
    with locked_for_update(ConnectedAccount.objects, account_id) as locked:
        stored_at = locked.stripe_state_at
        if stored_at is not None and read_at <= stored_at:
            if read_at == stored_at:
                _reconcile_after_commit(account_id, countdown=RECONCILE_RETRY_SECONDS)
            return
        _apply_state(
            locked,
            account_state(obj),
            stored_at,
            actor_kind=ActorKind.SYSTEM,
            data={"source": "reconcile"},
        )


def sweep_unsettled_accounts() -> int:
    """Queue a reconcile of every account in a status Stripe may still change (beat task,
    daily). Returns how many were queued."""
    from payments.tasks import reconcile_connected_account

    ids = list(
        ConnectedAccount.objects.filter(status__in=_SWEPT_STATUSES).values_list("pk", flat=True)
    )
    for account_id in ids:
        reconcile_connected_account.delay(account_id)
    return len(ids)


def _reconcile_after_commit(account_id: int, countdown: int = 0) -> None:
    # payments.tasks imports this module, so the task is looked up at call time.
    from payments.tasks import reconcile_connected_account

    transaction.on_commit(
        lambda: reconcile_connected_account.apply_async((account_id,), countdown=countdown)
    )


def _apply_state(
    account: ConnectedAccount,
    state: AccountState,
    state_at: dt.datetime | None,
    *,
    actor_kind: ActorKind,
    data: dict[str, Any],
) -> None:
    """Store `state` on the locked `account`; a status change writes the audit and outbox
    rows in the same transaction (hard rule c)."""
    previous = account.status
    account.details_submitted = state.details_submitted
    account.charges_enabled = state.charges_enabled
    account.payouts_enabled = state.payouts_enabled
    account.card_payments_capability = state.card_payments_capability
    account.transfers_capability = state.transfers_capability
    account.requirements_due = state.requirements_due
    account.current_deadline = state.current_deadline
    account.disabled_reason = state.disabled_reason
    account.status = state.status
    account.stripe_state_at = state_at
    if account.status != previous and account.status == Status.NEEDS_INFO:
        # The first notice goes out now; reminders count from here.
        account.info_reminders_sent = 0
        account.last_info_notice_at = timezone.now()
    account.save()

    if account.status != previous:
        ConnectedAccountEvent.objects.create(
            account=account,
            type="status_changed",
            actor_kind=actor_kind,
            data={"from": previous, "to": account.status, **data},
        )
        enqueue(STATUS_CHANGED_TOPIC, aggregate=account, payload={"status": account.status})


def _adopt(obj: dict[str, Any]) -> ConnectedAccount | None:
    """Store an account created by `start_onboarding` whose webhook arrived first."""
    metadata = obj.get("metadata") or {}
    try:
        profile_id = int(metadata.get("helper_profile_id", ""))
    except ValueError:
        return None
    profile = HelperProfile.objects.filter(pk=profile_id).first()
    if profile is None:
        return None
    account, _ = ConnectedAccount.objects.get_or_create(
        helper=profile, defaults={"stripe_account_id": obj["id"]}
    )
    if account.stripe_account_id != obj["id"]:
        # The helper already has another account: this one isn't theirs to adopt.
        return None
    return ConnectedAccount.objects.select_for_update().get(pk=account.pk)


def send_info_reminders(now: dt.datetime | None = None) -> int:
    """Remind helpers whose account still needs information (beat task, daily).

    At most `PAYOUT_INFO_MAX_REMINDERS` reminders, `PAYOUT_INFO_REMINDER_INTERVAL_DAYS`
    apart, after the first notice. Returns how many were queued.
    """
    now = now or timezone.now()
    due_before = now - dt.timedelta(days=settings.PAYOUT_INFO_REMINDER_INTERVAL_DAYS)
    candidates = ConnectedAccount.objects.filter(
        status=Status.NEEDS_INFO,
        info_reminders_sent__lt=settings.PAYOUT_INFO_MAX_REMINDERS,
        last_info_notice_at__lte=due_before,
    ).values_list("pk", flat=True)
    sent = 0
    for pk in list(candidates):
        with transaction.atomic():
            account = (
                ConnectedAccount.objects.select_for_update(skip_locked=True)
                .filter(
                    pk=pk,
                    status=Status.NEEDS_INFO,
                    info_reminders_sent__lt=settings.PAYOUT_INFO_MAX_REMINDERS,
                    last_info_notice_at__lte=due_before,
                )
                .first()
            )
            if account is None:
                continue
            account.info_reminders_sent += 1
            account.last_info_notice_at = now
            account.save(update_fields=["info_reminders_sent", "last_info_notice_at", "updated_at"])
            enqueue(
                INFO_REMINDER_TOPIC,
                aggregate=account,
                payload={"status": account.status, "reminder": account.info_reminders_sent},
            )
            sent += 1
    return sent

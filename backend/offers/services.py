"""Sending, changing, renewing, withdrawing and expiring offers (docs/PLAN.md, "Posting a
task and handling offers", "Service area: tasks in Croatia only").

- Who: sending, changing and renewing need a helper who can send offers
  (`helpers.services.require_can_send_offers`: verified phone, complete profile, ACTIVE
  Stripe payout account, a verified company). Withdrawing needs only the offer's helper,
  so a helper whose account Stripe paused can still pull an offer back. Nobody offers on
  their own task (OFFER_ON_OWN_TASK), and helpers offer only inside the service areas they
  selected (TASK_OUTSIDE_HELPER_AREAS).
- Where (hard rule j): every send, change and renewal checks the task's location against
  the active service areas again (`areas.services.assert_in_service_area`). A task whose
  area was switched off, or whose stored place lies outside every area, gets
  OUT_OF_SERVICE_AREA.
- Business posters (accounts.business): their tasks take offers from registered helpers
  only (BUSINESS_POSTER_REGISTERED_ONLY on sending, changing and renewing), and they don't
  see private helpers' offers (`visible_to_poster`). Becoming one withdraws private
  helpers' offers (`withdraw_for_business_poster`, outbox topic
  `offer.withdrawn_registered_only`, which tells the helper why).
- Price: at least EUR 10, like a task's budget (OFFER_PRICE_BELOW_MINIMUM).
- Message: optional, through the contact-detail filter like task text; masked text is
  stored. Offers have no review queue, so a doubtful match (NEEDS_REVIEW) is refused with
  OFFER_MESSAGE_CONTACT_DETAILS and the helper rewrites it.
- Booked: from the moment the poster accepts an offer (a booking in AWAITING_PAYMENT or
  later) the task takes no new, changed or renewed offers (TASK_NOT_OPEN_FOR_OFFERS),
  until that checkout fails (PAYMENT_FAILED). Withdrawing stays possible.
- Lifetime: an offer lasts OFFER_LIFETIME, never past the task's end (`task_ends_at`),
  also when the poster moves the date earlier. Renewing restarts that from now, also for
  an expired offer; it never confirms an offer the task changed under. A Beat task expires offers
  (`expire_offers`), and another queues the poster's reminder a day before an offer
  expires (`queue_expiry_reminders`, outbox topic `offer.expiring`).
- Notifying: sending writes `offer.received`, withdrawing `offer.withdrawn`; the poster
  hears of both live and by push (offers/outbox_handlers.py). Changing and renewing write
  `offer.updated`, a live event only.
- Closing: `close_offers` ends a task's live offers when the task closes.
- Needs update: `mark_needs_update` runs when the poster changes a task's date, address
  or scope (tasks.services.update_task). Changing or just confirming the offer
  (`update_offer` with no changes) makes it SENT again.
- Withdrawing writes the outbox row `offer.withdrawn`. If the poster is in checkout with
  this offer, its handler cancels the payment (bookings.checkout); this module makes no
  Stripe call.
- Locks: the task's poster, then the task, then its offers. update_task locks the task
  before its offers; acceptOffer and becoming a business poster lock the poster first.

Every change writes an `OfferEvent` in the same transaction. Never log messages.
"""

import datetime as dt
import logging
from uuid import UUID

from django.contrib.gis.geos import Point
from django.db import IntegrityError, transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from accounts.business import is_business_poster
from accounts.models import User
from accounts.permissions import require_signed_in
from accounts.states import takes_offers
from areas.models import ServiceArea
from areas.services import assert_in_service_area
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue
from helpers.models import HelperProfile
from helpers.services import require_can_send_offers
from moderation.contact_filter import Verdict, filter_contact_details
from offers.models import Offer, OfferEvent
from tasks.models import Task

logger = logging.getLogger(__name__)

MIN_PRICE_CENTS = 1_000
MAX_PRICE_CENTS = 1_000_000
MAX_MESSAGE = 1_000
OFFER_LIFETIME = dt.timedelta(days=7)
# A flexible-date task stays open this long after publishing (plan: "Tasks stay open
# until their date"); its offers end with it.
FLEXIBLE_TASK_LIFETIME = dt.timedelta(days=30)
EXPIRY_REMINDER_BEFORE = dt.timedelta(days=1)

RECEIVED_TOPIC = "offer.received"
WITHDRAWN_TOPIC = "offer.withdrawn"
EXPIRING_TOPIC = "offer.expiring"
# The helper changed or renewed an offer: a live event only, so an open offers list shows
# the new price or expiry (no push, no email).
UPDATED_TOPIC = "offer.updated"
# TaskBeavo withdrew a private helper's offer because the task's poster became a business
# poster: the helper hears why, with a neutral reason.
WITHDRAWN_REGISTERED_ONLY_TOPIC = "offer.withdrawn_registered_only"


def send_offer(
    helper: User,
    task_id: str,
    price_cents: int,
    message: str = "",
    *,
    now: dt.datetime | None = None,
) -> Offer:
    """Send `helper`'s offer on an open task."""
    profile = require_can_send_offers(helper)
    now = now or timezone.now()
    price = _price(price_cents)
    text, verdict = _message(helper, message)
    with transaction.atomic():
        _lock_poster_of(Task.objects.filter(status=Task.Status.OPEN), task_id)
        task = _open_task(task_id, lock=True)
        if task.poster_id == helper.pk:
            raise ApiError(ErrorCode.OFFER_ON_OWN_TASK, "You can't offer on your own task.")
        ends = _require_offerable(task, profile, now)
        if Offer.objects.filter(task=task, helper=helper, status__in=Offer.LIVE).exists():
            raise ApiError(ErrorCode.OFFER_ALREADY_SENT, "You already have an offer on this task.")
        offer = Offer(
            task=task,
            helper=helper,
            price_cents=price,
            message=text,
            moderation_verdict=verdict,
            expires_at=min(now + OFFER_LIFETIME, ends),
            lifetime_started_at=now,
        )
        try:
            with transaction.atomic():
                offer.save()
        except IntegrityError:
            # A second device sent one at the same moment (offer_one_live_per_helper).
            raise ApiError(
                ErrorCode.OFFER_ALREADY_SENT, "You already have an offer on this task."
            ) from None
        _event(offer, OfferEvent.Type.SENT, helper, price_cents=price)
        # The poster sees it live and gets a push (offers/outbox_handlers.py).
        enqueue(RECEIVED_TOPIC, aggregate=offer, payload={"task_id": str(offer.task_id)})
    return offer


def update_offer(
    helper: User,
    offer_id: str,
    *,
    price_cents: int | None = None,
    message: str | None = None,
    now: dt.datetime | None = None,
) -> Offer:
    """Change `helper`'s live offer; with no changes it confirms an offer that needs an
    update. Either way the offer is SENT afterwards. The expiry stays as it is, except
    that it never passes the task's (possibly moved) date."""
    profile = require_can_send_offers(helper)
    now = now or timezone.now()
    price = None if price_cents is None else _price(price_cents)
    text = None if message is None else _message(helper, message)
    with transaction.atomic():
        offer = _locked_own(helper, offer_id)
        _require_live(offer, now)
        ends = _require_offerable(offer.task, profile, now)
        _cap_expiry(offer, ends)
        if price is not None:
            offer.price_cents = price
        if text is not None:
            offer.message, offer.moderation_verdict = text
        offer.status = Offer.Status.SENT
        offer.task_changed = False
        offer.save()
        _event(offer, OfferEvent.Type.UPDATED, helper, price_cents=offer.price_cents)
        enqueue(UPDATED_TOPIC, aggregate=offer, payload={"task_id": str(offer.task_id)})
    return offer


def renew_offer(helper: User, offer_id: str, *, now: dt.datetime | None = None) -> Offer:
    """Give `helper`'s offer a fresh lifetime from now (one tap). An expired offer comes
    back, if the helper has no other live offer on the task: as SENT, or as NEEDS_UPDATE
    when the task changed since the helper last confirmed it. Renewing never confirms."""
    profile = require_can_send_offers(helper)
    now = now or timezone.now()
    with transaction.atomic():
        offer = _locked_own(helper, offer_id)
        if offer.status == Offer.Status.WITHDRAWN:
            raise ApiError(ErrorCode.OFFER_NOT_EDITABLE, "A withdrawn offer can't be renewed.")
        ends = _require_offerable(offer.task, profile, now)
        if offer.status == Offer.Status.EXPIRED or offer.expires_at <= now:
            others = Offer.objects.filter(
                task_id=offer.task_id, helper=helper, status__in=Offer.LIVE
            ).exclude(pk=offer.pk)
            if others.exists():
                raise ApiError(
                    ErrorCode.OFFER_ALREADY_SENT, "You already have an offer on this task."
                )
            offer.closed_at = None
        offer.status = Offer.Status.NEEDS_UPDATE if offer.task_changed else Offer.Status.SENT
        offer.expires_at = min(now + OFFER_LIFETIME, ends)
        offer.lifetime_started_at = now
        offer.expiry_reminder_queued_at = None
        offer.save()
        _event(offer, OfferEvent.Type.RENEWED, helper)
        enqueue(UPDATED_TOPIC, aggregate=offer, payload={"task_id": str(offer.task_id)})
    return offer


def withdraw_offer(helper: User, offer_id: str, *, now: dt.datetime | None = None) -> Offer:
    """Withdraw `helper`'s live offer, any time before it is accepted, or the accepted
    one while the poster is still in checkout: that checkout then fails (PLAN.md, "A
    helper can withdraw an offer"). Once the payment is held the helper cancels instead."""
    # Imported here: bookings.models imports this app's models.
    from bookings.state_machine import end_checkout_for_withdrawal, lock_checkout

    require_signed_in(helper)
    now = now or timezone.now()
    with transaction.atomic():
        task = _lock_own_task(helper, offer_id)
        checkout = lock_checkout(offer_id)
        offer = _lock_offer(task, offer_id)
        in_checkout = checkout is not None and offer.status == Offer.Status.ACCEPTED
        if not (offer.is_live or in_checkout):
            raise ApiError(ErrorCode.OFFER_NOT_EDITABLE, "The offer is no longer open.")
        offer.status = Offer.Status.WITHDRAWN
        offer.closed_at = now
        offer.save()
        _event(offer, OfferEvent.Type.WITHDRAWN, helper)
        payload = {"offer_id": str(offer.pk), "task_id": str(offer.task_id)}
        if checkout is not None and in_checkout:
            end_checkout_for_withdrawal(checkout, now)
            payload["booking_id"] = str(checkout.pk)
        # The poster may be in checkout with this offer: the handler cancels that payment.
        enqueue(WITHDRAWN_TOPIC, aggregate=offer, payload=payload)
    return offer


def mark_needs_update(task: Task, actor: User) -> int:
    """Ask the helpers of `task`'s offers to confirm or change them, after the poster
    changed its date, address or scope. Call inside the transaction holding the task's
    lock, after the change is saved. Returns how many offers were marked.

    Live offers become NEEDS_UPDATE and their expiry is capped at the task's new date.
    Expired ones are marked too, so a later renewal doesn't bring them back as SENT."""
    offers = list(
        Offer.objects.select_for_update().filter(
            task=task, status__in=(*Offer.LIVE, Offer.Status.EXPIRED)
        )
    )
    ends = task_ends_at(task)
    for offer in offers:
        offer.task_changed = True
        if offer.is_live:
            offer.status = Offer.Status.NEEDS_UPDATE
            if ends is not None:
                _cap_expiry(offer, ends)
        offer.save()
        _event(offer, OfferEvent.Type.NEEDS_UPDATE, actor, kind=ActorKind.POSTER)
    return len(offers)


def close_offers(task: Task, *, now: dt.datetime | None = None) -> int:
    """End `task`'s live offers when the task closes (cancelled, expired or done). Call
    inside the transaction holding the task's lock. Returns how many were closed."""
    now = now or timezone.now()
    offers = list(Offer.objects.select_for_update().filter(task=task, status__in=Offer.LIVE))
    for offer in offers:
        offer.status = Offer.Status.EXPIRED
        offer.closed_at = now
        offer.save(update_fields=["status", "closed_at", "updated_at"])
        _event(offer, OfferEvent.Type.EXPIRED, None, kind=ActorKind.SYSTEM, reason="task_closed")
    return len(offers)


def withdraw_for_business_poster(poster: User, *, now: dt.datetime | None = None) -> int:
    """Withdraw private helpers' live offers on `poster`'s open tasks, once the poster is a
    business poster (accounts.business). Call inside its transaction, holding the poster's
    lock: offers are sent, changed and renewed under that lock too (`_lock_poster_of`), so
    none slips in meanwhile. Closed offers (declined, expired) stay as they are: their
    helpers were told long ago. Each helper hears why, with a neutral reason, by push.
    Returns how many were withdrawn."""
    now = now or timezone.now()
    private = _private_offers(
        Offer.objects.filter(
            task__poster=poster, task__status=Task.Status.OPEN, status__in=Offer.LIVE
        )
    )
    task_ids = sorted(set(private.values_list("task_id", flat=True)))
    # Tasks before their offers, the order update_task and create_booking use.
    list(Task.objects.select_for_update().filter(pk__in=task_ids).order_by("pk"))
    offers = list(private.select_for_update(of=("self",)).order_by("pk"))
    for offer in offers:
        withdraw_registered_only(offer, now)
    return len(offers)


def withdraw_registered_only(offer: Offer, now: dt.datetime) -> None:
    """Withdraw a private helper's locked offer on a business poster's task, with the
    neutral reason; the helper hears why (outbox topic `offer.withdrawn_registered_only`)."""
    reason = Offer.WithdrawnReason.BUSINESS_POSTER_REGISTERED_ONLY
    offer.status = Offer.Status.WITHDRAWN
    offer.withdrawn_reason = reason
    offer.closed_at = now
    offer.save(update_fields=["status", "withdrawn_reason", "closed_at", "updated_at"])
    _event(offer, OfferEvent.Type.WITHDRAWN, None, kind=ActorKind.SYSTEM, reason=reason.value)
    enqueue(
        WITHDRAWN_REGISTERED_ONLY_TOPIC, aggregate=offer, payload={"task_id": str(offer.task_id)}
    )


def task_ends_at(task: Task) -> dt.datetime | None:
    """When offers on `task` must end at the latest: the end of its window, or for a
    flexible task the end of its open period."""
    if task.flexible_date:
        published = task.published_at or task.created_at
        return published + FLEXIBLE_TASK_LIFETIME
    return task.window_end


def expire_offers(now: dt.datetime | None = None) -> int:
    """Beat task: mark live offers past their expiry EXPIRED. Returns how many."""
    now = now or timezone.now()
    due = Offer.objects.filter(status__in=Offer.LIVE, expires_at__lte=now)
    expired = 0
    for pk in list(due.values_list("pk", flat=True)):
        with transaction.atomic():
            offer = (
                Offer.objects.select_for_update(skip_locked=True)
                .filter(pk=pk, status__in=Offer.LIVE, expires_at__lte=now)
                .first()
            )
            if offer is None:
                continue
            offer.status = Offer.Status.EXPIRED
            offer.closed_at = now
            offer.save(update_fields=["status", "closed_at", "updated_at"])
            _event(offer, OfferEvent.Type.EXPIRED, None, kind=ActorKind.SYSTEM)
            expired += 1
    return expired


def queue_expiry_reminders(now: dt.datetime | None = None) -> int:
    """Beat task: queue the poster's reminder for live offers on open tasks that expire
    within EXPIRY_REMINDER_BEFORE. Once per offer lifetime; renewing allows another.
    An offer whose lifetime was a day or less (a task due within a day) gets none: the
    poster heard of it moments ago. Returns how many were queued."""
    now = now or timezone.now()
    due = Offer.objects.filter(
        status__in=Offer.LIVE,
        task__status=Task.Status.OPEN,
        expires_at__gt=now,
        expires_at__lte=now + EXPIRY_REMINDER_BEFORE,
        expiry_reminder_queued_at__isnull=True,
        lifetime_started_at__lt=F("expires_at") - EXPIRY_REMINDER_BEFORE,
    )
    queued = 0
    for pk in list(due.values_list("pk", flat=True)):
        with transaction.atomic():
            offer = (
                Offer.objects.select_for_update(skip_locked=True)
                .filter(pk=pk, status__in=Offer.LIVE, expiry_reminder_queued_at__isnull=True)
                .first()
            )
            if offer is None:
                continue
            offer.expiry_reminder_queued_at = now
            offer.save(update_fields=["expiry_reminder_queued_at", "updated_at"])
            enqueue(EXPIRING_TOPIC, aggregate=offer, payload={"task_id": str(offer.task_id)})
            queued += 1
    return queued


def offers_on_task(viewer: User, task_id: str) -> list[Offer]:
    """Every offer on the viewer's own task, newest first. TASK_NOT_FOUND for anyone
    else's task, so IDs reveal nothing."""
    try:
        task = Task.objects.get(pk=UUID(str(task_id)), poster=viewer)
    except (ValueError, Task.DoesNotExist):
        raise ApiError(ErrorCode.TASK_NOT_FOUND, "No such task.") from None
    return list(_with_helper(visible_to_poster(Offer.objects.filter(task=task), viewer)))


def visible_to_poster(offers: QuerySet[Offer], poster: User) -> QuerySet[Offer]:
    """`offers` as their task's poster sees them: a business poster doesn't see private
    helpers' offers, except one already accepted (its booking predates the business)."""
    if not is_business_poster(poster):
        return offers
    hidden = _private_offers(offers.exclude(status=Offer.Status.ACCEPTED))
    return offers.exclude(pk__in=hidden.values("pk"))


def _private_offers(offers: QuerySet[Offer]) -> QuerySet[Offer]:
    """The offers of helpers who aren't registered businesses (HelperProfile.is_trader)."""
    return offers.exclude(helper__helper_profile__declared_status=HelperProfile.Status.BUSINESS)


def my_offers(helper: User) -> list[Offer]:
    return list(_with_helper(Offer.objects.filter(helper=helper)))


def _with_helper(offers: QuerySet[Offer]) -> QuerySet[Offer]:
    return offers.select_related("task", "helper__helper_profile")


def _open_task(task_id: str, *, lock: bool) -> Task:
    queryset = Task.objects.filter(status=Task.Status.OPEN)
    if lock:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=UUID(str(task_id)))
    except (ValueError, Task.DoesNotExist):
        raise ApiError(ErrorCode.TASK_NOT_FOUND, "No such task.") from None


def _require_offerable(task: Task, profile: HelperProfile, now: dt.datetime) -> dt.datetime:
    """The latest end of an offer on `task`, once the task is OPEN, still inside an active
    service area (hard rule j) the helper selected, and its date hasn't passed."""
    if task.status != Task.Status.OPEN or task.location is None:
        raise ApiError(ErrorCode.TASK_NOT_OPEN_FOR_OFFERS, "The task no longer takes offers.")
    # From acceptance on, the task is booked (plan: Open -> AwaitingPayment); it takes
    # offers again only if that checkout fails. create_booking locks the task too.
    if _booked(task):
        raise ApiError(ErrorCode.TASK_NOT_OPEN_FOR_OFFERS, "The task is booked.")
    # A poster in limited mode can't accept offers, so their tasks take none; the code
    # doesn't say why (accounts.states).
    if not takes_offers(task.poster):
        raise ApiError(ErrorCode.TASK_NOT_OPEN_FOR_OFFERS, "The task no longer takes offers.")
    # A business poster's tasks take registered helpers only (accounts.business).
    if not profile.is_trader and is_business_poster(task.poster_id):
        raise ApiError(
            ErrorCode.BUSINESS_POSTER_REGISTERED_ONLY, "The task takes registered helpers only."
        )
    # Raises OUT_OF_SERVICE_AREA: the area may have been switched off since posting.
    assert_in_service_area(task.location)
    # Every area covering the point counts: a task on a shared boundary is in both.
    if not _in_helper_areas(profile, task.location):
        raise ApiError(
            ErrorCode.TASK_OUTSIDE_HELPER_AREAS, "The task is outside your service areas."
        )
    ends = task_ends_at(task)
    if ends is None or ends <= now:
        raise ApiError(ErrorCode.TASK_NOT_OPEN_FOR_OFFERS, "The task's date has passed.")
    return ends


def _booked(task: Task) -> bool:
    # Imported here: bookings.models imports this app's models.
    from bookings.queries import task_is_booked

    return task_is_booked(task)


def _in_helper_areas(profile: HelperProfile, point: Point) -> bool:
    return profile.service_areas.filter(
        active=True, country_code=ServiceArea.Country.HR, polygon__covers=point
    ).exists()


def _cap_expiry(offer: Offer, ends: dt.datetime) -> None:
    """Keep the offer's expiry within the task's date; a moved expiry may remind again."""
    if offer.expires_at > ends:
        offer.expires_at = ends
        offer.expiry_reminder_queued_at = None


def _locked_own(helper: User, offer_id: str) -> Offer:
    """`helper`'s offer, its task locked first and then the offer."""
    return _lock_offer(_lock_own_task(helper, offer_id), offer_id)


def _lock_own_task(helper: User, offer_id: str) -> Task:
    try:
        task_id = (
            Offer.objects.filter(helper=helper)
            .values_list("task_id", flat=True)
            .get(pk=UUID(str(offer_id)))
        )
    except (ValueError, Offer.DoesNotExist):
        raise ApiError(ErrorCode.OFFER_NOT_FOUND, "No such offer.") from None
    _lock_poster_of(Task.objects.all(), task_id)
    return Task.objects.select_for_update().get(pk=task_id)


def _lock_poster_of(tasks: QuerySet[Task], task_id: str | UUID) -> None:
    """Lock the poster of `task_id` (if it is in `tasks`) before the task: becoming a
    business poster (accounts.business) and acceptOffer lock the poster first too, so a
    task's offers can't change while the poster's business status does. FOR NO KEY UPDATE,
    so rows referencing the poster can still be inserted meanwhile."""
    try:
        poster_id = tasks.values_list("poster_id", flat=True).filter(pk=UUID(str(task_id))).first()
    except ValueError:
        return
    if poster_id is not None:
        User.objects.select_for_update(no_key=True).only("pk").filter(pk=poster_id).first()


def _lock_offer(task: Task, offer_id: str) -> Offer:
    offer = Offer.objects.select_for_update().get(pk=UUID(str(offer_id)))
    offer.task = task
    return offer


def _require_live(offer: Offer, now: dt.datetime) -> None:
    # An offer past its expiry that the Beat task hasn't reached yet counts as expired.
    if not offer.is_live or offer.expires_at <= now:
        raise ApiError(ErrorCode.OFFER_NOT_EDITABLE, "The offer is no longer open.")


def _price(cents: int) -> int:
    if cents < MIN_PRICE_CENTS:
        raise ApiError(ErrorCode.OFFER_PRICE_BELOW_MINIMUM, "The price is below EUR 10.")
    if cents > MAX_PRICE_CENTS:
        raise ApiError(ErrorCode.INVALID_OFFER_DETAILS, "The price is too high.")
    return cents


def _message(helper: User, message: str) -> tuple[str, str]:
    """The message as stored, and the filter's verdict."""
    text = message.strip()
    if len(text) > MAX_MESSAGE:
        raise ApiError(
            ErrorCode.INVALID_OFFER_DETAILS, f"The message is longer than {MAX_MESSAGE}."
        )
    if not text:
        return "", Verdict.CLEAN.value
    result = filter_contact_details(text, helper.locale)
    if result.verdict == Verdict.NEEDS_REVIEW:
        raise ApiError(
            ErrorCode.OFFER_MESSAGE_CONTACT_DETAILS, "The message may hold contact details."
        )
    if result.verdict != Verdict.CLEAN:
        logger.info(
            "Offer message screened",
            extra={"verdict": result.verdict.value, "kinds": sorted(k.value for k in result.kinds)},
        )
    return result.text, result.verdict.value


def _event(
    offer: Offer,
    type: OfferEvent.Type,
    actor: User | None,
    *,
    kind: ActorKind = ActorKind.HELPER,
    **data: int | str,
) -> None:
    OfferEvent.objects.create(
        offer=offer,
        type=type,
        actor_kind=kind,
        actor=actor,
        data={"task_id": str(offer.task_id), **data},
    )

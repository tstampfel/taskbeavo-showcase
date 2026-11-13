"""Business posters book only registered helpers (decision A4, guardrail 3; ZPD čl. 40
st. 2). The decision record is kept outside the public repository.

A poster becomes a business poster by giving the OIB of their obrt or company
(`set_poster_business`). From then on:

- private helpers (anyone whose helper status isn't BUSINESS) can't send, change or renew
  offers on their tasks (BUSINESS_POSTER_REGISTERED_ONLY, offers.services);
- private helpers' offers on their tasks are hidden from them (offers.services.offers_on_task);
- acceptOffer refuses a private helper's offer (bookings.state_machine.create_booking).

Becoming one withdraws the private helpers' open offers on the poster's tasks, each with
the neutral reason BUSINESS_POSTER_REGISTERED_ONLY (offers.services
.withdraw_for_business_poster). That is about the poster, never the helper: it carries no
penalty or ranking (hard rule k). A poster with a private helper's checkout still open is
refused (POSTER_BUSINESS_CHECKOUT_OPEN), since a payment may already be under way.

A poster may remove the business again (`clear_poster_business`), for instance after a
typo; withdrawn offers stay withdrawn. Every set, change and removal writes a
`PosterBusinessEvent`, so removing it to book a private helper and adding it back leaves a
trail.

TODO(BEAV-136): asking for a business invoice for the fee (eRacun) makes a poster a business
poster too, once that request exists.

Never log an OIB or send it to Sentry or Honeycomb (hard rule e).
"""

import datetime as dt

from django.db import transaction
from django.utils import timezone

from accounts.models import PosterBusiness, PosterBusinessEvent, User
from accounts.permissions import require_signed_in
from core.crypto import pii_digest
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from helpers.oib import is_valid_oib

MAX_NAME = 200


def is_business_poster(user: User | int) -> bool:
    """Whether the poster gave an obrt or company OIB: their tasks take registered helpers only."""
    user_id = user if isinstance(user, int) else user.pk
    return PosterBusiness.objects.filter(user_id=user_id).exists()


def poster_business(user: User) -> PosterBusiness | None:
    return PosterBusiness.objects.filter(user=user).first()


def set_poster_business(
    user: User, *, kind: str, name: str, oib: str, now: dt.datetime | None = None
) -> PosterBusiness:
    """Record (or change) the obrt or company `user` posts tasks for, and withdraw private
    helpers' open offers on their tasks."""
    # Imported here: these apps import accounts.
    from helpers.models import HelperProfile
    from offers.services import withdraw_for_business_poster

    require_signed_in(user)
    now = now or timezone.now()
    if kind not in PosterBusiness.Kind.values:
        raise ApiError(ErrorCode.INVALID_POSTER_BUSINESS, "Unknown business kind.")
    name = name.strip()
    if not name or len(name) > MAX_NAME:
        raise ApiError(ErrorCode.INVALID_POSTER_BUSINESS, "The business name is empty or too long.")
    oib = oib.strip()
    if not is_valid_oib(oib):
        raise ApiError(ErrorCode.INVALID_OIB, "The OIB's check digit doesn't match.")
    helper = HelperProfile.objects.filter(user=user).first()
    if kind == PosterBusiness.Kind.OBRT:
        # An obrt has no OIB of its own: it is the owner's. If we know the owner's, it must match.
        if helper is not None and helper.oib and helper.oib != oib:
            raise ApiError(ErrorCode.INVALID_BUSINESS_OIB, "An obrt's OIB is its owner's own OIB.")
    elif HelperProfile.objects.filter(oib_digest=pii_digest("oib", oib)).exists():
        # A person's OIB is never a company's, as for a helper's company (helpers.services).
        # The same code as a bad check digit, so the answer never says whether an OIB
        # belongs to a TaskBeavo helper; the mutation's tight rate limit does the rest.
        raise ApiError(ErrorCode.INVALID_OIB, "Not a valid company OIB.")
    with transaction.atomic():
        # Serialises against acceptOffer, which locks the poster first too
        # (bookings.state_machine.create_booking).
        User.objects.select_for_update(no_key=True).only("pk").get(pk=user.pk)
        if _private_checkout_open(user):
            raise ApiError(
                ErrorCode.POSTER_BUSINESS_CHECKOUT_OPEN,
                "A checkout with a private helper is still open.",
            )
        business, created = PosterBusiness.objects.update_or_create(
            user=user, defaults={"kind": kind, "name": name, "oib": oib}
        )
        withdrawn = withdraw_for_business_poster(user, now=now)
        _event(
            user,
            PosterBusinessEvent.Type.SET if created else PosterBusinessEvent.Type.CHANGED,
            kind=kind,
            status_before="PRIVATE" if created else "BUSINESS",
            status_after="BUSINESS",
            withdrawn_offers=withdrawn,
        )
    return business


def clear_poster_business(user: User) -> None:
    """`user` no longer posts for a business. Offers withdrawn meanwhile stay withdrawn."""
    require_signed_in(user)
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).only("pk").get(pk=user.pk)
        deleted, _ = PosterBusiness.objects.filter(user=user).delete()
        if deleted:
            _event(
                user,
                PosterBusinessEvent.Type.CLEARED,
                status_before="BUSINESS",
                status_after="PRIVATE",
            )


def _event(user: User, type: PosterBusinessEvent.Type, **data: int | str) -> None:
    PosterBusinessEvent.objects.create(
        poster=user, type=type, actor_kind=ActorKind.POSTER, actor=user, data=data
    )


def _private_checkout_open(user: User) -> bool:
    # Imported here: bookings imports accounts.
    from bookings.models import Booking
    from helpers.models import HelperProfile

    return (
        Booking.objects.filter(poster=user, status=Booking.Status.AWAITING_PAYMENT)
        .exclude(helper__helper_profile__declared_status=HelperProfile.Status.BUSINESS)
        .exists()
    )

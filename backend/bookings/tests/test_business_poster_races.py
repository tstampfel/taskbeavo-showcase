"""Races between becoming a business poster and a private helper's offer (BEAV-195,
review R1-1): sendOffer and setPosterBusiness both lock the poster first, so either the
offer waits and is refused, or it commits first and the switch withdraws it."""

import threading

import pytest
from django.db import connection, transaction

from accounts import business
from accounts.models import PosterBusiness, User
from core.errors import ApiError
from offers import services
from offers.models import Offer
from offers.tests.factories import COMPANY_OIB, make_helper, open_task

pytestmark = pytest.mark.django_db(transaction=True)

REASON = Offer.WithdrawnReason.BUSINESS_POSTER_REGISTERED_ONLY


def become_business(poster: User) -> PosterBusiness:
    return business.set_poster_business(
        poster, kind=PosterBusiness.Kind.COMPANY, name="Villa Beavo d.o.o.", oib=COMPANY_OIB
    )


def test_a_send_racing_the_switch_to_business_waits_for_it_and_is_refused() -> None:
    """Review R1-1: sendOffer locks the poster before the task, like setPosterBusiness. A
    private helper's offer on a task with no earlier private offers, sent while the poster
    is becoming a business, waits for that to commit and is then refused; it never ends
    up live on a business poster's task."""
    task = open_task()
    helper = make_helper()
    result: list[str] = []

    def send() -> None:
        try:
            services.send_offer(helper, str(task.pk), 3_000)
            result.append("ok")
        except ApiError as exc:
            result.append(exc.code.name)
        finally:
            connection.close()

    with transaction.atomic():
        become_business(task.poster)
        thread = threading.Thread(target=send)
        thread.start()
        thread.join(timeout=1)
        # Still waiting on the poster's lock this transaction holds.
        assert thread.is_alive()
    thread.join(timeout=10)

    assert result == ["BUSINESS_POSTER_REGISTERED_ONLY"]
    assert not Offer.objects.filter(task=task, status__in=Offer.LIVE).exists()


def test_a_send_that_got_the_lock_first_is_withdrawn_by_the_switch() -> None:
    """The other order: the offer commits first, so the switch finds it and withdraws it."""
    task = open_task()
    helper = make_helper()
    done: list[str] = []

    def switch() -> None:
        try:
            become_business(task.poster)
            done.append("ok")
        finally:
            connection.close()

    with transaction.atomic():
        offer = services.send_offer(helper, str(task.pk), 3_000)
        thread = threading.Thread(target=switch)
        thread.start()
        thread.join(timeout=1)
        assert thread.is_alive()
    thread.join(timeout=10)

    assert done == ["ok"]
    offer.refresh_from_db()
    assert (offer.status, offer.withdrawn_reason) == (Offer.Status.WITHDRAWN, REASON)

"""Test helpers who can send offers, and open tasks to offer on."""

import datetime as dt
from itertools import count
from typing import Any

from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import accept_current_terms, make_user
from areas.models import ServiceArea
from core.categories import TaskCategory
from core.synthetic_identifiers import synthetic_iban, synthetic_oib
from helpers import services as helpers
from helpers.models import HelperProfile
from legal.models import LegalDocument
from legal.services import current_document
from payments.tests.factories import connect_account
from tasks import services as tasks
from tasks.models import Task
from tasks.tests.factories import changes, make_poster

_numbers = count(1)


# make_helper's OIBs are synthetic counters 30-59, so they never meet the fixed synthetic
# OIBs other tests use (12, 69, 94, 95) or the demo seed's (1-3, 11-13, ...). OIBs are
# unique per database and each test starts from an empty one, so an autouse fixture in
# backend/conftest.py restarts the counter for every test: no test ever reuses an OIB.
_HELPER_OIB_COUNTERS = range(30, 60)
_helper_numbers = count()
# A company OIB for business posters, distinct from every helper's.
COMPANY_OIB = synthetic_oib(95)


def reset_helper_oibs() -> None:
    """Start make_helper's OIBs again from the first counter (once per test)."""
    global _helper_numbers
    _helper_numbers = count()


def _helper_oib() -> str:
    n = next(_helper_numbers)
    if n >= len(_HELPER_OIB_COUNTERS):
        raise RuntimeError(
            f"make_helper gives at most {len(_HELPER_OIB_COUNTERS)} helpers per test their "
            "own synthetic OIB; widen _HELPER_OIB_COUNTERS if a test needs more."
        )
    return synthetic_oib(_HELPER_OIB_COUNTERS[n])


def make_helper(name: str = "marko", **fields: Any) -> User:
    """A helper who can send offers: complete profile, ACTIVE payout account, verified phone."""
    user = make_user(name, phone_verified_at=timezone.now(), **fields)
    profile = helpers.update_profile(
        user,
        helpers.ProfileChanges(
            status=HelperProfile.Status.PENSIONER,
            legal_first_name="Marko",
            legal_last_name="Horvat",
            birth_date=dt.date(1960, 3, 1),
            address=helpers.Address("Put Republike 3", "20000", "Dubrovnik", "hr"),
            oib=_helper_oib(),
            iban=synthetic_iban(160),
            residence_country="hr",
            service_area_ids=[str(ServiceArea.objects.get(slug="dubrovnik").pk)],
            categories=[TaskCategory.GARDEN],
        ),
    )
    connect_account(profile)
    assert helpers.can_send_offers(user)
    return user


def open_task(poster: User | None = None, **fields: Any) -> Task:
    poster = poster or make_poster(f"poster{next(_numbers)}")
    # A real poster accepted the TERMS in force at sign-up; without it they would be in
    # limited mode and their task would take no offers (accounts.states).
    if current_document(LegalDocument.Kind.TERMS, "en") is not None:
        accept_current_terms(poster)
    task = tasks.post_task(poster, changes(**fields)).task
    assert task.status == Task.Status.OPEN
    return task

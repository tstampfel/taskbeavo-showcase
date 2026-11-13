"""Demo data for the public showcase (BEAV-198): `manage.py seed_demo`.

Fake Dubrovnik posters, helpers, tasks, offers and bookings, so a visitor can sign in as
each demo user and click through the built screens (docs/demo/README.md). Everything is
obviously fake: names that mean "example" (Primjer, Beispiel, Exemple), `@demo.example.com`
addresses, "Demo ulica" streets, OIBs that start with eight zeros (with a valid check
digit), synthetic IBANs of zeros and a counter (with valid check digits), and no phone
numbers at all.

Every booking gets where it is through the real code: the offer through
`offers.services.send_offer`, the booking through `checkout.accept_offer`, payment
through the demo Stripe stand-in (`payments/demo_stripe.py`: the demo card confirms the
PaymentIntent, and its payment_intent.succeeded event goes through the webhook handler
to FUNDS_HELD), a cancellation through `cancel.cancel_booking` and its settlement, a
no-show through `arrival`. Request and release payment have no service yet (BEAV-116 to
BEAV-118), so those two bookings move with `state_machine.transition` as the helper and
the poster, like every move: booking_events and outbox rows included. No payout is made.

What the code can't do is make time pass: a booking whose job is over gets its dates
moved back after it was paid (`move_back`), and every paid booking's acceptance is dated
two hours back (`age_acceptance`), past the free hour after accepting. Each seeded
booking gets a `demo_seed` BookingEvent saying it was seeded and what was moved.

Idempotent: each scenario is keyed by its task (poster and title), seeded all or nothing,
and skipped when it exists; users are looked up by email. Refused in production, next to
a live Stripe key and without DEMO_PAYMENTS (`refuse_outside_demo`).

The times are relative to the seed, so the demo goes stale: `seed_demo --reset` retires
the current demo users (`retire_demo_users`) and seeds a fresh set relative to now.
Retiring is not deleting: bookings, their events and the checkout consent records are
append-only legal evidence (the consent table refuses DELETE in the database), so they
stay, tied to an anonymised, deactivated user. Only users on the demo domain whose
address is one of the fixed demo addresses are retired.
"""

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Session, User
from accounts.sessions import revoke_all_sessions
from areas.models import ServiceArea
from bookings import arrival, cancel, checkout, settlement, state_machine
from bookings.models import Booking, BookingEvent
from bookings.money import fee_schedule_at
from care_contacts.gdpr import delete_care_contacts
from core.categories import TaskCategory
from core.crypto import pii_digest
from core.i18n import SUPPORTED_LOCALES
from core.models import ActorKind
from core.synthetic_identifiers import SYNTHETIC_OIB_PREFIX, synthetic_iban, synthetic_oib
from helpers import services as helpers
from helpers.models import HelperProfile
from legal.models import LegalDocument, TermsAcceptance
from legal.services import NewVersion, current_document, publish
from offers import services as offers
from offers.models import Offer
from payments import connect, demo_stripe
from tasks import services as tasks
from tasks.models import Task

# A subdomain of the reserved example.com used for nothing else, so a demo address can't
# be mistaken for anyone's: --reset touches only this domain and the fixed addresses below.
DOMAIN = "demo.example.com"
# BookingEvent data (IDs and enum keys only): "seeded by seed_demo".
SEED_SOURCE = "SEED_DEMO"
# The demo card the seed pays with (Stripe's test card number; the stand-in takes any
# number but the declined one).
DEMO_CARD = "4242424242424242"
# Every demo OIB is these eight zeros, a two-digit counter and the check digit: it passes
# the check digit, as the helper forms require, and is plainly not anyone's number.
DEMO_OIB_PREFIX = SYNTHETIC_OIB_PREFIX
# What retiring a demo helper clears, so the next set can take the same demo OIBs.
RETIRED_HELPER_FIELDS = [
    "oib",
    "oib_digest",
    "business_oib",
    "business_oib_digest",
    "iban",
    "vat_id",
]
DEMO_LEGAL_VERSION = "demo-1"

# How long before the seed each booking was accepted: past cancellation.GRACE_PERIOD (the
# free hour after accepting), so the cancel screens show the free and the late cancel.
ACCEPTED_AGE = dt.timedelta(hours=2)

# Points inside the seeded Dubrovnik service area: the middle of Gruž and of Lapad.
GRUZ = (42.6610, 18.0850)
LAPAD = (42.6550, 18.0700)


def demo_oib(counter: int) -> str:
    """The synthetic OIB for `counter` (core.synthetic_identifiers)."""
    return synthetic_oib(counter)


def demo_iban(counter: int) -> str:
    """A synthetic Croatian IBAN: an account number of zeros and the counter, with valid
    ISO 7064 mod-97 check digits. Bank code 0000000 belongs to no bank."""
    return synthetic_iban(counter)


def _free_demo_oib(counter: int) -> str:
    """The demo OIB for `counter`, or the next one (every tenth counter) that no helper
    holds: OIBs are unique across helpers. Retiring a demo helper releases its OIB, so
    this normally returns the first."""
    for candidate in range(counter, 100, 10):
        oib = demo_oib(candidate)
        digest = pii_digest("oib", oib)
        if not HelperProfile.objects.filter(
            Q(oib_digest=digest) | Q(business_oib_digest=digest)
        ).exists():
            return oib
    raise RuntimeError("No free demo OIB left.")


@dataclass(frozen=True)
class DemoUser:
    key: str
    first_name: str
    last_name: str
    locale: str
    country: str
    time_zone: str
    role: str
    note: str
    helper_status: HelperProfile.Status | None = None
    business_name: str = ""
    # Picks the helper's demo OIB, IBAN and street.
    counter: int = 0

    @property
    def email(self) -> str:
        return f"{self.key}@{DOMAIN}"


POSTERS = [
    DemoUser(
        "klaus.beispiel",
        "Klaus",
        "Beispiel",
        "de",
        "DE",
        "Europe/Berlin",
        "poster",
        "Lives in Germany and books help for his mother in Dubrovnik (German).",
    ),
    DemoUser(
        "claire.exemple",
        "Claire",
        "Exemple",
        "fr",
        "CH",
        "Europe/Zurich",
        "poster",
        "Lives in Switzerland and books help for her father (French).",
    ),
    DemoUser(
        "mara.primjer",
        "Mara",
        "Primjer",
        "hr",
        "HR",
        "Europe/Zagreb",
        "poster",
        "Lives in Dubrovnik (Croatian).",
    ),
]

HELPERS = [
    DemoUser(
        "ivo.primjeric",
        "Ivo",
        "Primjerić",
        "hr",
        "HR",
        "Europe/Zagreb",
        "helper",
        "Private helper, a pensioner.",
        helper_status=HelperProfile.Status.PENSIONER,
        counter=1,
    ),
    DemoUser(
        "petra.uzorak",
        "Petra",
        "Uzorak",
        "en",
        "HR",
        "Europe/Zagreb",
        "helper",
        "Private helper, a student (English).",
        helper_status=HelperProfile.Status.STUDENT,
        counter=2,
    ),
    DemoUser(
        "luka.testic",
        "Luka",
        "Testić",
        "hr",
        "HR",
        "Europe/Zagreb",
        "helper",
        "Registered business (obrt): shows a business name and OIB on offers.",
        helper_status=HelperProfile.Status.BUSINESS,
        business_name="Obrt Primjer popravci (demo)",
        counter=3,
    ),
]

USERS = {user.key: user for user in [*POSTERS, *HELPERS]}


class DemoSeedRefused(Exception):
    """The demo seed never runs in production, next to a live key or without DEMO_PAYMENTS."""


def refuse_outside_demo() -> None:
    """Checked by every entry point here, not only by the seed_demo command."""
    if settings.APP_ENV == "production":
        raise DemoSeedRefused("seed_demo never runs in production.")
    if settings.STRIPE_SECRET_KEY.startswith(("sk_live_", "rk_live_")):
        raise DemoSeedRefused("seed_demo never runs next to a live Stripe key.")
    if not settings.DEMO_PAYMENTS:
        raise DemoSeedRefused(
            "seed_demo pays its bookings through the demo Stripe stand-in: "
            "set DEMO_PAYMENTS=1 (and keep it on while you use the demo)."
        )


def retire_demo_users() -> list[str]:
    """Retire the current demo users so a fresh set can take their addresses.

    Each user is deactivated and its address and name replaced: its open tasks closed
    through the task service, its sessions revoked (which also forgets its push tokens)
    and its on-site contacts deleted. A demo helper's OIB, IBAN and VAT ID are cleared, so
    the next set can take the same demo OIBs; the rest of its profile keeps the fake
    details it was seeded with. Its bookings, events, consent records
    and terms acceptances are legal evidence and stay as they are. The demo payout
    accounts live only in the demo Stripe stand-in, so nothing is cleaned in Stripe.

    TODO(BEAV-153): use the account deletion flow's anonymisation once it exists.
    """
    refuse_outside_demo()
    retired = []
    users = User.objects.filter(
        email__in=[demo.email for demo in USERS.values()], email__endswith=f"@{DOMAIN}"
    )
    for user in users:
        with transaction.atomic():
            for task in Task.objects.filter(poster=user).exclude(status=Task.Status.CLOSED):
                tasks.close_task(task)
            revoke_all_sessions(user)
            delete_care_contacts(user)
            profile = HelperProfile.objects.filter(user=user).first()
            if profile is not None:
                profile.oib = profile.business_oib = profile.iban = profile.vat_id = ""
                profile.oib_digest = None
                profile.business_oib_digest = ""
                profile.save(update_fields=RETIRED_HELPER_FIELDS)
            old = user.email
            user.email = f"retired-{uuid.uuid4().hex[:12]}@{DOMAIN}"
            user.first_name = "Retired"
            user.last_name = "demo user"
            user.is_active = False
            user.save(update_fields=["email", "first_name", "last_name", "is_active"])
        retired.append(old)
    return retired


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    bookings: dict[str, Booking] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    key: str
    poster: str
    title: str
    description: str
    category: TaskCategory
    point: tuple[float, float]
    days_ahead: float
    budget_cents: int
    offers: list[tuple[str, int, str]]
    # Takes the first offer through checkout and then this, or None to leave the task open.
    then: Callable[["Seeder", Booking], Booking] | None


class Seeder:
    def __init__(self, now: dt.datetime | None = None) -> None:
        refuse_outside_demo()
        self.now = now or timezone.now()
        self.report = SeedReport()
        self.users: dict[str, User] = {}

    # --- Setup. ---

    def run(self) -> SeedReport:
        self._legal_documents()
        for demo in [*POSTERS, *HELPERS]:
            self.users[demo.key] = self._user(demo)
        for demo in HELPERS:
            self._helper(demo)
        for scenario in SCENARIOS:
            self._scenario(scenario)
        demo_stripe.deliver_events()
        return self.report

    def _legal_documents(self) -> None:
        """Draft TERMS and CANCELLATION texts, unless versions are already in force:
        sign-up and checkout refuse without them. Placeholders, marked as drafts."""
        for kind, body in (
            (LegalDocument.Kind.TERMS, _terms_body),
            (LegalDocument.Kind.CANCELLATION, _cancellation_body),
        ):
            if current_document(kind, "en", at=self.now) is not None:
                continue
            if LegalDocument.objects.filter(kind=kind, version=DEMO_LEGAL_VERSION).exists():
                continue
            publish(
                NewVersion(
                    kind=kind,
                    version=DEMO_LEGAL_VERSION,
                    bodies={code: body(code) for code in SUPPORTED_LOCALES},
                    effective_at=self.now,
                    material_change=False,
                    is_draft=True,
                ),
                now=self.now,
            )
            self.report.created.append(f"legal document {kind} {DEMO_LEGAL_VERSION} (draft)")

    def _user(self, demo: DemoUser) -> User:
        user = User.objects.filter(email=demo.email).first()
        if user is None:
            user = User.objects.create_user(
                demo.email,
                first_name=demo.first_name,
                last_name=demo.last_name,
                locale=demo.locale,
                country=demo.country,
                time_zone=demo.time_zone,
                adult_confirmed_at=self.now,
                phone_verified_at=self.now,
            )
            self.report.created.append(f"user {demo.email}")
        terms = current_document(LegalDocument.Kind.TERMS, demo.locale, at=self.now)
        if (
            terms is not None
            and not TermsAcceptance.objects.filter(user=user, document=terms).exists()
        ):
            TermsAcceptance.objects.create(user=user, document=terms, platform=Session.Platform.WEB)
        return user

    def _helper(self, demo: DemoUser) -> None:
        user = self.users[demo.key]
        if helpers.can_send_offers(user):
            return
        trader = demo.helper_status == HelperProfile.Status.BUSINESS
        street = f"Demo ulica {demo.counter}"
        helpers.update_profile(
            user,
            helpers.ProfileChanges(
                status=demo.helper_status,
                business=(
                    helpers.Business(
                        HelperProfile.BusinessKind.OBRT, demo.business_name, None, False
                    )
                    if trader
                    else None
                ),
                business_address=(
                    helpers.Address("Demo ulica 99", "20000", "Dubrovnik", "hr") if trader else None
                ),
                legal_first_name=demo.first_name,
                legal_last_name=demo.last_name,
                birth_date=dt.date(1990, 1, 1)
                if demo.helper_status != HelperProfile.Status.PENSIONER
                else dt.date(1955, 1, 1),
                address=helpers.Address(street, "20000", "Dubrovnik", "hr"),
                oib=_free_demo_oib(demo.counter),
                iban=demo_iban(demo.counter),
                residence_country="hr",
                service_area_ids=[str(ServiceArea.objects.get(slug="dubrovnik").pk)],
                categories=list(TaskCategory),
            ),
        )
        # The demo stand-in's Express account is enabled at once; its account.updated
        # event goes through the webhook handler like Stripe's.
        connect.start_onboarding(user)
        demo_stripe.deliver_events()
        self.report.created.append(f"helper profile and demo payout account for {demo.email}")

    # --- Scenarios. ---

    def _scenario(self, scenario: Scenario) -> None:
        poster = self.users[scenario.poster]
        task = Task.objects.filter(poster=poster, title=scenario.title).first()
        if task is not None:
            self.report.skipped.append(scenario.key)
            self.report.tasks[scenario.key] = task
            booking = Booking.objects.filter(task=task).order_by("-created_at").first()
            if booking is not None:
                self.report.bookings[scenario.key] = booking
            return
        # All or nothing: a scenario that fails halfway (say, no CANCELLATION policy in
        # force) leaves no task behind, so the next run seeds it again instead of skipping
        # it. The demo Stripe events it queued are delivered within the same transaction.
        with transaction.atomic():
            self._create_scenario(scenario, poster)

    def _create_scenario(self, scenario: Scenario, poster: User) -> None:
        start = (self.now + dt.timedelta(days=scenario.days_ahead)).replace(second=0, microsecond=0)
        task = tasks.post_task(
            poster,
            tasks.TaskChanges(
                category=scenario.category,
                title=scenario.title,
                description=scenario.description,
                location=tasks.Location("Demo ulica 1, 20000 Dubrovnik", *scenario.point),
                access_details="Demo access note: ring the bell marked DEMO.",
                window_start=start,
                window_end=start + dt.timedelta(hours=2),
                budget_cents=scenario.budget_cents,
            ),
        ).task
        self.report.tasks[scenario.key] = task
        sent = [
            offers.send_offer(self.users[helper], str(task.pk), price, message)
            for helper, price, message in scenario.offers
        ]
        if scenario.then is None:
            self.report.created.append(f"{scenario.key}: open task with {len(sent)} offers")
            return
        booking = self._pay(sent[0])
        booking = scenario.then(self, booking)
        self.report.bookings[scenario.key] = booking
        self.report.created.append(f"{scenario.key}: booking {booking.status}")

    def _pay(self, offer: Offer) -> Booking:
        """Accept the offer at checkout and pay with the demo card: FUNDS_HELD once the
        payment_intent.succeeded event has gone through the webhook handler."""
        poster = offer.task.poster
        trader = HelperProfile.objects.get(user=offer.helper).is_trader
        cancellation = current_document(
            LegalDocument.Kind.CANCELLATION, poster.locale, at=timezone.now()
        )
        if cancellation is None:
            raise RuntimeError("No CANCELLATION policy is in force.")
        price = offer.price_cents
        session = checkout.accept_offer(
            poster,
            str(offer.pk),
            expected_price_cents=price,
            expected_total_cents=price + state_machine.service_fee_cents(price),
            expected_fee_schedule_version=fee_schedule_at().version,
            consent=checkout.CheckoutConsent(
                cancellation_policy_accepted=True,
                early_start_requested=True,
                cancellation_version=cancellation.version,
                platform=Session.Platform.WEB,
                language=poster.locale,
                variant="trader" if trader else "private",
            ),
        )
        booking = Booking.objects.get(pk=session.booking.pk)
        demo_stripe.confirm_payment_intent(
            booking.payment_intent_id,
            session.client_secret,
            DEMO_CARD,
        )
        demo_stripe.deliver_events()
        booking.refresh_from_db()
        if booking.status != Booking.Status.FUNDS_HELD:
            raise RuntimeError(f"The demo payment didn't confirm booking {booking.pk}.")
        return self.age_acceptance(booking)

    def age_acceptance(self, booking: Booking) -> Booking:
        """Date the acceptance ACCEPTED_AGE back, past the free hour after accepting, so
        cancelling shows the policy for the start time (free or late), as it would for a
        booking made earlier. The job's dates stay as they are."""
        Booking.objects.filter(pk=booking.pk).update(created_at=booking.created_at - ACCEPTED_AGE)
        self.note(booking, accepted_moved_back_seconds=int(ACCEPTED_AGE.total_seconds()))
        booking.refresh_from_db()
        return booking

    def note(self, booking: Booking, **data: Any) -> None:
        BookingEvent.objects.create(
            booking=booking,
            type="demo_seed",
            actor_kind=ActorKind.SYSTEM,
            data={"source": SEED_SOURCE, **data},
        )

    def move_back(self, booking: Booking, start: dt.datetime) -> Booking:
        """Move the job to `start` (in the past): the dates only, never the status."""
        shift = booking.slot_start - start
        with transaction.atomic():
            Task.objects.filter(pk=booking.task_id).update(
                window_start=start,
                window_end=start + dt.timedelta(hours=2),
            )
            Booking.objects.filter(pk=booking.pk).update(
                slot_start=start, created_at=booking.created_at - shift
            )
            self.note(booking, moved_back_seconds=int(shift.total_seconds()))
        booking.refresh_from_db()
        return booking


# --- What each scenario does after the payment. ---


def _held(seeder: Seeder, booking: Booking) -> Booking:
    seeder.note(booking)
    return booking


def _payment_requested(seeder: Seeder, booking: Booking) -> Booking:
    booking = seeder.move_back(booking, seeder.now - dt.timedelta(days=1))
    helper = User.objects.get(pk=booking.helper_id)
    # TODO(BEAV-116): the helper's "Request payment" service; until then the state machine.
    return state_machine.transition(
        booking.pk,
        Booking.Status.PAYMENT_REQUESTED,
        actor_kind=ActorKind.HELPER,
        actor=helper,
        data={"source": SEED_SOURCE},
    )


def _released(seeder: Seeder, booking: Booking) -> Booking:
    booking = _payment_requested(seeder, booking)
    poster = User.objects.get(pk=booking.poster_id)
    # TODO(BEAV-117): "Release payment" creates the payout; the seed makes none.
    return state_machine.transition(
        booking.pk,
        Booking.Status.RELEASED,
        actor_kind=ActorKind.POSTER,
        actor=poster,
        data={"source": SEED_SOURCE},
    )


def _cancelled(seeder: Seeder, booking: Booking) -> Booking:
    """The poster cancels more than 24 hours ahead: a full refund, settled at the demo
    Stripe stand-in and confirmed by its charge.refunded event."""
    seeder.note(booking)
    poster = User.objects.get(pk=booking.poster_id)
    cancel.cancel_booking(poster, booking.pk, cancel.PosterCancelReason.DIFFERENT_DATE)
    settlement.settle(str(booking.pk))
    demo_stripe.deliver_events()
    booking.refresh_from_db()
    return booking


def _no_show_contested(seeder: Seeder, booking: Booking) -> Booking:
    """The job started two hours ago; the poster says the helper didn't come, and the
    helper answers "I was there", which goes to support."""
    booking = seeder.move_back(booking, seeder.now - dt.timedelta(hours=2))
    poster = User.objects.get(pk=booking.poster_id)
    helper = User.objects.get(pk=booking.helper_id)
    arrival.confirm_helper_came(poster, booking.pk, came=False)
    return arrival.contest_no_show(helper, booking.pk)


SCENARIOS = [
    Scenario(
        "open-task",
        "klaus.beispiel",
        "Check on my mother's flat and water the plants",
        "My mother is in hospital this week. Please water the plants and air the flat.",
        TaskCategory.CHECK_IN,
        GRUZ,
        4,
        3_000,
        [
            ("ivo.primjeric", 2_500, "I live nearby and can go in the morning."),
            ("luka.testic", 3_500, "Invoice from my obrt included."),
        ],
        None,
    ),
    Scenario(
        "funds-held",
        "claire.exemple",
        "Fix a dripping kitchen tap",
        "The kitchen tap in my father's flat drips all night. The washer probably needs changing.",
        TaskCategory.REPAIRS,
        LAPAD,
        3,
        4_000,
        [("luka.testic", 4_500, "I have washers for most taps with me.")],
        _held,
    ),
    Scenario(
        "on-my-way",
        "mara.primjer",
        "Carry shopping up to the fourth floor",
        "Two bags from the market up to the fourth floor; there is no lift.",
        TaskCategory.ERRANDS,
        GRUZ,
        2 / 24,
        1_500,
        [("ivo.primjeric", 1_500, "")],
        _held,
    ),
    Scenario(
        "late-cancel",
        "klaus.beispiel",
        "Feed the cat and change the litter",
        "My mother is visiting me in Germany; please feed her cat tomorrow.",
        TaskCategory.CHECK_IN,
        LAPAD,
        20 / 24,
        1_500,
        [("ivo.primjeric", 1_500, "")],
        _held,
    ),
    Scenario(
        "payment-requested",
        "klaus.beispiel",
        "Mow the lawn and trim the hedge",
        "The garden behind my mother's house needs mowing and the hedge trimming.",
        TaskCategory.GARDEN,
        LAPAD,
        3,
        3_500,
        [("petra.uzorak", 3_500, "I can bring the mower.")],
        _payment_requested,
    ),
    Scenario(
        "released",
        "mara.primjer",
        "Pharmacy run for my neighbour",
        "Pick up a prescription at the pharmacy and bring it to my neighbour.",
        TaskCategory.ERRANDS,
        GRUZ,
        3,
        1_200,
        [("petra.uzorak", 1_200, "")],
        _released,
    ),
    Scenario(
        "cancelled",
        "claire.exemple",
        "Clean the windows before the holidays",
        "Clean the windows of a two-room flat, inside and out.",
        TaskCategory.CLEANING,
        LAPAD,
        5,
        4_000,
        [("ivo.primjeric", 3_800, "")],
        _cancelled,
    ),
    Scenario(
        "no-show-contested",
        "klaus.beispiel",
        "Take the bins out and check the post",
        "Take the bins out and check my mother's post box while she is away.",
        TaskCategory.CHECK_IN,
        GRUZ,
        3,
        1_500,
        [("petra.uzorak", 1_500, "")],
        _no_show_contested,
    ),
]


def _terms_body(code: str) -> str:
    return f"DRAFT - needs professional translation and lawyer review ({code})"


def _cancellation_body(code: str) -> str:
    pairs = "".join(
        f"<!-- checkout:cancellation_policy_{variant} -->DRAFT: I accept the cancellation "
        f"policy, {label} ({code}).<!-- /checkout:cancellation_policy_{variant} -->\n\n"
        f"<!-- checkout:early_start_{variant} -->DRAFT: I ask for the service to start "
        f"before the withdrawal period ends, {label} ({code})."
        f"<!-- /checkout:early_start_{variant} -->\n\n"
        for variant, label in (("private", "private helper"), ("trader", "registered business"))
    )
    return f"DRAFT - needs professional translation and lawyer review ({code})\n\n{pairs}"

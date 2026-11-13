"""Stripe Express onboarding and `account.updated` (plan: "Becoming a helper", "Payout
failures and account problems")."""

import copy
import datetime as dt
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import make_user
from areas.models import ServiceArea
from core import outbox
from core.categories import TaskCategory
from core.errors import ApiError
from core.models import Outbox
from helpers import services
from helpers.models import HelperProfile
from notifications.models import Delivery
from notifications.tests.conftest import FakeExpo, register_device
from payments import connect, tasks
from payments.models import ConnectedAccount, ConnectedAccountEvent, StripeEvent
from payments.tests.factories import connect_account
from payments.tests.stripe_fakes import load_event, sign

Status = ConnectedAccount.Status
SECRET = "whsec_test_connect_secret_not_secret"
ACCOUNT = "acct_1TestHelperExpress"
NOW = 1793516400
PAYOUT_ACCOUNT = services.Requirement.PAYOUT_ACCOUNT


def complete_helper(name: str = "marija", **user_fields: Any) -> tuple[User, HelperProfile]:
    user = make_user(name, phone_verified_at=timezone.now(), **user_fields)
    profile = services.update_profile(
        user,
        services.ProfileChanges(
            status=HelperProfile.Status.PENSIONER,
            legal_first_name="Marija",
            legal_last_name="Kovač",
            birth_date=dt.date(1955, 6, 1),
            address=services.Address("Put Republike 3", "20000", "Dubrovnik", "HR"),
            oib="00000000698",
            iban="HR0600000000000000160",
            residence_country="HR",
            service_area_ids=[str(ServiceArea.objects.get(slug="dubrovnik").pk)],
            categories=[TaskCategory.CHECK_IN],
        ),
    )
    return user, profile


def account_event(
    *,
    event_id: str = "evt_1TestAccountUpdated",
    created: int = NOW,
    account: str = ACCOUNT,
    helper_profile_id: int | None = None,
    **changes: Any,
) -> dict[str, Any]:
    """The recorded `account.updated` fixture, with `changes` applied to the account and
    `requirements_<key>` to its requirements."""
    event = copy.deepcopy(load_event("account.updated"))
    event.update(id=event_id, created=created, account=account)
    obj = event["data"]["object"]
    obj["id"] = account
    obj["metadata"] = (
        {"helper_profile_id": str(helper_profile_id)} if helper_profile_id is not None else {}
    )
    for key, value in changes.items():
        if key.startswith("requirements_"):
            obj["requirements"][key.removeprefix("requirements_")] = value
        else:
            obj[key] = value
    return event


def post(client: Client, event: dict[str, Any]) -> Any:
    body = json.dumps(event).encode()
    with override_settings(STRIPE_WEBHOOK_SECRETS=["whsec_platform", SECRET]):
        return client.post(
            "/webhooks/stripe",
            data=body,
            content_type="application/json",
            headers={"Stripe-Signature": sign(body, SECRET)},
        )


# --- Deriving the status from Stripe's account object -----------------------------------


ACTIVE = {
    "details_submitted": True,
    "charges_enabled": True,
    "capabilities": {"card_payments": "active", "transfers": "active"},
}


@pytest.mark.parametrize(
    ("account", "status"),
    [
        (ACTIVE, Status.ACTIVE),
        ({**ACTIVE, "details_submitted": False}, Status.INCOMPLETE),
        (
            {**ACTIVE, "requirements": {"currently_due": ["individual.verification.document"]}},
            Status.NEEDS_INFO,
        ),
        ({**ACTIVE, "requirements": {"past_due": ["external_account"]}}, Status.NEEDS_INFO),
        # Destination charges need both capabilities, not just charges_enabled.
        (
            {**ACTIVE, "capabilities": {"card_payments": "active", "transfers": "pending"}},
            Status.VERIFYING,
        ),
        (
            {
                **ACTIVE,
                "charges_enabled": False,
                "requirements": {
                    "disabled_reason": "requirements.pending_verification",
                    "pending_verification": ["individual.verification.document"],
                },
            },
            Status.VERIFYING,
        ),
        (
            {
                **ACTIVE,
                "charges_enabled": False,
                "requirements": {"disabled_reason": "under_review"},
            },
            Status.RESTRICTED,
        ),
        (
            {
                **ACTIVE,
                "charges_enabled": False,
                "requirements": {"disabled_reason": "rejected.fraud"},
            },
            Status.REJECTED,
        ),
        (
            {"details_submitted": False, "requirements": {"disabled_reason": "rejected.listed"}},
            Status.REJECTED,
        ),
    ],
)
def test_the_status_follows_stripes_account(account: dict[str, Any], status: Status) -> None:
    assert connect.account_state(account).status == status


def test_requirements_due_join_past_and_currently_due_without_duplicates() -> None:
    state = connect.account_state(
        {
            "requirements": {
                "past_due": ["external_account"],
                "currently_due": ["external_account", "individual.id_number"],
                "current_deadline": NOW,
            }
        }
    )
    assert state.requirements_due == ["external_account", "individual.id_number"]
    assert state.current_deadline == dt.datetime.fromtimestamp(NOW, tz=dt.UTC)


# --- Offers blocked until the account can receive destination charges --------------------


@pytest.mark.django_db
class TestSendingOffers:
    def test_a_complete_profile_without_a_payout_account_cant_send_offers(self) -> None:
        marija, profile = complete_helper()

        assert services.missing_requirements(marija, profile) == [
            services.Requirement.PAYOUT_ACCOUNT
        ]
        assert not services.can_send_offers(marija)
        with pytest.raises(ApiError) as raised:
            services.require_can_send_offers(marija)
        assert raised.value.code.name == "PAYOUT_ACCOUNT_NOT_READY"

    @pytest.mark.parametrize("status", [s for s in Status if s != Status.ACTIVE])
    def test_only_an_active_account_lets_the_helper_send_offers(self, status: Status) -> None:
        marija, profile = complete_helper()
        connect_account(profile, status)

        assert services.missing_requirements(marija, profile) == [
            services.Requirement.PAYOUT_ACCOUNT
        ]
        with pytest.raises(ApiError):
            services.require_can_send_offers(marija)

    def test_an_active_account_completes_the_profile(self) -> None:
        marija, profile = complete_helper()
        connect_account(profile)

        assert services.require_can_send_offers(marija) == profile

    def test_other_missing_details_still_say_the_profile_is_incomplete(self) -> None:
        marija = make_user("marija", phone_verified_at=timezone.now())
        services.update_profile(
            marija, services.ProfileChanges(status=HelperProfile.Status.PENSIONER)
        )

        with pytest.raises(ApiError) as raised:
            services.require_can_send_offers(marija)
        assert raised.value.code.name == "HELPER_PROFILE_INCOMPLETE"

    def test_without_a_profile_the_payout_account_is_listed_too(self) -> None:
        marija = make_user("marija")
        assert "PAYOUT_ACCOUNT" in [r.value for r in services.missing_requirements(marija, None)]


# --- The public profile (product decision on BEAV-95) -----------------------------------


@pytest.mark.django_db
class TestPublicProfile:
    @pytest.mark.parametrize(
        ("status", "public"),
        [
            (Status.ACTIVE, True),
            (Status.NEEDS_INFO, True),
            (Status.VERIFYING, True),
            (Status.RESTRICTED, True),
            (Status.INCOMPLETE, False),
            (Status.REJECTED, False),
        ],
    )
    def test_stays_visible_while_stripe_works_on_a_submitted_account(
        self, status: Status, public: bool
    ) -> None:
        marija, profile = complete_helper()
        connect_account(profile, status)

        assert services.is_public(marija, profile) is public

    def test_is_hidden_without_a_payout_account(self) -> None:
        marija, profile = complete_helper()
        assert not services.is_public(marija, profile)

    def test_other_missing_details_still_hide_it(self) -> None:
        marija, profile = complete_helper()
        connect_account(profile, Status.NEEDS_INFO)
        profile = services.update_profile(marija, services.ProfileChanges(categories=[]))

        assert not services.is_public(marija, profile)

    def test_the_query_shows_a_helper_whose_offers_are_paused(self, client: Client) -> None:
        marija, profile = complete_helper()
        connect_account(profile, Status.NEEDS_INFO)
        query = "query($userId: ID!) { helperProfile(userId: $userId) { id } }"

        body = client.post(
            "/graphql",
            {"query": query, "variables": {"userId": str(marija.pk)}},
            content_type="application/json",
        ).json()

        assert body["data"]["helperProfile"] == {"id": str(profile.pk)}
        assert not services.can_send_offers(marija)


# --- account.updated --------------------------------------------------------------------


@pytest.mark.django_db
class TestAccountUpdated:
    def test_an_active_account_lets_the_helper_send_offers(self, client: Client) -> None:
        marija, profile = complete_helper()
        connect_account(profile, Status.INCOMPLETE, stripe_account_id=ACCOUNT)

        response = post(client, account_event())

        assert response.status_code == 200
        account = ConnectedAccount.objects.get()
        assert account.status == Status.ACTIVE
        assert account.stripe_state_at == dt.datetime.fromtimestamp(NOW, tz=dt.UTC)
        assert (account.charges_enabled, account.payouts_enabled) == (True, True)
        assert services.can_send_offers(marija)
        event = ConnectedAccountEvent.objects.get()
        assert event.data == {
            "from": "INCOMPLETE",
            "to": "ACTIVE",
            "stripe_event_id": "evt_1TestAccountUpdated",
        }
        assert event.actor_kind == "STRIPE_WEBHOOK"
        assert Outbox.objects.get().payload == {"status": "ACTIVE"}

    def test_requirements_due_pause_offers(self, client: Client) -> None:
        marija, profile = complete_helper()
        connect_account(profile, stripe_account_id=ACCOUNT)

        post(
            client,
            account_event(
                requirements_currently_due=["individual.verification.document"],
                requirements_current_deadline=NOW + 86400,
            ),
        )

        account = ConnectedAccount.objects.get()
        assert account.status == Status.NEEDS_INFO
        assert account.requirements_due == ["individual.verification.document"]
        assert not services.can_send_offers(marija)

    def test_a_replayed_event_is_applied_once(self, client: Client) -> None:
        _marija, profile = complete_helper()
        connect_account(profile, Status.INCOMPLETE, stripe_account_id=ACCOUNT)
        event = account_event()

        first, replay = post(client, event), post(client, event)

        assert first.status_code == replay.status_code == 200
        assert StripeEvent.objects.filter(pk=event["id"]).count() == 1
        assert ConnectedAccountEvent.objects.count() == 1
        assert Outbox.objects.count() == 1

    def test_an_older_event_arriving_late_is_ignored(self, client: Client) -> None:
        _marija, profile = complete_helper()
        connect_account(profile, Status.INCOMPLETE, stripe_account_id=ACCOUNT)

        post(client, account_event(event_id="evt_new", created=NOW))
        post(
            client,
            account_event(
                event_id="evt_old",
                created=NOW - 60,
                requirements_currently_due=["external_account"],
            ),
        )

        account = ConnectedAccount.objects.get()
        assert account.status == Status.ACTIVE
        assert account.requirements_due == []
        # Stored and processed, so it isn't retried.
        assert StripeEvent.objects.get(pk="evt_old").processed_at is not None

    def test_two_events_of_the_same_second_are_settled_by_reading_the_account(
        self, client: Client, gateway: "FakeGateway", django_capture_on_commit_callbacks: Any
    ) -> None:
        # Onboarding: "capabilities active" and the earlier "details submitted" share a second
        # and arrive newest first. Which is newer can't be told, so Stripe is asked.
        _marija, profile = complete_helper()
        connect_account(profile, Status.INCOMPLETE, stripe_account_id=ACCOUNT)
        newest = account_event(event_id="evt_active")
        older = account_event(
            event_id="evt_submitted",
            charges_enabled=False,
            capabilities={"card_payments": "pending", "transfers": "pending"},
            requirements_pending_verification=["individual.verification.document"],
        )
        gateway.current = copy.deepcopy(newest["data"]["object"])

        post(client, newest)
        with django_capture_on_commit_callbacks(execute=True):
            post(client, older)

        assert gateway.reads == [ACCOUNT]
        account = ConnectedAccount.objects.get()
        assert account.status == Status.ACTIVE
        assert account.stripe_state_at is not None
        assert account.stripe_state_at >= dt.datetime.fromtimestamp(NOW, tz=dt.UTC)
        # Only the real change was recorded; the reconcile found nothing new.
        assert list(ConnectedAccountEvent.objects.values_list("data__to", flat=True)) == ["ACTIVE"]

    def test_a_reconcile_corrects_a_tie_applied_in_the_wrong_order(
        self, gateway: "FakeGateway"
    ) -> None:
        _marija, profile = complete_helper()
        account = connect_account(profile, Status.VERIFYING, stripe_account_id=ACCOUNT)
        ConnectedAccount.objects.update(stripe_state_at=dt.datetime.fromtimestamp(NOW, tz=dt.UTC))
        gateway.current = copy.deepcopy(account_event()["data"]["object"])

        connect.reconcile_account(account.pk)

        account.refresh_from_db()
        assert account.status == Status.ACTIVE
        assert ConnectedAccountEvent.objects.get().data == {
            "from": "VERIFYING",
            "to": "ACTIVE",
            "source": "reconcile",
        }

    def test_a_webhook_from_the_reads_second_is_kept_and_read_again(
        self,
        gateway: "FakeGateway",
        monkeypatch: pytest.MonkeyPatch,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        # R2-1: the read starts at second T; while it is in flight an account.updated
        # created at T (capabilities now active) is applied. The read's answer may predate
        # that change, so it must not overwrite it; another read is queued instead.
        _marija, profile = complete_helper()
        account = connect_account(profile, Status.VERIFYING, stripe_account_id=ACCOUNT)
        read_second = dt.datetime.fromtimestamp(NOW + 30, tz=dt.UTC)
        monkeypatch.setattr(connect.timezone, "now", lambda: read_second.replace(microsecond=1))
        stale = copy.deepcopy(account_event()["data"]["object"])
        stale["capabilities"] = {"card_payments": "pending", "transfers": "pending"}
        retrieve = gateway.retrieve_account

        def webhook_lands_during_the_read(*, connected_account: str) -> dict[str, Any]:
            with transaction.atomic():
                connect.apply_account_update(account_event(created=NOW + 30))
            return retrieve(connected_account=connected_account)

        monkeypatch.setattr(gateway, "retrieve_account", webhook_lands_during_the_read)
        gateway.current = stale
        queued: list[tuple[Any, ...]] = []
        monkeypatch.setattr(
            tasks.reconcile_connected_account,
            "apply_async",
            lambda args, countdown: queued.append((args, countdown)),
        )

        with django_capture_on_commit_callbacks(execute=True):
            connect.reconcile_account(account.pk)

        account.refresh_from_db()
        assert account.status == Status.ACTIVE
        assert account.stripe_state_at == read_second
        assert queued == [((account.pk,), connect.RECONCILE_RETRY_SECONDS)]
        assert list(ConnectedAccountEvent.objects.values_list("data__to", flat=True)) == ["ACTIVE"]

    def test_a_reconcile_keeps_the_stamp_of_the_last_webhook(self, gateway: "FakeGateway") -> None:
        _marija, profile = complete_helper()
        account = connect_account(profile, Status.VERIFYING, stripe_account_id=ACCOUNT)
        stamp = dt.datetime.fromtimestamp(NOW, tz=dt.UTC)
        ConnectedAccount.objects.update(stripe_state_at=stamp)
        gateway.current = copy.deepcopy(account_event()["data"]["object"])

        connect.reconcile_account(account.pk)

        account.refresh_from_db()
        assert (account.status, account.stripe_state_at) == (Status.ACTIVE, stamp)

    def test_the_daily_sweep_reads_back_unsettled_accounts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        swept: list[int] = []
        monkeypatch.setattr(tasks.reconcile_connected_account, "delay", swept.append)
        accounts = {}
        for number, status in enumerate(Status):
            profile = HelperProfile.objects.create(user=make_user(f"helper{number}"))
            accounts[status] = connect_account(profile, status).pk

        assert tasks.sweep_unsettled_connected_accounts() == 4
        assert sorted(swept) == sorted(
            accounts[s]
            for s in (Status.INCOMPLETE, Status.NEEDS_INFO, Status.VERIFYING, Status.RESTRICTED)
        )

    def test_a_reconcile_never_overwrites_a_newer_event(self, gateway: "FakeGateway") -> None:
        _marija, profile = complete_helper()
        account = connect_account(profile, stripe_account_id=ACCOUNT)
        later = timezone.now() + dt.timedelta(minutes=5)
        ConnectedAccount.objects.update(stripe_state_at=later)
        gateway.current = {"details_submitted": False}

        connect.reconcile_account(account.pk)

        account.refresh_from_db()
        assert (account.status, account.stripe_state_at) == (Status.ACTIVE, later)

    def test_an_account_no_helper_owns_is_ignored(self, client: Client) -> None:
        response = post(client, account_event(account="acct_1SomeoneElse"))

        assert response.status_code == 200
        assert not ConnectedAccount.objects.exists()

    def test_a_webhook_ahead_of_the_onboarding_mutation_stores_the_account(
        self, client: Client
    ) -> None:
        _marija, profile = complete_helper()

        post(client, account_event(helper_profile_id=profile.pk))

        account = ConnectedAccount.objects.get()
        assert (account.helper, account.stripe_account_id) == (profile, ACCOUNT)
        assert account.status == Status.ACTIVE

    def test_metadata_never_moves_a_helper_to_another_account(self, client: Client) -> None:
        _marija, profile = complete_helper()
        connect_account(profile, stripe_account_id="acct_1TheHelpersOwn")

        post(client, account_event(account="acct_1NotTheirs", helper_profile_id=profile.pk))

        assert list(ConnectedAccount.objects.values_list("stripe_account_id", flat=True)) == [
            "acct_1TheHelpersOwn"
        ]

    def test_a_rejection_is_kept_with_its_reason(self, client: Client) -> None:
        _marija, profile = complete_helper()
        connect_account(profile, stripe_account_id=ACCOUNT)

        post(
            client,
            account_event(charges_enabled=False, requirements_disabled_reason="rejected.fraud"),
        )

        account = ConnectedAccount.objects.get()
        assert (account.status, account.disabled_reason) == (Status.REJECTED, "rejected.fraud")


# --- Push and email: "more info needed", rejected, reminders ------------------------------


@pytest.mark.django_db(transaction=True)
class TestNotifications:
    @pytest.fixture
    def helper(self) -> Iterator[tuple[User, HelperProfile]]:
        # Transactional tests start without the seeded service areas; the profile's
        # completeness doesn't matter here.
        user = make_user("marija", locale="de")
        profile = HelperProfile.objects.create(user=user)
        register_device(user, "ExponentPushToken[marija-phone-01]")
        connect_account(profile, stripe_account_id=ACCOUNT)
        yield user, profile

    def test_needing_more_info_pushes_and_emails_the_helper(
        self, client: Client, fake_expo: FakeExpo, helper: tuple[User, HelperProfile]
    ) -> None:
        post(client, account_event(requirements_currently_due=["external_account"]))

        [push] = fake_expo.sent
        assert push.title == "Stripe benötigt weitere Angaben"
        assert push.data["route"] == "/helper/payouts"
        [email] = mail.outbox
        assert email.to == ["marija@example.com"]
        assert email.subject == "Stripe benötigt weitere Angaben"
        # Lock-screen safe: no requirement keys or names in the text.
        assert "external_account" not in push.body + email.body
        assert "Marija" not in push.body

    def test_a_rejection_tells_the_helper(
        self, client: Client, fake_expo: FakeExpo, helper: tuple[User, HelperProfile]
    ) -> None:
        post(
            client,
            account_event(charges_enabled=False, requirements_disabled_reason="rejected.other"),
        )

        assert [m.title for m in fake_expo.sent] == ["Auszahlungskonto nicht genehmigt"]
        assert [m.subject for m in mail.outbox] == ["Auszahlungskonto nicht genehmigt"]

    def test_becoming_active_sends_nothing(
        self, client: Client, fake_expo: FakeExpo, helper: tuple[User, HelperProfile]
    ) -> None:
        ConnectedAccount.objects.update(status=Status.VERIFYING)

        post(client, account_event())

        assert fake_expo.sent == [] and mail.outbox == []
        assert Outbox.objects.get().status == Outbox.Status.PUBLISHED

    def test_reminders_repeat_the_notice_at_most_three_times(
        self, client: Client, fake_expo: FakeExpo, helper: tuple[User, HelperProfile]
    ) -> None:
        post(client, account_event(requirements_currently_due=["external_account"]))
        start = timezone.now()

        assert connect.send_info_reminders(start + dt.timedelta(days=2)) == 0
        sent = [
            connect.send_info_reminders(start + dt.timedelta(days=3 * n + 1)) for n in range(1, 6)
        ]

        assert sent == [1, 1, 1, 0, 0]
        assert len(fake_expo.sent) == 4 and len(mail.outbox) == 4
        assert ConnectedAccount.objects.get().info_reminders_sent == 3

    def test_no_reminder_once_the_information_was_given(
        self, client: Client, fake_expo: FakeExpo, helper: tuple[User, HelperProfile]
    ) -> None:
        post(client, account_event(requirements_currently_due=["external_account"]))
        post(client, account_event(event_id="evt_done", created=NOW + 60))

        assert connect.send_info_reminders(timezone.now() + dt.timedelta(days=4)) == 0
        assert len(fake_expo.sent) == 1

    def test_a_notice_whose_status_moved_on_is_not_sent(
        self, fake_expo: FakeExpo, helper: tuple[User, HelperProfile]
    ) -> None:
        account = ConnectedAccount.objects.get()

        with transaction.atomic():
            row = outbox.enqueue(connect.STATUS_CHANGED_TOPIC, account, {"status": "NEEDS_INFO"})

        assert Outbox.objects.get(pk=row.pk).status == Outbox.Status.PUBLISHED
        assert fake_expo.sent == [] and not Delivery.objects.exists()


# --- startPayoutOnboarding --------------------------------------------------------------


@dataclass
class FakeGateway:
    accounts: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    # What retrieve_account answers: Stripe's current state of the account.
    current: dict[str, Any] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    def create_express_account(self, **kwargs: Any) -> SimpleNamespace:
        self.accounts.append(kwargs)
        return SimpleNamespace(id=ACCOUNT, object="account")

    def retrieve_account(self, *, connected_account: str) -> dict[str, Any]:
        self.reads.append(connected_account)
        return {"id": connected_account, "object": "account", **self.current}

    def create_onboarding_link(self, **kwargs: Any) -> SimpleNamespace:
        self.links.append(kwargs)
        return SimpleNamespace(
            object="account_link",
            url=f"https://connect.stripe.com/setup/e/{ACCOUNT}/link{len(self.links)}",
            expires_at=NOW + 300,
        )


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    fake = FakeGateway()
    monkeypatch.setattr(connect, "get_gateway", lambda: fake)
    return fake


@pytest.mark.django_db
class TestStartOnboarding:
    def test_the_first_call_opens_the_express_account(self, gateway: FakeGateway) -> None:
        marija, profile = complete_helper()

        link = connect.start_onboarding(marija)

        assert gateway.accounts == [
            {
                "helper_profile_id": profile.pk,
                "idempotency_key": f"helper:{profile.pk}:express-account:v1",
            }
        ]
        assert gateway.links == [
            {
                "connected_account": ACCOUNT,
                "return_url": "http://localhost:3000/helper/payouts/return",
                "refresh_url": "http://localhost:3000/helper/payouts/refresh",
                "idempotency_key": f"helper:{profile.pk}:account-link:1:v1",
            }
        ]
        assert link.url.endswith("/link1")
        assert link.expires_at == dt.datetime.fromtimestamp(NOW + 300, tz=dt.UTC)
        assert link.account.status == Status.INCOMPLETE
        assert ConnectedAccount.objects.get().stripe_account_id == ACCOUNT

    def test_later_calls_reuse_the_account_with_a_new_link(self, gateway: FakeGateway) -> None:
        marija, profile = complete_helper()

        connect.start_onboarding(marija)
        second = connect.start_onboarding(marija)

        assert len(gateway.accounts) == 1
        assert [link["idempotency_key"] for link in gateway.links] == [
            f"helper:{profile.pk}:account-link:1:v1",
            f"helper:{profile.pk}:account-link:2:v1",
        ]
        assert second.url.endswith("/link2")

    def test_an_account_stored_by_a_webhook_first_is_used(self, gateway: FakeGateway) -> None:
        marija, profile = complete_helper()
        connect_account(profile, Status.INCOMPLETE, stripe_account_id=ACCOUNT)

        connect.start_onboarding(marija)

        assert gateway.accounts == []
        assert gateway.links[0]["connected_account"] == ACCOUNT

    def test_a_double_tap_racing_to_store_the_account_keeps_one_row(
        self, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marija, profile = complete_helper()
        create_account = gateway.create_express_account

        def other_tap_stores_it_first(**kwargs: Any) -> SimpleNamespace:
            # The other tap commits the same account while this one waits for Stripe.
            connect_account(profile, Status.INCOMPLETE, stripe_account_id=ACCOUNT)
            return create_account(**kwargs)

        def insert_loses_the_race(**kwargs: Any) -> Any:
            raise IntegrityError

        monkeypatch.setattr(gateway, "create_express_account", other_tap_stores_it_first)
        monkeypatch.setattr(ConnectedAccount.objects, "get_or_create", insert_loses_the_race)

        link = connect.start_onboarding(marija)

        assert link.account == ConnectedAccount.objects.get()
        assert gateway.links[0]["connected_account"] == ACCOUNT

    @pytest.mark.parametrize("missing", ["address", "oib"])
    def test_needs_a_croatian_address_and_the_oib_first(
        self, gateway: FakeGateway, missing: str
    ) -> None:
        marija = make_user("marija")
        changes = {
            "address": services.Address("Put Republike 3", "20000", "Dubrovnik", "HR"),
            "oib": "00000000698",
        }
        del changes[missing]
        services.update_profile(marija, services.ProfileChanges(**changes))

        with pytest.raises(ApiError) as raised:
            connect.start_onboarding(marija)

        assert raised.value.code.name == "HELPER_PROFILE_INCOMPLETE"
        assert gateway.accounts == gateway.links == []

    def test_a_rejected_account_gets_no_link(self, gateway: FakeGateway) -> None:
        marija, profile = complete_helper()
        connect_account(profile, Status.REJECTED)

        with pytest.raises(ApiError) as raised:
            connect.start_onboarding(marija)

        assert raised.value.code.name == "PAYOUT_ACCOUNT_REJECTED"
        assert gateway.links == []

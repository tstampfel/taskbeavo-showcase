"""Account states and the CanCreateWork guard (plan: "Sign-up and login", "Reporting,
blocking, suspension and appeal", "Terms and conditions")."""

import datetime as dt
from typing import Any

import pytest
import strawberry
from django.core.cache import caches
from django.test import Client
from django.urls import path
from django.utils import timezone

from accounts import states
from accounts.models import User
from accounts.permissions import CanCreateWork, PhoneVerified
from accounts.states import AccountState
from accounts.tests.factories import access_token, make_user
from accounts.tests.test_schema import error_codes, gql
from config.schema import Mutation, build_schema
from config.views import GraphQLView
from legal.models import LegalDocument, TermsAcceptance
from legal.services import withdraw
from legal.tests.factories import publish_version

pytestmark = pytest.mark.django_db

TERMS = LegalDocument.Kind.TERMS
# Every mutation that creates new work; nothing else may use CanCreateWork, so finishing
# open bookings, chat, data export and deletion stay open in limited mode.
CREATING_WORK = {
    "postTask",
    "publishTaskDraft",
    "sendOffer",
    "updateOffer",
    "renewOffer",
    "acceptOffer",
}


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


def accept(user: User, version: str, language: str = "en") -> TermsAcceptance:
    document = LegalDocument.objects.get(kind=TERMS, version=version, language=language)
    return TermsAcceptance.objects.create(user=user, document=document, platform="web")


def publish_scheduled(version: str, *, days: int, material: bool, **fields: Any) -> None:
    """Publishes `version` effective `days` ago, with the 30-day notice before that."""
    now = timezone.now()
    publish_version(
        version=version,
        now=now - dt.timedelta(days=days + 31),
        effective_at=now - dt.timedelta(days=days),
        material_change=material,
        **fields,
    )


# --- TERMS_UPDATE_REQUIRED is worked out from the versions and acceptances ---------------


def test_no_terms_in_force_means_active() -> None:
    assert states.account_state(make_user("ana")) is AccountState.ACTIVE


def test_accepting_the_version_in_force_means_active() -> None:
    publish_scheduled("v1", days=40, material=False)
    ana = make_user("ana")
    accept(ana, "v1")

    assert states.account_state(ana) is AccountState.ACTIVE


def test_a_user_who_never_accepted_must_accept() -> None:
    publish_scheduled("v1", days=40, material=False)

    assert states.account_state(make_user("ana")) is AccountState.TERMS_UPDATE_REQUIRED


def test_a_material_version_in_force_needs_acceptance() -> None:
    publish_scheduled("v1", days=80, material=False)
    publish_scheduled("v2", days=1, material=True)
    ana = make_user("ana")
    accept(ana, "v1")

    assert states.must_accept_terms(ana) is True
    assert states.account_state(ana) is AccountState.TERMS_UPDATE_REQUIRED


def test_a_small_fix_doesnt_ask_again() -> None:
    publish_scheduled("v1", days=80, material=False)
    publish_scheduled("v1.1", days=1, material=False)
    ana = make_user("ana")
    accept(ana, "v1")

    assert states.account_state(ana) is AccountState.ACTIVE


def test_a_material_version_still_scheduled_doesnt_limit_yet() -> None:
    publish_scheduled("v1", days=80, material=False)
    publish_version(
        version="v2", effective_at=timezone.now() + dt.timedelta(days=31), material_change=True
    )
    ana = make_user("ana")
    accept(ana, "v1")

    assert states.account_state(ana) is AccountState.ACTIVE
    later = timezone.now() + dt.timedelta(days=32)
    assert states.account_state(ana, at=later) is AccountState.TERMS_UPDATE_REQUIRED


def test_a_withdrawn_version_is_skipped() -> None:
    publish_scheduled("v1", days=80, material=False)
    publish_version(
        version="v2", effective_at=timezone.now() + dt.timedelta(days=31), material_change=True
    )
    withdraw(TERMS, "v2", reason="Wrong text")
    ana = make_user("ana")
    accept(ana, "v1")

    later = timezone.now() + dt.timedelta(days=32)
    assert states.account_state(ana, at=later) is AccountState.ACTIVE


def test_accepting_a_withdrawn_version_doesnt_count_for_a_later_one() -> None:
    """An early acceptance of v2 (BEAV-82), v2 withdrawn, then a material v3 takes effect
    before v2's date: the user never saw v3 (review BEAV-77 R1-4)."""
    now = timezone.now()
    publish_scheduled("v1", days=80, material=False)
    publish_version(version="v2", effective_at=now + dt.timedelta(days=60), material_change=True)
    withdraw(TERMS, "v2", reason="Wrong text")
    ana = make_user("ana")
    accept(ana, "v1")
    accept(ana, "v2")
    publish_version(version="v3", effective_at=now + dt.timedelta(days=31), material_change=True)

    later = now + dt.timedelta(days=32)
    assert states.account_state(ana, at=later) is AccountState.TERMS_UPDATE_REQUIRED


def test_the_first_final_version_after_drafts_needs_acceptance(settings: Any) -> None:
    settings.LEGAL_DRAFTS_ALLOWED = True
    publish_scheduled("draft-1", days=80, material=False, is_draft=True)
    publish_scheduled("v1", days=1, material=False)
    ana = make_user("ana")
    accept(ana, "draft-1")

    assert states.account_state(ana) is AccountState.TERMS_UPDATE_REQUIRED


def test_accepting_the_version_in_force_lifts_the_restriction(
    client: Client, settings: Any
) -> None:
    """BEAV-82's accept mutation only has to insert the TERMS_ACCEPTANCE row: the guard
    looks again on every request, with no state to change."""
    publish_scheduled("v1", days=80, material=False)
    publish_scheduled("v2", days=1, material=True)
    ana = make_user("ana", phone_verified_at=timezone.now())
    accept(ana, "v1")
    token = access_token(ana)
    schema_with_stand_in(settings)

    assert error_codes(call(client, "postDummyTask", token)) == ["TERMS_UPDATE_REQUIRED"]

    accept(ana, "v2", language="de")

    assert call(client, "postDummyTask", token) == {"data": {"postDummyTask": True}}


# --- Stored states, and which wins -------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (User.Status.SUSPENDED, AccountState.SUSPENDED),
        (User.Status.DELETION_PENDING, AccountState.DELETION_PENDING),
    ],
)
def test_a_stored_status_wins_over_the_terms(status: str, state: AccountState) -> None:
    publish_scheduled("v1", days=40, material=False)

    assert states.account_state(make_user("ana", status=status)) is state


# --- The guard over HTTP -----------------------------------------------------------------


@strawberry.type
class GuardedMutation(Mutation):
    @strawberry.mutation(permission_classes=[CanCreateWork])
    async def post_dummy_task(self) -> bool:
        return True


def schema_with_stand_in(settings: Any) -> None:
    """Serves a schema with a guarded stand-in mutation at /graphql for the test."""
    view = GraphQLView.as_view(schema=build_schema(app_env="test", mutation=GuardedMutation))
    settings.ROOT_URLCONF = type("Urls", (), {"urlpatterns": [path("graphql", view)]})


def call(client: Client, field: str, token: str | None) -> dict[str, Any]:
    return gql(client, f"mutation {{ {field} }}", token=token)


def verified(name: str = "ana", **fields: Any) -> User:
    return make_user(name, phone_verified_at=timezone.now(), **fields)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (User.Status.SUSPENDED, "ACCOUNT_SUSPENDED"),
        (User.Status.DELETION_PENDING, "ACCOUNT_DELETION_PENDING"),
    ],
)
def test_a_limited_account_cant_create_work(
    client: Client, settings: Any, status: str, code: str
) -> None:
    schema_with_stand_in(settings)

    body = call(client, "postDummyTask", access_token(verified(status=status)))

    assert error_codes(body) == [code]
    assert body["data"] is None


def test_terms_required_cant_create_work(client: Client, settings: Any) -> None:
    publish_scheduled("v1", days=40, material=False)
    schema_with_stand_in(settings)

    body = call(client, "postDummyTask", access_token(verified()))

    assert error_codes(body) == ["TERMS_UPDATE_REQUIRED"]


def test_an_active_verified_account_passes(client: Client, settings: Any) -> None:
    schema_with_stand_in(settings)

    body = call(client, "postDummyTask", access_token(verified()))

    assert body == {"data": {"postDummyTask": True}}


def test_the_guard_still_needs_sign_in_and_a_phone(client: Client, settings: Any) -> None:
    schema_with_stand_in(settings)

    assert error_codes(call(client, "postDummyTask", None)) == ["UNAUTHENTICATED"]
    unverified = access_token(make_user("ivo"))
    assert error_codes(call(client, "postDummyTask", unverified)) == ["PHONE_NOT_VERIFIED"]


def test_the_state_is_reported_before_the_phone(client: Client, settings: Any) -> None:
    schema_with_stand_in(settings)
    token = access_token(make_user("ivo", status=User.Status.SUSPENDED))

    assert error_codes(call(client, "postDummyTask", token)) == ["ACCOUNT_SUSPENDED"]


# --- Which mutations use it ---------------------------------------------------------------


def _permissions_by_mutation() -> dict[str, list[type]]:
    definition = Mutation.__strawberry_definition__  # pyright: ignore[reportAttributeAccessIssue]
    schema = strawberry.Schema(query=_Query, mutation=Mutation)
    names = schema.config.name_converter
    return {
        names.get_graphql_name(field): list(field.permission_classes) for field in definition.fields
    }


@strawberry.type
class _Query:
    ok: bool = True


def test_exactly_the_mutations_that_create_work_use_the_guard() -> None:
    guarded = {
        name for name, classes in _permissions_by_mutation().items() if CanCreateWork in classes
    }

    assert guarded == CREATING_WORK


def test_the_guard_replaces_the_plain_phone_check_there() -> None:
    permissions = _permissions_by_mutation()

    assert all(PhoneVerified not in permissions[name] for name in CREATING_WORK)


# --- What a limited account keeps ----------------------------------------------------------

VIEWER = "{ viewer { id accountState } }"


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (User.Status.ACTIVE, "ACTIVE"),
        (User.Status.SUSPENDED, "SUSPENDED"),
        (User.Status.DELETION_PENDING, "DELETION_PENDING"),
    ],
)
def test_the_viewer_sees_its_state(client: Client, status: str, state: str) -> None:
    ana = verified(status=status)

    body = gql(client, VIEWER, token=access_token(ana))

    assert body == {"data": {"viewer": {"id": str(ana.pk), "accountState": state}}}


def test_the_viewer_sees_terms_update_required(client: Client) -> None:
    publish_scheduled("v1", days=40, material=False)

    body = gql(client, VIEWER, token=access_token(verified()))

    assert body["data"]["viewer"]["accountState"] == "TERMS_UPDATE_REQUIRED"

"""The PHONE_NOT_VERIFIED guard that postTask, sendOffer and sendMessage use."""

from typing import Any

import pytest
import strawberry
from django.test import Client
from django.urls import path
from django.utils import timezone
from strawberry.types import Info

from accounts.permissions import PhoneVerified, require_verified_phone
from accounts.tests.factories import access_token, make_user
from accounts.tests.test_schema import error_codes
from config.schema import Mutation, build_schema
from config.views import GraphQLView
from core.errors import ApiError

pytestmark = pytest.mark.django_db


@strawberry.type
class GuardedMutation(Mutation):
    """Stand-ins for the mutations that will use the guard (BEAV-85, 100, 104)."""

    @strawberry.mutation(permission_classes=[PhoneVerified])
    def post_dummy_task(self) -> bool:
        return True

    @strawberry.mutation
    async def send_dummy_message(self, info: Info[Any, Any]) -> bool:
        require_verified_phone(info.context.user)
        return True


@pytest.fixture
def guarded(settings: Any) -> Any:
    """Serves a schema with the stand-ins at /graphql for the test."""
    schema = build_schema(app_env="test", mutation=GuardedMutation)
    view = GraphQLView.as_view(schema=schema)
    urlpatterns = [path("graphql", view)]
    settings.ROOT_URLCONF = type("Urls", (), {"urlpatterns": urlpatterns})
    return schema


def call(client: Client, field: str, token: str | None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.post(
        "/graphql",
        {"query": f"mutation {{ {field} }}"},
        content_type="application/json",
        headers=headers,
    )
    return response.json()


@pytest.mark.parametrize("field", ["postDummyTask", "sendDummyMessage"])
def test_unverified_user_gets_phone_not_verified(client: Client, guarded: Any, field: str) -> None:
    body = call(client, field, access_token(make_user("ana")))

    assert error_codes(body) == ["PHONE_NOT_VERIFIED"]
    assert body["data"] is None


@pytest.mark.parametrize("field", ["postDummyTask", "sendDummyMessage"])
def test_verified_user_passes(client: Client, guarded: Any, field: str) -> None:
    user = make_user("ana", phone_verified_at=timezone.now())

    assert call(client, field, access_token(user)) == {"data": {field: True}}


@pytest.mark.parametrize("field", ["postDummyTask", "sendDummyMessage"])
def test_signed_out_gets_unauthenticated(client: Client, guarded: Any, field: str) -> None:
    assert error_codes(call(client, field, None)) == ["UNAUTHENTICATED"]


def test_inactive_user_is_refused() -> None:
    user = make_user("ana", phone_verified_at=timezone.now(), is_active=False)

    with pytest.raises(ApiError) as raised:
        require_verified_phone(user)

    assert raised.value.extensions == {"code": "UNAUTHENTICATED"}

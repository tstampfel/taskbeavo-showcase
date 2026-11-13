"""A GraphQL endpoint for the telemetry tests: one mutation that writes an outbox row
for the signed-in user, and one that crashes with personal data in hand."""

import strawberry
from asgiref.sync import sync_to_async
from django.db import transaction
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from strawberry.types import Info

from config.schema import build_schema
from config.views import GraphQLView
from core.outbox import enqueue

TRACE_TOPIC = "test.telemetry"


class ResolverCrashError(Exception):
    pass


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def notify(self, info: Info, note: str) -> int:
        user = info.context.user

        @sync_to_async
        def write() -> int:
            with transaction.atomic():
                return enqueue(TRACE_TOPIC, user).pk

        return await write()

    @strawberry.mutation
    async def crash(self, info: Info, chat_text: str, oib: str) -> int:
        who = str(info.context.user)  # The username (email): a local Sentry must not see.
        raise ResolverCrashError(f"{who} ({oib}) wrote: {chat_text}")


schema = build_schema(app_env="test", mutation=Mutation)

urlpatterns = [path("graphql", csrf_exempt(GraphQLView.as_view(schema=schema)))]

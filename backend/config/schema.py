"""The GraphQL schema served at /graphql and exported to packages/graphql/schema.graphql."""

from collections.abc import Callable

import strawberry
from django.conf import settings
from strawberry.extensions import (
    DisableIntrospection,
    MaxAliasesLimiter,
    MaxTokensLimiter,
    QueryDepthLimiter,
    SchemaExtension,
)
from strawberry_django.optimizer import DjangoOptimizerExtension

from accounts.schema import Mutation as AccountsMutation
from accounts.schema import Query as AccountsQuery
from areas.schema import Mutation as AreasMutation
from areas.schema import Query as AreasQuery
from bookings.schema import Mutation as BookingsMutation
from bookings.schema import Query as BookingsQuery
from bookings.schema import Subscription as BookingsSubscription
from care_contacts.schema import Mutation as CareContactsMutation
from care_contacts.schema import Query as CareContactsQuery
from config.graphql_limits import MutationRateLimiter, QueryComplexityLimiter
from config.graphql_tracing import GraphQLTracing
from core.errors import ErrorCode
from helpers.schema import Mutation as HelpersMutation
from helpers.schema import Query as HelpersQuery
from legal.schema import Mutation as LegalMutation
from legal.schema import Query as LegalQuery
from notifications.schema import Mutation as NotificationsMutation
from notifications.schema import Query as NotificationsQuery
from offers.schema import Mutation as OffersMutation
from offers.schema import Query as OffersQuery
from offers.schema import Subscription as OffersSubscription
from tasks.schema import Mutation as TasksMutation
from tasks.schema import Query as TasksQuery
from uploads.schema import Mutation as UploadsMutation
from uploads.schema import Query as UploadsQuery

API_VERSION = "0.1.0"


@strawberry.type
class Query(
    AccountsQuery,
    AreasQuery,
    BookingsQuery,
    CareContactsQuery,
    HelpersQuery,
    LegalQuery,
    NotificationsQuery,
    OffersQuery,
    TasksQuery,
    UploadsQuery,
):
    @strawberry.field(description="Version of the API contract, for client compatibility checks.")
    def api_version(self) -> str:
        return API_VERSION


def build_schema(
    *, app_env: str, mutation: type | None = None, subscription: type | None = None
) -> strawberry.Schema:
    """Hard rule d: depth, token, alias and complexity limits and the mutation rate limit
    always apply; no introspection in production.

    `mutation` and `subscription` are the root Mutation and Subscription types.
    Subscriptions are served over the WebSocket (config/ws.py) and build on
    `core.realtime.viewer_events`.
    """
    # Factories, so each request gets fresh extension instances.
    extensions: list[type[SchemaExtension] | Callable[[], SchemaExtension]] = [
        lambda: QueryDepthLimiter(max_depth=settings.GRAPHQL_MAX_DEPTH),
        lambda: MaxTokensLimiter(max_token_count=settings.GRAPHQL_MAX_TOKENS),
        lambda: MaxAliasesLimiter(max_alias_count=settings.GRAPHQL_MAX_ALIASES),
        lambda: QueryComplexityLimiter(
            max_complexity=settings.GRAPHQL_MAX_COMPLEXITY,
            list_multiplier=settings.GRAPHQL_COMPLEXITY_LIST_MULTIPLIER,
        ),
        MutationRateLimiter,
        DjangoOptimizerExtension,
        # A span per operation and resolver, with no arguments (config/graphql_tracing.py).
        GraphQLTracing,
    ]
    if app_env == "production":
        extensions.append(DisableIntrospection)
    return strawberry.Schema(
        query=Query,
        mutation=mutation,
        subscription=subscription,
        extensions=extensions,
        # Listing ErrorCode documents every code in schema.graphql, used in a field or not.
        types=[ErrorCode],
    )


@strawberry.type
class Mutation(
    AccountsMutation,
    AreasMutation,
    BookingsMutation,
    CareContactsMutation,
    HelpersMutation,
    LegalMutation,
    NotificationsMutation,
    OffersMutation,
    TasksMutation,
    UploadsMutation,
):
    pass


@strawberry.type
class Subscription(BookingsSubscription, OffersSubscription):
    pass


schema = build_schema(app_env=settings.APP_ENV, mutation=Mutation, subscription=Subscription)

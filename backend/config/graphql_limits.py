"""Query complexity limit and mutation rate limiting for the GraphQL schema (hard rule d).

Depth, token and alias limits come from Strawberry; these two fill the remaining gaps.
"""

import hmac
import ipaddress
import logging
import time
from collections.abc import Iterator, Mapping
from typing import Any

from django.conf import settings
from django.core.cache import caches
from graphql import ExecutionResult as GraphQLExecutionResult
from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    GraphQLOutputType,
    InlineFragmentNode,
    OperationDefinitionNode,
    SelectionSetNode,
    ValidationContext,
    ValidationRule,
    get_named_type,
    get_nullable_type,
    is_list_type,
)
from redis.exceptions import RedisError
from strawberry.extensions import AddValidationRules, SchemaExtension
from strawberry.types.graphql import OperationType

from config.ws import WSContext
from core.errors import ErrorCode

RATE_LIMITED = ErrorCode.RATE_LIMITED.name
MUTATIONS_REQUIRE_HTTP = ErrorCode.MUTATIONS_REQUIRE_HTTP.name
RATE_LIMIT_CACHE = "graphql_rate_limit"

logger = logging.getLogger(__name__)


class QueryComplexityLimiter(AddValidationRules):
    """Rejects operations whose estimated cost exceeds a maximum.

    Each field costs 1. Whatever a list field selects is multiplied by `list_multiplier`,
    an estimate of the page size, so `{ tasks { offers { id } } }` costs 1 + 10 x (1 + 10 x 1).
    """

    def __init__(self, max_complexity: int, list_multiplier: int) -> None:
        super().__init__([complexity_rule(max_complexity, list_multiplier)])


def complexity_rule(max_complexity: int, list_multiplier: int) -> type[ValidationRule]:
    class QueryComplexityRule(ValidationRule):
        def __init__(self, context: ValidationContext) -> None:
            super().__init__(context)
            self.fragments = {
                d.name.value: d
                for d in context.document.definitions
                if isinstance(d, FragmentDefinitionNode)
            }
            # A fragment's cost depends only on the fragment, so each is summed once; without
            # this, fragments spreading each other several times blow up exponentially.
            self.fragment_costs: dict[str, int] = {}

        def enter_operation_definition(self, node: OperationDefinitionNode, *_args: Any) -> None:
            root = self.context.schema.get_root_type(node.operation)
            if root is None:
                return
            cost = self.selection_cost(node.selection_set, root, frozenset())
            if cost > max_complexity:
                self.report_error(
                    GraphQLError(
                        f"Query complexity {cost} exceeds the maximum of {max_complexity}.",
                        node,
                    )
                )

        def selection_cost(
            self, selection_set: SelectionSetNode, parent: Any, seen: frozenset[str]
        ) -> int:
            cost = 0
            for selection in selection_set.selections:
                if isinstance(selection, FieldNode):
                    cost += self.field_cost(selection, parent, seen)
                elif isinstance(selection, InlineFragmentNode):
                    condition = selection.type_condition
                    target = (
                        self.context.schema.get_type(condition.name.value) if condition else parent
                    )
                    cost += self.selection_cost(selection.selection_set, target or parent, seen)
                elif isinstance(selection, FragmentSpreadNode):
                    name = selection.name.value
                    fragment = self.fragments.get(name)
                    if fragment is None or name in seen:
                        continue  # Unknown fragments and cycles are reported by graphql-core.
                    if name not in self.fragment_costs:
                        target = self.context.schema.get_type(fragment.type_condition.name.value)
                        self.fragment_costs[name] = self.selection_cost(
                            fragment.selection_set, target or parent, seen | {name}
                        )
                    cost += self.fragment_costs[name]
            return cost

        def field_cost(self, node: FieldNode, parent: Any, seen: frozenset[str]) -> int:
            name = node.name.value
            # __typename is free; introspection is capped by the depth and token limits.
            if name.startswith("__"):
                return 0
            # Object and interface types have fields; unions only reach fields via fragments.
            field = (getattr(parent, "fields", None) or {}).get(name)
            if field is None or node.selection_set is None:
                return 1
            field_type: GraphQLOutputType = field.type
            child_cost = self.selection_cost(node.selection_set, get_named_type(field_type), seen)
            if is_list_type(get_nullable_type(field_type)):  # pyright: ignore[reportArgumentType]
                child_cost *= list_multiplier
            return 1 + child_cost

    return QueryComplexityRule


class MutationRateLimiter(SchemaExtension):
    """Fixed-window rate limit per client and mutation field.

    Each top-level mutation field in an operation counts as one call, so aliasing the same
    mutation five times uses five calls. Signed-in users are counted per account, everyone
    else per client address. Over the limit, nothing in the operation runs and the result
    carries the error code RATE_LIMITED, which clients translate.

    Mutations are accepted over HTTP only. On the subscriptions WebSocket the client address
    is Render's proxy for every connection, so it can't be counted per client; Apollo sends
    mutations over HTTP anyway, and a WebSocket mutation gets MUTATIONS_REQUIRE_HTTP. The
    WebSocket context (config.ws.WSContext) is refused by type, whatever it carries; any
    other context without a Django request's META is refused too.

    If the counter store (Redis) is down, each web process counts on its own and a warning is
    logged: the limit stays in force, only looser (once per process), and mutations don't go
    down with Redis. Revisit per mutation (e.g. sign-in codes) when such mutations exist.

    Settings: GRAPHQL_MUTATION_RATE_LIMIT calls per GRAPHQL_MUTATION_RATE_WINDOW_SECONDS,
    with per-mutation limits in GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES ({"fieldName": n}).

    Queries that cost like a mutation (each `checkoutSession` reads from Stripe) are listed
    in GRAPHQL_QUERY_RATE_LIMITS and counted in the same windows, per signed-in account on
    HTTP and on the WebSocket alike; each may appear once per operation, so aliases can't
    multiply it. Signed-out requests aren't counted: those fields refuse them anyway.
    """

    def on_execute(self) -> Iterator[None]:
        ctx = self.execution_context
        if ctx.operation_type is OperationType.QUERY and ctx.graphql_document is not None:
            ctx.result = _limited_query(ctx.context, ctx.graphql_document, ctx.operation_name)
        if ctx.operation_type is OperationType.MUTATION and ctx.graphql_document is not None:
            meta = None if isinstance(ctx.context, WSContext) else _http_meta(ctx.context)
            if meta is None:
                ctx.result = _error("Send mutations over HTTP.", MUTATIONS_REQUIRE_HTTP)
            else:
                fields = _mutation_fields(ctx.graphql_document, ctx.operation_name)
                if _over_limit(_client_key(ctx.context, meta), fields):
                    ctx.result = _error("Too many requests; try again later.", RATE_LIMITED)
        yield


def _limited_query(
    context: Any, document: Any, operation_name: str | None
) -> GraphQLExecutionResult | None:
    """RATE_LIMITED when the query repeats or overuses a field in GRAPHQL_QUERY_RATE_LIMITS."""
    limits: Mapping[str, int] = settings.GRAPHQL_QUERY_RATE_LIMITS
    fields = [f for f in _mutation_fields(document, operation_name) if f in limits]
    if not fields:
        return None
    if len(fields) != len(set(fields)):
        return _error("Ask for this field once per request.", RATE_LIMITED)
    user = getattr(context, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    window = settings.GRAPHQL_MUTATION_RATE_WINDOW_SECONDS
    bucket = int(time.time()) // window
    over = False
    for field in fields:
        if _count(f"gql-rl:{field}:user:{user.pk}:{bucket}", window) > limits[field]:
            over = True
    return _error("Too many requests; try again later.", RATE_LIMITED) if over else None


def _error(message: str, code: str) -> GraphQLExecutionResult:
    return GraphQLExecutionResult(
        data=None, errors=[GraphQLError(message, extensions={"code": code})]
    )


def _http_meta(context: Any) -> Mapping[str, str] | None:
    """The Django request's META for an HTTP request, None for a WebSocket (Channels) one."""
    request = (
        context.get("request")
        if isinstance(context, Mapping)
        else getattr(context, "request", None)
    )
    return getattr(request, "META", None)


def _mutation_fields(document: Any, operation_name: str | None) -> list[str]:
    operations = [d for d in document.definitions if isinstance(d, OperationDefinitionNode)]
    fragments = {
        d.name.value: d for d in document.definitions if isinstance(d, FragmentDefinitionNode)
    }
    operation = next(
        (op for op in operations if op.name and op.name.value == operation_name),
        operations[0] if len(operations) == 1 else None,
    )
    if operation is None:
        return []
    return list(_root_field_names(operation.selection_set, fragments, frozenset()))


def _root_field_names(
    selection_set: SelectionSetNode,
    fragments: Mapping[str, FragmentDefinitionNode],
    seen: frozenset[str],
) -> Iterator[str]:
    for selection in selection_set.selections:
        if isinstance(selection, FieldNode):
            if not selection.name.value.startswith("__"):
                yield selection.name.value
        elif isinstance(selection, InlineFragmentNode):
            yield from _root_field_names(selection.selection_set, fragments, seen)
        elif isinstance(selection, FragmentSpreadNode):
            name = selection.name.value
            if name in fragments and name not in seen:
                fragment = fragments[name].selection_set
                yield from _root_field_names(fragment, fragments, seen | {name})


def _over_limit(client: str, fields: list[str]) -> bool:
    if not fields:
        return False
    window = settings.GRAPHQL_MUTATION_RATE_WINDOW_SECONDS
    bucket = int(time.time()) // window
    overrides: Mapping[str, int] = settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES
    over = False
    for field in fields:
        count = _count(f"gql-rl:{field}:{client}:{bucket}", window)
        if count > overrides.get(field, settings.GRAPHQL_MUTATION_RATE_LIMIT):
            over = True
    return over


def _count(key: str, window: int) -> int:
    """Increments and returns the counter, in Redis or, while Redis is down, in this process."""
    try:
        return incr_in_window(caches[RATE_LIMIT_CACHE], key, window)
    except RedisError as exc:
        # Degrade to a per-process count rather than fail every mutation (see
        # MutationRateLimiter). Only the error type is logged: the key holds an address.
        logger.warning("GraphQL rate limit store unavailable: %s", type(exc).__name__)
        return incr_in_window(caches["default"], key, window)


def incr_in_window(cache: Any, key: str, window: int) -> int:
    """Increments and returns the counter `key` in `cache`; it expires `window` seconds
    after its first increment. Raises the cache's errors (RedisError) to the caller."""
    # add() is a no-op when the key exists, so incr() is atomic across workers.
    cache.add(key, 0, timeout=window)
    try:
        return cache.incr(key)
    except ValueError:
        # The key expired between add() and incr(): start the count again.
        if cache.add(key, 1, timeout=window):
            return 1
        return cache.incr(key)


def _client_key(context: Any, meta: Mapping[str, str]) -> str:
    """`user:<pk>` for a signed-in user, else `ip:<address>`."""
    user = getattr(context, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.pk}"
    return f"ip:{http_client_ip(meta)}"


def http_client_ip(meta: Mapping[str, str]) -> str:
    """The client address, trusting X-Forwarded-For only as far as our own proxies.

    Each trusted proxy appends the address it received the request from, so with N proxies
    the Nth entry from the right is the real client; entries further left are client-supplied.

    The web app's /api/graphql route handler is one more hop, whose own address would put
    every browser in one bucket. It sends the visitor's address in X-TaskBeavo-Client-IP
    with WEB_PROXY_SECRET in X-TaskBeavo-Proxy-Secret; with a matching secret that address
    wins, and without one the header is ignored.
    """
    web_client = _web_proxy_client_ip(meta)
    if web_client is not None:
        return web_client
    proxies = settings.GRAPHQL_TRUSTED_PROXY_COUNT
    if proxies > 0:
        forwarded = [a.strip() for a in meta.get("HTTP_X_FORWARDED_FOR", "").split(",") if a]
        if len(forwarded) >= proxies:
            return forwarded[-proxies]
    return meta.get("REMOTE_ADDR", "unknown")


def _web_proxy_client_ip(meta: Mapping[str, str]) -> str | None:
    secret = settings.WEB_PROXY_SECRET
    sent = meta.get("HTTP_X_TASKBEAVO_PROXY_SECRET", "")
    if not secret or not sent:
        return None
    if not hmac.compare_digest(sent.encode(), secret.encode()):
        # Most likely Vercel and Render hold different values, which silently puts every
        # web visitor in one bucket. Neither the secret nor the address goes in the log.
        logger.warning(
            "X-TaskBeavo-Proxy-Secret does not match WEB_PROXY_SECRET; the web app's client "
            "address is ignored. Set the same WEB_PROXY_SECRET on Vercel and Render."
        )
        return None
    address = meta.get("HTTP_X_TASKBEAVO_CLIENT_IP", "").strip()
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return None

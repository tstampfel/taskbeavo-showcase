"""Strawberry's OpenTelemetry spans for both async and sync execution, without arguments.

Strawberry ships an async extension (whose resolver hook is a coroutine, so
`execute_sync` fails) and a sync one (whose resolver span ends before an async resolver
has run). This one ends each resolver span when its result is ready, either way.

Resolver arguments can be personal data, so none are recorded; config/telemetry.py's
allow-list also drops the GraphQL document the operation span carries.
"""

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from graphql import GraphQLResolveInfo
from opentelemetry import trace
from strawberry.extensions import LifecycleStep
from strawberry.extensions.tracing import OpenTelemetryExtensionSync
from strawberry.extensions.tracing.utils import should_skip_tracing
from strawberry.types import ExecutionContext


class GraphQLTracing(OpenTelemetryExtensionSync):
    def __init__(self, *, execution_context: ExecutionContext | None = None) -> None:
        super().__init__(execution_context=execution_context, arg_filter=lambda _args, _info: {})

    def resolve(
        self,
        _next: Callable[..., Any],
        root: Any,
        info: GraphQLResolveInfo,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if should_skip_tracing(_next, info):
            return _next(root, info, *args, **kwargs)

        span = self._tracer.start_span(
            f"GraphQL Resolving: {info.field_name}",
            context=trace.set_span_in_context(self._span_holder[LifecycleStep.OPERATION]),
        )
        self.add_tags(span, info, kwargs)
        try:
            with trace.use_span(span, end_on_exit=False):
                result = _next(root, info, *args, **kwargs)
        except BaseException:
            span.end()
            raise
        if not isawaitable(result):
            span.end()
            return result
        return self._finish(span, result)

    @staticmethod
    async def _finish(span: trace.Span, result: Awaitable[Any]) -> Any:
        with trace.use_span(span, end_on_exit=True):
            return await result

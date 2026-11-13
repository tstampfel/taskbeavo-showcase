"""W3C trace context helpers for rows that hand work to Celery (outbox, audit events).

Only `traceparent` and `tracestate` are stored. They carry trace and span IDs and
vendor flags, never personal data.
"""

from opentelemetry import context as otel_context
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_propagator = TraceContextTextMapPropagator()

# Column sizes for stored trace context. A version-00 traceparent is exactly 55
# characters. W3C allows a tracestate of 32 members of up to 256 characters each, and
# it comes from the client's request headers, so it is cut to fit rather than stored.
TRACEPARENT_MAX_LENGTH = 55
TRACESTATE_MAX_LENGTH = 512


def current_traceparent() -> tuple[str, str]:
    """Return `(traceparent, tracestate)` of the active span, or empty strings outside a trace.

    Both fit their columns: telemetry must never make the change it describes fail.
    A traceparent that is too long is dropped (the work starts a new trace), and a
    tracestate keeps only its leading members that fit, as W3C truncation allows.
    """
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    traceparent = carrier.get("traceparent", "")
    if len(traceparent) > TRACEPARENT_MAX_LENGTH:
        return "", ""
    return traceparent, _truncate_tracestate(carrier.get("tracestate", ""))


def context_from_traceparent(traceparent: str, tracestate: str = "") -> Context:
    """Rebuild the trace context saved in a row, so a span started under it joins that trace.

    An empty or malformed `traceparent` yields the current context: the work then
    starts a new trace instead of failing.
    """
    carrier = {"traceparent": traceparent} if traceparent else {}
    if traceparent and tracestate:
        carrier["tracestate"] = tracestate
    return _propagator.extract(carrier, context=otel_context.get_current())


def _truncate_tracestate(tracestate: str) -> str:
    kept: list[str] = []
    length = 0
    for member in tracestate.split(","):
        added = len(member) + (1 if kept else 0)
        if length + added > TRACESTATE_MAX_LENGTH:
            break
        kept.append(member)
        length += added
    return ",".join(kept)

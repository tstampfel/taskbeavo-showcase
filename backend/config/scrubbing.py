"""Sentry `before_send` and `before_breadcrumb`: no personal data leaves for Sentry.

Personal data here is names, email, OIB, IBAN, address, phone and chat text (CLAUDE.md,
Observability). Names and chat text can't be recognised by their value, so the scrubber
works by structure first and patterns second:

- Request bodies, cookies, query strings and every header except a few technical ones
  are dropped. The user keeps only its `id`.
- Exception messages are replaced: an `IntegrityError` or a `ValidationError` quotes the
  values that caused it. The type, module and stack trace stay; local variables never
  leave (`include_local_variables=False`, and frames' `vars` are dropped). The one
  exception is `core.errors.SafeMessageError`, whose message is built from codes, IDs
  and enum values by contract. To keep filtered errors debuggable, events are tagged
  with safe structure: a SafeMessageError's code and allow-listed IDs, and a database
  error's SQLSTATE, table and constraint names.
- A log record keeps its format string, never its arguments or the formatted text.
  Structured `extra` fields stay, so a log like core.i18n's locale fallback still shows
  its recipient type, id and reason.
- In every remaining mapping (extra, tags, breadcrumb data, contexts), a key that names
  personal data, access details, free text or a credential has its value replaced.
- In every remaining string, email addresses, IBANs, OIBs and phone numbers are replaced.
  The one exception is a value under an ID key that has, as a whole, that key's shape: a
  UUID under `booking.id`, a Stripe ID under `stripe.event_id`... Digit runs inside a
  random ID look like an OIB or a phone number, and filtering them would mangle the ID
  support needs. Anything else under an ID key is still scrubbed.

Expected business errors (a GraphQL error carrying an `ErrorCode`, or a client's invalid
query) are dropped: they are answers, not failures.
"""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from graphql import GraphQLError
from opentelemetry import trace

from config.telemetry import ALLOWED_SPAN_ATTRIBUTES
from core.errors import ErrorCode, SafeMessageError
from core.payloads import _PII_WORDS, _key_words

FILTERED = "[Filtered]"

# Words in a key that mark its value as personal or secret, on top of the "IDs only"
# words for outbox payloads. Matched against the key's words, so `recipient_type` and
# `context` pass while `full_name`, `phoneNumber` and `set-cookie` do not.
_SENSITIVE_KEY_WORDS = _PII_WORDS | {
    "mobile",
    "first",
    "last",
    "surname",
    "username",
    "user",
    "ip",
    "addr",
    "cookie",
    "cookies",
    "authorization",
    "auth",
    "session",
    "csrf",
    "credentials",
    "dsn",
    "signature",
    "query",
    "variables",
    "data",
    "content",
    "chat",
    "reply",
    "review",
    "bio",
    "params",
}

_VALUE_PATTERNS = (
    # Email addresses.
    re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+\.[A-Za-z]{2,}"),
    # IBANs, with or without the four-character grouping (HR has 21 characters).
    re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b"),
    # OIBs: exactly 11 digits.
    re.compile(r"(?<![\d.])\d{11}(?![\d.])"),
    # Phone numbers, also right after a letter or "_" (`tel0912345678`, `ref_091 234 5678`).
    # The boundary is a hex digit rather than any digit, and not two hex groups and "-" (the
    # only place in a UUID where these patterns can start): random hex (trace IDs, SHAs,
    # UUIDs) would otherwise match far more often. The price is that a number right after
    # a-f (`b0912345678`) or after two UUID-like groups (`dead-beef-0912345678`) is missed.
    # A "+" never occurs in hex, so the "+" form only needs no digit or "+" before it, and
    # nothing after it.
    # International: +385 91 234 5678, 00385912345678.
    re.compile(r"(?<![0-9+])\+\d[\d \t/().-]{6,}\d"),
    re.compile(r"(?<![0-9A-Fa-f+])(?<![0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-)00\d[\d \t/().-]{6,}\d"),
    # Croatian national: 091 234 5678, 020/123-456.
    re.compile(
        r"(?<![0-9A-Fa-f])(?<![0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-)"
        r"0\d{1,2}[ /-]?\d{3}[ -]?\d{3,4}(?![0-9A-Fa-f])"
    ),
)

# Tag, extra and breadcrumb keys whose value is an opaque ID (the allow-listed span
# attributes that name one), each with the one shape it accepts. Such a value skips
# `_VALUE_PATTERNS` only when, as a whole, it has its key's shape:
# - UUID primary keys take a lowercase UUID, with no further check: the digit runs
#   inside random UUIDs are what the patterns used to mangle. This is the case still
#   trusted by shape alone: a UUID-shaped string with an OIB or a phone number in its
#   hex groups is kept under these keys. `fiscal.receipt_id` is a UUID too
#   (FISCAL_RECEIPT's `storno_of` references it as one, docs/PLAN.md).
# - Stripe keys take a Stripe ID: a known prefix and "_", then at least 14 letters and
#   digits (the shortest real ones, `cus_`), at least one of them a letter and no run of
#   9 or more digits, so an OIB or a phone number dressed up as a Stripe ID is still
#   scrubbed.
# Integer primary keys (`user.id`, `outbox.id`, `helper_profile.id`) take no shape: a
# small integer passes the patterns anyway. Anything else under an ID key is scrubbed
# like any string.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_STRIPE_ID = re.compile(
    r"(?:evt|pi|po|ch|re|tr|acct|py|du|in|cus)_(?=[A-Za-z0-9]*[A-Za-z])(?![A-Za-z0-9]*\d{9})"
    r"[A-Za-z0-9]{14,}"
)
_ID_KEYS: dict[str, re.Pattern[str]] = {
    "task.id": _UUID,
    "offer.id": _UUID,
    "booking.id": _UUID,
    "upload.id": _UUID,
    "fiscal.receipt_id": _UUID,
    "stripe.event_id": _STRIPE_ID,
    "stripe.object_id": _STRIPE_ID,
    "stripe.account_id": _STRIPE_ID,
}
# OTel and Sentry trace and span IDs (Sentry's `contexts.trace`): random hex, which the
# OIB pattern used to mangle in about 2% of trace IDs. Kept only as whole lowercase hex
# of exactly their length. Like a UUID, such a value is trusted by its shape, so digits
# hidden in its hex are kept; the SDK and OTel write these from random bits.
_TRACE_IDS = {
    "trace_id": re.compile(r"[0-9a-f]{32}"),
    "span_id": re.compile(r"[0-9a-f]{16}"),
    "parent_span_id": re.compile(r"[0-9a-f]{16}"),
}

# Sentry event keys that may leave. Anything an integration adds later is dropped
# until someone reviews it and adds it here.
_EVENT_KEYS = frozenset(
    {
        "event_id",
        "level",
        "platform",
        "timestamp",
        "start_timestamp",
        "logger",
        "server_name",
        "release",
        "environment",
        "dist",
        "sdk",
        "modules",
        "tags",
        "extra",
        "contexts",
        "request",
        "user",
        "exception",
        "threads",
        "breadcrumbs",
        "logentry",
        "message",
        "fingerprint",
        "transaction",
        "transaction_info",
        "type",
        "_meta",
    }
)
# Contexts the SDK fills with runtime facts. `response` (a GraphQL response body) and
# anything else an integration adds is dropped.
_CONTEXT_KEYS = frozenset({"trace", "otel", "runtime", "os", "device", "app", "celery"})
_REQUEST_HEADERS = frozenset({"content-type", "content-length", "host", "traceparent"})


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Sentry `before_send`: drop expected errors, scrub the rest, tag the OTel trace."""
    if _is_expected_error(hint):
        return None
    record = hint.get("log_record")
    if record is not None and not isinstance(record.msg, str) and "logentry" in event:
        # Strawberry logs the GraphQLError itself, so the "format string" is its message.
        event["logentry"] = {"message": type(record.msg).__name__}
    event = {key: value for key, value in event.items() if key in _EVENT_KEYS}

    if "request" in event:
        event["request"] = _scrub_request(event["request"])
    if "user" in event:
        user_id = (event["user"] or {}).get("id")
        event["user"] = {"id": str(user_id)} if user_id is not None else {}
    chain = _exception_chain(hint)
    safe_types = {
        (type(exc).__module__, name)
        for exc in chain
        if isinstance(exc, SafeMessageError)
        for name in (type(exc).__name__, type(exc).__qualname__)
    }
    for value in (event.get("exception") or {}).get("values") or []:
        _scrub_exception(value, safe=(value.get("module"), value.get("type")) in safe_types)
    for value in (event.get("threads") or {}).get("values") or []:
        _drop_frame_vars(value)
    if "logentry" in event:
        event["logentry"] = _scrub_logentry(event["logentry"])
    if isinstance(event.get("message"), str):
        event["message"] = scrub_text(event["message"])
    if "contexts" in event:
        contexts = event["contexts"] or {}
        # These hold SDK runtime facts (`runtime.name` is "CPython"), so only their
        # strings are checked, not their keys.
        event["contexts"] = {
            key: scrub(value, keys=False) for key, value in contexts.items() if key in _CONTEXT_KEYS
        }
    for key in ("extra", "tags"):
        if key in event:
            event[key] = scrub(event[key])
    breadcrumbs = (event.get("breadcrumbs") or {}).get("values")
    if breadcrumbs is not None:
        event["breadcrumbs"]["values"] = [_scrub_breadcrumb(crumb) for crumb in breadcrumbs]

    _tag_error_structure(event, chain)
    _tag_trace(event)
    return event


def before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any] | None) -> dict[str, Any]:
    """Sentry `before_breadcrumb`: a log breadcrumb keeps its format string, not its args."""
    record = (hint or {}).get("log_record")
    if record is not None:
        message = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
        crumb = {**crumb, "message": message}
    return _scrub_breadcrumb(crumb)


def scrub(value: Any, key: str | None = None, *, keys: bool = True) -> Any:
    """Recursively replace values under sensitive keys and PII patterns in strings.

    With `keys=False` only the strings are checked.
    """
    if keys and key is not None and _is_sensitive_key(key):
        return FILTERED
    if isinstance(value, Mapping):
        return {k: scrub(v, k if isinstance(k, str) else None, keys=keys) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(item, keys=keys) for item in value]
    if isinstance(value, str):
        return scrub_id(key, value)
    return value


def scrub_id(key: str | None, text: str) -> str:
    """`text` as is when `key` names an ID and `text` has that key's shape (see `_ID_KEYS`
    and `_TRACE_IDS`), otherwise scrubbed like any string."""
    shape = _ID_KEYS.get(key) or _TRACE_IDS.get(key) if key is not None else None
    if shape is not None and shape.fullmatch(text):
        return text
    return scrub_text(text)


def scrub_text(text: str) -> str:
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(FILTERED, text)
    return text


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_WORDS & _key_words(key))


def _is_expected_error(hint: dict[str, Any]) -> bool:
    exc_info = hint.get("exc_info")
    exc = exc_info[1] if exc_info else None
    record = hint.get("log_record")
    if record is not None and isinstance(record.msg, GraphQLError):
        # Strawberry logs every error in a result, with the original error as exc_info.
        exc = record.msg
    if not isinstance(exc, GraphQLError):
        return False
    code = (exc.extensions or {}).get("code")
    if code in ErrorCode.__members__:
        return True
    # A GraphQLError with no original error is the client's: a syntax or validation
    # error, or a limit. A resolver that crashed has its exception as `original_error`.
    return exc.original_error is None


def _scrub_request(request: dict[str, Any]) -> dict[str, Any]:
    scrubbed: dict[str, Any] = {}
    if method := request.get("method"):
        scrubbed["method"] = method
    if url := request.get("url"):
        parts = urlsplit(url)
        scrubbed["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    headers = request.get("headers") or {}
    scrubbed["headers"] = {
        name: value for name, value in headers.items() if name.lower() in _REQUEST_HEADERS
    }
    return scrubbed


def _exception_chain(hint: dict[str, Any]) -> list[BaseException]:
    """The hint's exception with its causes and contexts, as Sentry reports them."""
    exc_info = hint.get("exc_info")
    exc = exc_info[1] if exc_info else None
    chain: list[BaseException] = []
    while isinstance(exc, BaseException) and exc not in chain:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    return chain


def _scrub_exception(value: dict[str, Any], *, safe: bool) -> None:
    if "value" in value:
        # A SafeMessageError's message is still checked, in case the contract was broken.
        value["value"] = scrub_text(str(value["value"])) if safe else FILTERED
    _drop_frame_vars(value)


def _drop_frame_vars(value: dict[str, Any]) -> None:
    for frame in (value.get("stacktrace") or {}).get("frames") or []:
        frame.pop("vars", None)


def _tag_error_structure(event: dict[str, Any], chain: list[BaseException]) -> None:
    """Tags that keep a filtered error debuggable: codes, allow-listed IDs, schema names."""
    tags: dict[str, str] = {}
    for exc in chain:
        if isinstance(exc, SafeMessageError):
            if exc.code:
                tags.setdefault("error.code", str(exc.code))
            for key, value in exc.ids.items():
                if key in ALLOWED_SPAN_ATTRIBUTES:
                    tags.setdefault(key, scrub_id(key, str(value)))
        # psycopg errors (the cause of Django's IntegrityError and friends) carry the
        # failing table and constraint: schema names, never the row's values.
        diag = getattr(exc, "diag", None)
        if diag is not None:
            for tag, field in (
                ("db.sqlstate", "sqlstate"),
                ("db.table", "table_name"),
                ("db.constraint", "constraint_name"),
            ):
                if (name := getattr(diag, field, None)) and tag not in tags:
                    tags[tag] = str(name)[:200]
    if tags:
        event["tags"] = {**tags, **(event.get("tags") or {})}


def _scrub_logentry(logentry: dict[str, Any]) -> dict[str, Any]:
    # The format string is written by us; the arguments and the formatted text are not.
    message = logentry.get("message")
    scrubbed: dict[str, Any] = {"message": scrub_text(message) if isinstance(message, str) else ""}
    params = logentry.get("params")
    if params:
        scrubbed["params"] = [
            param if isinstance(param, int | float | bool) or param is None else FILTERED
            for param in params
        ]
    return scrubbed


def _scrub_breadcrumb(crumb: dict[str, Any]) -> dict[str, Any]:
    crumb = dict(crumb)
    if isinstance(crumb.get("message"), str):
        crumb["message"] = scrub_text(crumb["message"])
    data = crumb.get("data")
    if isinstance(data, Mapping):
        data = dict(data)
        # Outgoing HTTP breadcrumbs carry the query string and fragment separately.
        for key in ("http.query", "http.fragment"):
            data.pop(key, None)
        if isinstance(data.get("url"), str):
            parts = urlsplit(data["url"])
            data["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        crumb["data"] = scrub(data)
    return crumb


def _tag_trace(event: dict[str, Any]) -> None:
    """Tag the event with the current OTel trace, so it links to the trace in Honeycomb."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return
    trace_id = f"{context.trace_id:032x}"
    event.setdefault("tags", {})["trace_id"] = trace_id
    event.setdefault("contexts", {})["otel"] = {
        "trace_id": trace_id,
        "span_id": f"{context.span_id:016x}",
    }

"""Legal document versions for tests."""

from datetime import datetime
from typing import Any

from django.utils import timezone

from core.i18n import SUPPORTED_LOCALES
from legal.models import LegalDocument
from legal.services import NewVersion, publish


def bodies(text: str = "Terms") -> dict[str, str]:
    return {code: f"{text} ({code})" for code in SUPPORTED_LOCALES}


def cancellation_body(code: str, text: str = "Cancellation policy") -> str:
    """A CANCELLATION text with both helper types' checkout tick-box pairs marked."""
    pairs = "".join(
        f"<!-- checkout:cancellation_policy_{variant} -->\n"
        f"I accept the policy ({variant}, {code}).\n"
        f"<!-- /checkout:cancellation_policy_{variant} -->\n\n"
        f"<!-- checkout:early_start_{variant} -->\n"
        f"Start early, please ({variant}, {code}).\n"
        f"<!-- /checkout:early_start_{variant} -->\n\n"
        for variant in ("private", "trader")
    )
    return f"{text} ({code})\n\n{pairs}"


def cancellation_bodies(text: str = "Cancellation policy") -> dict[str, str]:
    return {code: cancellation_body(code, text) for code in SUPPORTED_LOCALES}


def publish_version(
    kind: str = LegalDocument.Kind.TERMS,
    version: str = "2026-11",
    *,
    effective_at: datetime | None = None,
    now: datetime | None = None,
    **fields: Any,
) -> list[LegalDocument]:
    now = now or timezone.now()
    default = cancellation_bodies() if kind == LegalDocument.Kind.CANCELLATION else bodies()
    details: dict[str, Any] = {"material_change": False, "is_draft": False, "bodies": default}
    details.update(fields)
    return publish(
        NewVersion(kind=kind, version=version, effective_at=effective_at or now, **details),
        now=now,
    )

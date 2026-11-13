"""Writing the staff audit log (`staff.models.StaffAuditEvent`)."""

from typing import Any

from django.db import models

from core.models import ActorKind
from staff.models import StaffAction, StaffAuditEvent


def object_key(obj: models.Model) -> tuple[str, str]:
    """The ("app_label.model", id) pair the audit log and staff notes name an object by."""
    return obj._meta.label_lower, str(obj.pk)


def record(
    actor: Any,
    action: StaffAction,
    obj: models.Model | tuple[str, str],
    /,
    actor_kind: ActorKind = ActorKind.ADMIN,
    **data: Any,
) -> StaffAuditEvent:
    """Log that staff `actor` (None for the system, with `actor_kind` SYSTEM) did `action`
    on `obj`. `data` holds IDs and enum keys only; `AuditEvent.save` refuses anything that
    looks like personal data."""
    object_type, object_id = obj if isinstance(obj, tuple) else object_key(obj)
    return StaffAuditEvent.objects.create(
        type=action,
        actor_kind=actor_kind,
        actor=actor,
        object_type=object_type,
        object_id=object_id,
        data=data,
    )

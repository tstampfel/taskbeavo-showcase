"""The admin site: Django's, with every change made through it written to the staff audit
log (`staff.models.StaffAuditEvent`).

`register()` puts `AuditedModelAdmin` in front of each admin class, Django's own (users,
groups) included, so a new admin can't forget it. Reads of personal data are logged by
the views that show it (the booking evidence view, the helper tax ID reveal).
"""

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib import admin
from django.contrib.admin import widgets
from django.db import models
from django.db.models.constants import LOOKUP_SEP
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from staff import audit
from staff.models import StaffAction


class AuditedModelAdmin(admin.ModelAdmin):
    """Logs additions, changes, deletions and admin actions. Django's own `LogEntry` keeps
    the object's `__str__`, which our models keep free of personal data."""

    def log_addition(self, request: HttpRequest, object: models.Model, message: Any) -> Any:
        audit.record(request.user, StaffAction.ADMIN_ADD, object)
        return super().log_addition(request, object, message)

    def construct_change_message(
        self, request: HttpRequest, form: Any, formsets: Any, add: bool = False
    ) -> Any:
        # Field names, not the translated labels Django's own message holds.
        request._staff_changed_fields = list(form.changed_data)  # pyright: ignore[reportAttributeAccessIssue]
        return super().construct_change_message(request, form, formsets, add)

    def log_change(self, request: HttpRequest, object: models.Model, message: Any) -> Any:
        # Which fields changed, never their values.
        fields = getattr(request, "_staff_changed_fields", [])
        audit.record(request.user, StaffAction.ADMIN_CHANGE, object, fields=fields)
        return super().log_change(request, object, message)

    def log_deletions(self, request: HttpRequest, queryset: Any) -> Any:
        for obj in queryset:
            audit.record(request.user, StaffAction.ADMIN_DELETE, obj)
        return super().log_deletions(request, queryset)  # pyright: ignore[reportAttributeAccessIssue]

    def get_actions(self, request: HttpRequest) -> dict[str, Any]:
        # Each action logs the objects it really receives, "select all" included.
        return {
            name: (_audited(func, name), name, description)
            for name, (func, _name, description) in super().get_actions(request).items()
        }

    def lookup_allowed(self, lookup: str, value: Any, request: Any = None) -> bool:
        """Only the declared filters and date hierarchy. Django would also accept any
        lookup on the model's own fields (`?birth_date__year=1950`), which lets the list
        page answer questions about fields the admin hides, without an audit row."""
        allowed = {_filter_field(item) for item in self.list_filter}
        if self.date_hierarchy:
            allowed.add(self.date_hierarchy.split(LOOKUP_SEP, 1)[0])
        if lookup.split(LOOKUP_SEP, 1)[0] not in allowed and not self._limits_choices(
            lookup, value
        ):
            return False
        return super().lookup_allowed(lookup, value, request)

    def _limits_choices(self, lookup: str, value: Any) -> bool:
        """A `limit_choices_to` lookup of a raw-ID popup, which Django lets through."""
        for fk_lookup in self.model._meta.related_fkey_lookups:  # pyright: ignore[reportAttributeAccessIssue]
            if callable(fk_lookup):
                fk_lookup = fk_lookup()
            if (lookup, value) in widgets.url_params_from_lookup_dict(fk_lookup).items():
                return True
        return False


def _filter_field(item: Any) -> str | None:
    # A field name, or a (field name, FieldListFilter) pair. A SimpleListFilter class
    # reads its own parameter, which Django still checks here, before the filter runs.
    name = item[0] if isinstance(item, (list, tuple)) else item
    if isinstance(name, type) and issubclass(name, admin.SimpleListFilter):
        return name.parameter_name
    return name.split(LOOKUP_SEP, 1)[0] if isinstance(name, str) else None


def _audited(func: Callable[..., Any], name: str) -> Callable[..., Any]:
    # Deleting is logged per object by log_deletions.
    if name == "delete_selected":
        return func

    @wraps(func)
    def action(model_admin: Any, request: HttpRequest, queryset: Any) -> Any:
        # Before running: the action may change rows so they no longer match.
        pks = list(queryset.values_list("pk", flat=True))
        response = func(model_admin, request, queryset)
        label = model_admin.model._meta.label_lower
        for pk in pks:
            audit.record(request.user, StaffAction.ADMIN_ACTION, (label, str(pk)), action=name)
        return response

    return action


class StaffAdminSite(admin.AdminSite):
    site_header = _("TaskBeavo staff")
    site_title = _("TaskBeavo staff")
    index_title = _("Staff console")

    def register(self, model_or_iterable: Any, admin_class: Any = None, **options: Any) -> None:
        admin_class = admin_class or admin.ModelAdmin
        if not issubclass(admin_class, AuditedModelAdmin):
            admin_class = type(admin_class.__name__, (AuditedModelAdmin, admin_class), {})
        super().register(model_or_iterable, admin_class, **options)

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from staff.models import StaffAuditEvent


@admin.register(StaffAuditEvent)
class StaffAuditEventAdmin(admin.ModelAdmin):
    """The staff audit log, read-only. No role group gets it: who reads the log is a
    superuser (or a group given `staff.view_staffauditevent` by hand)."""

    list_display = ("created_at", "type", "actor", "object_type", "object_id")
    list_filter = ("type", "object_type")
    search_fields = ("=object_id",)
    raw_id_fields = ("actor",)
    readonly_fields = (
        "created_at",
        "type",
        "actor_kind",
        "actor",
        "object_type",
        "object_id",
        "data",
        "traceparent",
    )
    show_full_result_count = False

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

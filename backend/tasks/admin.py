from django.contrib import admin

from tasks.models import Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    """Held tasks are found by status; the review queue itself is BEAV-138. Address and
    access details aren't listed or searchable."""

    list_display = ("id", "status", "category", "service_area", "created_at", "published_at")
    list_filter = ("status", "category", "service_area")
    fields = (
        "status",
        "category",
        "title",
        "description",
        "moderation_verdict",
        "moderation_kinds",
        "hold_reasons",
        "created_at",
        "published_at",
        "closed_at",
    )
    readonly_fields = (
        "category",
        "title",
        "description",
        "moderation_verdict",
        "moderation_kinds",
        "hold_reasons",
        "created_at",
        "published_at",
        "closed_at",
    )

from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from areas.models import ServiceArea, WaitlistEntry


@admin.register(ServiceArea)
class ServiceAreaAdmin(GISModelAdmin):
    """The polygon is drawn and edited on an OpenStreetMap map."""

    list_display = ("name", "slug", "country_code", "active", "updated_at")
    list_filter = ("active", "country_code")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    """Demand per city. The email isn't listed or searchable; it is for the launch email."""

    list_display = ("city", "country_code", "category", "created_at", "launch_email_sent_at")
    list_filter = ("country_code", "category")
    exclude = ("email", "leave_token_digest")
    readonly_fields = (
        "city",
        "city_key",
        "country_code",
        "category",
        "consent_launch_email",
        "consent_version",
        "consent_given_at",
        "consent_locale",
        "launch_email_sent_at",
        "created_at",
    )

    def has_add_permission(self, request: object) -> bool:
        return False

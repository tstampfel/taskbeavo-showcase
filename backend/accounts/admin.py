from typing import Any

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse

from accounts import sessions
from accounts.models import PosterBusiness, PosterBusinessEvent, Session, User
from helpers.oib import masked_oib
from staff import audit
from staff.models import StaffAction


class PosterBusinessInline(admin.StackedInline):
    """The obrt or company the poster books for (accounts.business). Read-only: setting it
    withdraws private helpers' offers, which only the service does. The OIB only masked."""

    model = PosterBusiness
    can_delete = False
    fields = ("kind", "name", "oib_masked", "created_at", "updated_at")
    readonly_fields = fields

    @admin.display(description="OIB")
    def oib_masked(self, obj: PosterBusiness) -> str:
        return masked_oib(obj.oib)

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class PosterBusinessEventInline(admin.TabularInline):
    """Every time the poster set, changed or removed their business: who, when, the status
    before and after (BEAV-195). Append-only."""

    model = PosterBusinessEvent
    fk_name = "poster"
    can_delete = False
    extra = 0
    fields = ("created_at", "type", "actor_kind", "actor", "data")
    readonly_fields = fields
    ordering = ("-created_at", "-id")

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    inlines = (PosterBusinessInline, PosterBusinessEventInline)
    list_display = ("id", "email", "country", "locale", "status", "is_active", "date_joined")
    search_fields = ("email",)
    # `status` (accounts.states) shows here but is read-only for everyone, superusers
    # included: a restriction needs a statement of reasons and one appeal (plan:
    # "Reporting, blocking, suspension and appeal"; DSA Art. 17), which the trust-safety
    # suspend and lift flow will write, and DELETION_PENDING belongs to the GDPR deletion
    # flow (user decision 2026-11-04, review BEAV-77 R1-3). Suspending won't end
    # sessions: a suspended account still signs in to finish open bookings and appeal.
    list_filter = ("status", "is_active", "is_staff", "locale")
    # The phone number stays out of the admin: support verifies it by SMS code, never by
    # reading it (hard rule e). Only whether and when it was verified shows.
    exclude = ("password", "phone_number", "phone_digest")
    readonly_fields = (
        "adult_confirmed_at",
        "date_joined",
        "last_login",
        "phone_verified_at",
        "status",
    )

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        response = super().change_view(request, object_id, form_url, extra_context)
        # A name and email were shown: log who looked (staff.roles). Saving is logged as
        # ADMIN_CHANGE by the site.
        if request.method == "GET" and response.status_code == 200:
            audit.record(request.user, StaffAction.VIEW_USER, ("accounts.user", str(object_id)))
        return response


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "platform", "created_at", "last_used_at", "revoked_at")
    list_filter = ("platform",)
    raw_id_fields = ("user",)
    # Read-only: a session is revoked only through the action, which closes its
    # WebSockets and sends session_revoked like every other revocation.
    readonly_fields = (
        "user",
        "platform",
        "device_name",
        "device_hash",
        "location",
        "created_at",
        "last_used_at",
        "revoked_at",
    )
    actions = ("revoke",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    @admin.action(description="Revoke the selected sessions", permissions=["change"])
    def revoke(self, request: HttpRequest, queryset: QuerySet[Session]) -> None:
        count = sessions.revoke_sessions(queryset.values_list("pk", flat=True))
        self.message_user(request, f"Revoked {count} session(s).", messages.SUCCESS)

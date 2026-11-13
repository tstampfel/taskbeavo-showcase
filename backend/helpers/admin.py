from typing import Any

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import URLPattern, path
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from helpers.models import HelperProfile
from staff import audit
from staff.masking import mask
from staff.models import StaffAction
from staff.roles import Capability, can, is_staff

# The only fields staff can change here: a trader's registered business address.
EDITABLE = (
    "business_address_street",
    "business_address_postal_code",
    "business_address_city",
    "business_address_country",
)

# Encrypted at rest (hard rule e); shown masked, revealed only by the finance role.
TAX_ID_LABELS = {
    "oib": _("OIB"),
    "business_oib": _("Business OIB"),
    "iban": _("IBAN"),
    "vat_id": _("VAT ID"),
}


@admin.register(HelperProfile)
class HelperProfileAdmin(admin.ModelAdmin):
    """Read-only, except a trader's registered business address, which support and finance
    may correct (`helpers.change_helperprofile`, staff migration 0004; the
    BUSINESS_ADDRESS_REQUIRED error sends helpers to support). The site audits the change,
    with field names only. Trust can't open the page. The legal name,
    birth date and home address stay out (the DAC7 page is a later ticket); OIB, IBAN and
    VAT ID show masked. The finance role can reveal them, which is logged. Business
    verification is still set with `verify_business`."""

    list_display = ("id", "user", "declared_status", "business_kind", "business_verified_at")
    list_filter = ("declared_status", "business_kind")
    fields = (
        "user",
        "declared_status",
        "business_kind",
        "business_name",
        *EDITABLE,
        "business_verified_at",
        "business_verified_by",
        "issues_fiscalized_invoices",
        "residence_country",
        "categories",
        "masked_oib",
        "masked_business_oib",
        "masked_iban",
        "masked_vat_id",
        "created_at",
        "updated_at",
    )
    readonly_fields = tuple(name for name in fields if name not in EDITABLE)
    change_form_template = "admin/helpers/helperprofile/change_form.html"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        # Only EDITABLE can change (readonly_fields), and only for roles granted it.
        return is_staff(request.user) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.display(description=TAX_ID_LABELS["oib"])
    def masked_oib(self, obj: HelperProfile) -> str:
        return mask(obj.oib, 4)

    @admin.display(description=TAX_ID_LABELS["business_oib"])
    def masked_business_oib(self, obj: HelperProfile) -> str:
        return mask(obj.business_oib, 4)

    @admin.display(description=TAX_ID_LABELS["iban"])
    def masked_iban(self, obj: HelperProfile) -> str:
        return mask(obj.iban, 4)

    @admin.display(description=TAX_ID_LABELS["vat_id"])
    def masked_vat_id(self, obj: HelperProfile) -> str:
        return mask(obj.vat_id, 4)

    def get_urls(self) -> list[URLPattern]:
        reveal = self.admin_site.admin_view(require_POST(self.reveal_view))
        return [
            path("<int:object_id>/reveal/", reveal, name="helpers_helperprofile_reveal"),
            *super().get_urls(),
        ]

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        extra_context = {
            **(extra_context or {}),
            "can_reveal": can(request.user, Capability.REVEAL_TAX_IDS),
        }
        return super().change_view(request, object_id, form_url, extra_context)

    def reveal_view(self, request: HttpRequest, object_id: int) -> HttpResponse:
        """Shows the full OIB, IBAN and VAT ID once, on a POST so a link or prefetch
        never reveals them, and logs which fields were shown."""
        if not self.has_view_permission(request) or not can(
            request.user, Capability.REVEAL_TAX_IDS
        ):
            raise PermissionDenied
        profile = get_object_or_404(HelperProfile, pk=object_id)
        shown = [name for name in TAX_ID_LABELS if getattr(profile, name)]
        audit.record(request.user, StaffAction.REVEAL_TAX_IDS, profile, fields=shown)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "original": profile,
            "title": _("Tax IDs of helper profile %(id)s") % {"id": profile.pk},
            "values": [(TAX_ID_LABELS[name], getattr(profile, name)) for name in shown],
        }
        return TemplateResponse(request, "admin/helpers/helperprofile/reveal.html", context)

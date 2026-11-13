from typing import Any

from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from legal.models import LegalDocument, LegalDocumentWithdrawal, TermsAcceptance


class _ReadOnly(admin.ModelAdmin):
    """Legal records are only ever written by the legal commands and the API."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(LegalDocument)
class LegalDocumentAdmin(_ReadOnly):
    list_display = ("kind", "version", "language", "effective_at", "material_change", "is_draft")
    list_filter = ("kind", "language", "is_draft")


@admin.register(LegalDocumentWithdrawal)
class LegalDocumentWithdrawalAdmin(_ReadOnly):
    list_display = ("kind", "version", "withdrawn_at")


@admin.register(TermsAcceptance)
class TermsAcceptanceAdmin(_ReadOnly):
    list_display = ("id", "document", "accepted_at", "platform", "sign_in_method")
    list_select_related = ("document",)
    raw_id_fields = ("user",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[TermsAcceptance]:
        return super().get_queryset(request).defer("document__body")

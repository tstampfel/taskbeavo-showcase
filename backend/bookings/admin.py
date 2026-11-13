import logging
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from bookings import arrival, cancel, no_fault, reliability
from bookings.models import Booking, HelperReviewItem
from core.errors import ApiError
from staff import evidence
from staff.evidence import NOTE_MAX_LENGTH
from staff.roles import Capability, can

logger = logging.getLogger(__name__)


class NoteForm(forms.Form):
    body = forms.CharField(
        label=_("Note"), widget=forms.Textarea(attrs={"rows": 3}), max_length=NOTE_MAX_LENGTH
    )
    restricted = forms.BooleanField(
        label=_("Restricted: trust team only (may hold health details)"), required=False
    )


class WithdrawalShareForm(forms.Form):
    """The share of the work already done when the poster withdrew after the start; the
    split is `cancellation.pro_rata_withdrawal_outcome`'s (BEAV-122)."""

    work_done_percent = forms.IntegerField(
        label=_("Work already done (%)"), min_value=0, max_value=99
    )


class NoFaultDecisionForm(forms.Form):
    """Support's decision on a no-fault request that waits for them (BEAV-123): uphold the
    reason (a full refund) or reject it (the ordinary policy for whoever asked)."""

    decision = forms.ChoiceField(
        label=_("Decision"),
        choices=[
            ("uphold", _("Uphold: nobody's fault, full refund")),
            ("reject", _("Reject: the ordinary policy applies to whoever asked")),
        ],
        widget=forms.RadioSelect,
    )


class NoShowDecisionForm(forms.Form):
    """Support's decision on a no-show the helper contested (BEAV-124): uphold it (the
    helper cancellation: full refund, the EUR 5 credit if late, a NO_SHOW fault) or reject
    it (the helper was there: the booking is held again)."""

    decision = forms.ChoiceField(
        label=_("Decision"),
        choices=[
            ("uphold", _("Uphold: the helper didn't come, full refund")),
            ("reject", _("Reject: the helper was there, the booking continues")),
        ],
        widget=forms.RadioSelect,
    )

    def __init__(self, *args: Any, uphold_only: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Uncontested: only uphold (the 24 h already decided it, rule g).
        if uphold_only:
            field = self.fields["decision"]
            field.choices = [c for c in field.choices if c[0] == "uphold"]  # type: ignore[attr-defined]


class CancellationQueueFilter(admin.SimpleListFilter):
    """Support's "Cancellations & withdrawals" queue (BEAV-122)."""

    title = _("Cancellations & withdrawals")
    parameter_name = "cancellation"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, Any]]:
        return [
            ("share", _("Withdrawal waits for the share of the work done")),
            ("no_fault", _("No-fault request waits for a decision")),
            ("no_show", _("No-show waits for a decision (contested or not settled)")),
            ("settling", _("Refund not confirmed yet")),
            ("refused", _("Refused by Stripe")),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Booking]) -> QuerySet[Booking]:
        if self.value() == "share":
            return queryset.filter(
                status=Booking.Status.CANCEL_REQUESTED,
                cancel_reason=cancel.WITHDRAWAL_REASON,
                settlement__isnull=True,
            )
        if self.value() == "no_fault":
            return queryset.filter(
                status=Booking.Status.CANCEL_REQUESTED,
                no_fault_requests__closed_at__isnull=True,
                no_fault_requests__support_needed_at__isnull=False,
            ).distinct()
        if self.value() == "no_show":
            return queryset.filter(
                status=Booking.Status.CANCEL_REQUESTED,
                cancel_reason=arrival.NO_SHOW_REASON,
                arrival__no_show_decision="",
                arrival__no_show_settled_at__isnull=True,
            ).filter(
                Q(arrival__no_show_contested_at__isnull=False)
                | Q(arrival__no_show_settle_attempts__gt=0)
            )
        if self.value() == "settling":
            # settlement__isnull=False: the reverse one-to-one is a LEFT JOIN, so without it
            # every booking with no settlement at all would match (review R1-3).
            return queryset.filter(
                settlement__isnull=False,
                settlement__confirmed_at__isnull=True,
                settlement__refused_at__isnull=True,
            )
        if self.value() == "refused":
            return queryset.filter(settlement__isnull=False, settlement__refused_at__isnull=False)
        return queryset


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    """Bookings for staff: a list of IDs, statuses and amounts, and per booking the
    read-only evidence view (`staff.evidence`), cut down to the staff member's role.
    Opening it is logged. The one money action is the share of the work done for a
    withdrawal after the start (support and finance, BEAV-122); release and other refunds
    are BEAV-157."""

    # `poster_was_business`: the poster booked for a business at acceptance (BEAV-195).
    list_display = (
        "id",
        "status",
        "amount_cents",
        "fee_cents",
        "poster_was_business",
        "slot_start",
        "created_at",
    )
    list_filter = ("status", "poster_was_business", CancellationQueueFilter)
    date_hierarchy = "created_at"
    show_full_result_count = False

    def get_queryset(self, request: HttpRequest) -> QuerySet[Booking]:
        return super().get_queryset(request).only(*self.list_display)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def get_urls(self) -> list[URLPattern]:
        add_note = self.admin_site.admin_view(require_POST(self.add_note_view))
        share = self.admin_site.admin_view(require_POST(self.withdrawal_share_view))
        decide = self.admin_site.admin_view(require_POST(self.no_fault_decision_view))
        no_show = self.admin_site.admin_view(require_POST(self.no_show_decision_view))
        return [
            path(
                "<uuid:object_id>/no-show-decision/",
                no_show,
                name="bookings_booking_no_show_decision",
            ),
            path(
                "<uuid:object_id>/no-fault-decision/",
                decide,
                name="bookings_booking_no_fault_decision",
            ),
            path("<uuid:object_id>/notes/", add_note, name="bookings_booking_add_note"),
            path(
                "<uuid:object_id>/withdrawal-share/",
                share,
                name="bookings_booking_withdrawal_share",
            ),
            *super().get_urls(),
        ]

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        booking = self._booking(request, object_id)
        return self._render(request, booking, NoteForm())

    def add_note_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        booking = self._booking(request, object_id)
        form = NoteForm(request.POST)
        if not form.is_valid():
            return self._render(request, booking, form)
        evidence.add_note(
            request.user,
            booking,
            form.cleaned_data["body"],
            # A non-trust POST with restricted set is refused, never stored unrestricted.
            restricted=form.cleaned_data["restricted"],
        )
        self.message_user(request, _("Note added."), messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:bookings_booking_change", args=[booking.pk]))

    def withdrawal_share_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        booking = self._booking(request, object_id)
        if not can(request.user, Capability.STRIPE_IDS):
            raise PermissionDenied
        form = WithdrawalShareForm(request.POST)
        if not form.is_valid():
            return self._render(request, booking, NoteForm(), share_form=form)
        try:
            cancel.settle_withdrawal(
                request.user, booking.pk, form.cleaned_data["work_done_percent"]
            )
        except ApiError:
            self.message_user(
                request, _("No withdrawal waits for a share on this booking."), messages.ERROR
            )
        else:
            self.message_user(
                request, _("Share saved. The settlement is being sent to Stripe."), messages.SUCCESS
            )
        return HttpResponseRedirect(reverse("admin:bookings_booking_change", args=[booking.pk]))

    def no_fault_decision_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        booking = self._booking(request, object_id)
        if not can(request.user, Capability.STRIPE_IDS):
            raise PermissionDenied
        form = NoFaultDecisionForm(request.POST)
        if not form.is_valid():
            return self._render(request, booking, NoteForm(), no_fault_form=form)
        try:
            no_fault.decide_no_fault(
                request.user, booking.pk, uphold=form.cleaned_data["decision"] == "uphold"
            )
        except ApiError:
            self.message_user(
                request, _("No no-fault request waits for a decision here."), messages.ERROR
            )
        else:
            self.message_user(request, _("Decision saved."), messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:bookings_booking_change", args=[booking.pk]))

    def no_show_decision_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        booking = self._booking(request, object_id)
        if not can(request.user, Capability.STRIPE_IDS):
            raise PermissionDenied
        form = NoShowDecisionForm(request.POST)
        if not form.is_valid():
            return self._render(request, booking, NoteForm(), no_show_form=form)
        try:
            arrival.decide_no_show(
                request.user, booking.pk, uphold=form.cleaned_data["decision"] == "uphold"
            )
        except ApiError:
            self.message_user(request, _("No no-show waits for a decision here."), messages.ERROR)
        except Exception:
            # The same failure the sweep hit (payments review MINOR 2): the money stays held.
            logger.exception(
                "bookings: settling a no-show from the admin failed",
                extra={"booking.id": str(booking.pk)},
            )
            self.message_user(
                request,
                _("Settling failed again. The money stays held; ask engineering."),
                messages.ERROR,
            )
        else:
            self.message_user(request, _("Decision saved."), messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:bookings_booking_change", args=[booking.pk]))

    def _booking(self, request: HttpRequest, object_id: str) -> Booking:
        if not self.has_view_permission(request):
            raise PermissionDenied
        return get_object_or_404(
            Booking.objects.select_related(
                "task__service_area", "care_contact", "helper__helper_profile"
            ),
            pk=object_id,
        )

    def _render(
        self,
        request: HttpRequest,
        booking: Booking,
        form: NoteForm,
        share_form: WithdrawalShareForm | None = None,
        no_fault_form: NoFaultDecisionForm | None = None,
        no_show_form: NoShowDecisionForm | None = None,
    ) -> HttpResponse:
        seen = evidence.booking_evidence(request.user, booking)
        no_show_waiting = arrival.awaiting_support(booking)
        no_fault_request = no_fault.open_request(booking)
        if not seen.can_write_restricted:
            del form.fields["restricted"]
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": _("Booking %(id)s") % {"id": booking.pk},
            "evidence": seen,
            # Finance works from IDs: no user admin (staff.roles).
            "can_view_users": request.user.has_perm("accounts.view_user"),
            "note_form": form,
            "share_form": (
                (share_form or WithdrawalShareForm())
                if cancel.awaiting_share(booking) and can(request.user, Capability.STRIPE_IDS)
                else None
            ),
            "no_fault_request": no_fault_request,
            "no_fault_reason": (
                evidence.no_fault_reason_for(request.user, no_fault_request.reason)
                if no_fault_request
                else ""
            ),
            "no_fault_form": (
                (no_fault_form or NoFaultDecisionForm())
                if no_fault.awaiting_support(booking) and can(request.user, Capability.STRIPE_IDS)
                else None
            ),
            "no_show_waiting": no_show_waiting,
            "no_show_settle_failed": no_show_waiting
            and arrival.settle_failed(getattr(booking, "arrival", None)),
            "no_show_form": (
                (
                    no_show_form
                    or NoShowDecisionForm(
                        uphold_only=no_show_waiting
                        and arrival.settle_failed(getattr(booking, "arrival", None))
                    )
                )
                if no_show_waiting and can(request.user, Capability.STRIPE_IDS)
                else None
            ),
        }
        return TemplateResponse(request, "admin/bookings/booking/evidence.html", context)


@admin.register(HelperReviewItem)
class HelperReviewItemAdmin(admin.ModelAdmin):
    """Helpers with two helper-fault cancellations within 30 days (BEAV-123). A person
    looks at the helper and decides; nothing here blocks an account or charges anyone.
    IDs, counts and times only."""

    list_display = (
        "id",
        "helper_id",
        "reason",
        "fault_count",
        "score",
        "created_at",
        "reviewed_at",
    )
    list_filter = ("reason", ("reviewed_at", admin.EmptyFieldListFilter))
    fields = readonly_fields = (
        "helper_id",
        "reason",
        "fault_bookings",
        "created_at",
        "reviewed_at",
        "reviewed_by_id",
    )
    actions = ("mark_reviewed",)
    show_full_result_count = False

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    @admin.display(description=_("Faults"))
    def fault_count(self, obj: HelperReviewItem) -> int:
        return obj.faults.count()

    @admin.display(description=_("Bookings"))
    def fault_bookings(self, obj: HelperReviewItem) -> str:
        return ", ".join(str(pk) for pk in obj.faults.values_list("booking_id", flat=True))

    @admin.display(description=_("Reliability (%)"))
    def score(self, obj: HelperReviewItem) -> int | None:
        return reliability.reliability_score(obj.helper, timezone.now())

    @admin.action(description=_("Mark reviewed"), permissions=["change"])
    def mark_reviewed(self, request: HttpRequest, queryset: QuerySet[HelperReviewItem]) -> None:
        now = timezone.now()
        for item in queryset:
            reliability.mark_reviewed(item, request.user, now)  # pyright: ignore[reportArgumentType]
        self.message_user(request, _("Marked reviewed."), messages.SUCCESS)

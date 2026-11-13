"""Legal document versions and acceptances (plan: "Terms and conditions: pages, acceptance
and updates").

A `LegalDocument` row is one language of one version of one document. Rows are immutable:
a change is a new version, so TaskBeavo can always show exactly what someone accepted.
The model refuses updates and deletes, and so does a database trigger (migration 0001).
Versions are published only through `manage.py publish_legal_document` (legal/services.py).

A scheduled version that turns out wrong is withdrawn before it takes effect, by adding a
`LegalDocumentWithdrawal` (`manage.py withdraw_legal_document`); the version itself stays,
unchanged, and is skipped from then on.

A `TermsAcceptance` row is the proof that a user accepted a version. It is append-only;
the one change allowed is detaching it from a deleted account (see the class docstring).
"""

from datetime import datetime
from typing import Any, ClassVar, NoReturn

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import ProviderIdentity, Session
from core.i18n import SUPPORTED_LOCALES


class ImmutableRowError(Exception):
    """A legal record was changed or deleted in place."""


class LegalDocumentQuerySet(models.QuerySet["LegalDocument"]):
    def update(self, **kwargs: Any) -> NoReturn:
        raise ImmutableRowError("Legal documents are immutable; publish a new version.")

    def delete(self) -> NoReturn:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise ImmutableRowError("Legal documents are immutable; publish a new version.")


class LegalDocument(models.Model):
    """One language of one version of a legal document.

    `effective_at` is when the version starts to apply; until then the previous version
    is current. A `material_change` (fees, cancellations, liability) needs 30 days'
    notice and re-acceptance; a small fix doesn't. `is_draft` marks placeholder text that
    no lawyer has approved: production refuses to start while one is current or
    scheduled (`check_legal_documents`). `body_sha256` fingerprints the text as published.
    """

    class Kind(models.TextChoices):
        TERMS = "TERMS"
        HELPER_TERMS = "HELPER_TERMS"
        PRIVACY = "PRIVACY"
        CANCELLATION = "CANCELLATION"
        COOKIES = "COOKIES"
        IMPRINT = "IMPRINT"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    # Chosen by whoever publishes, e.g. "2027-03"; the same for every language.
    version = models.CharField(max_length=32)
    language = models.CharField(max_length=2, choices=[(code, code) for code in SUPPORTED_LOCALES])
    body = models.TextField()
    body_sha256 = models.CharField(max_length=64)
    effective_at = models.DateTimeField()
    material_change = models.BooleanField()
    is_draft = models.BooleanField()
    published_at = models.DateTimeField(default=timezone.now)

    objects: ClassVar[models.Manager["LegalDocument"]] = LegalDocumentQuerySet.as_manager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["kind", "version", "language"], name="unique_legal_document_version"
            ),
        ]
        indexes: ClassVar = [models.Index(fields=["kind", "language", "effective_at"])]

    def __str__(self) -> str:
        return f"{self.kind} {self.version} ({self.language})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRowError("Legal documents are immutable; publish a new version.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ImmutableRowError("Legal documents are immutable; publish a new version.")


class LegalDocumentWithdrawalQuerySet(models.QuerySet["LegalDocumentWithdrawal"]):
    def update(self, **kwargs: Any) -> NoReturn:
        raise ImmutableRowError("Withdrawals are immutable.")

    def delete(self) -> NoReturn:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise ImmutableRowError("Withdrawals are immutable.")


class LegalDocumentWithdrawal(models.Model):
    """`kind` `version` was withdrawn at `withdrawn_at`, before it took effect.

    Every language of the version stays as published, so the record shows what was
    scheduled and when it was pulled; current-version lookups, the draft guard and the
    publishing rules skip it. Immutable like the documents (model and trigger).
    """

    kind = models.CharField(max_length=20, choices=LegalDocument.Kind.choices)
    version = models.CharField(max_length=32)
    withdrawn_at = models.DateTimeField(default=timezone.now)
    # Why, in the operator's words; not shown to users.
    reason = models.TextField()

    objects: ClassVar[models.Manager["LegalDocumentWithdrawal"]] = (
        LegalDocumentWithdrawalQuerySet.as_manager()  # pyright: ignore[reportIncompatibleVariableOverride]
    )

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["kind", "version"], name="unique_legal_withdrawal")
        ]

    def __str__(self) -> str:
        return f"Withdrawal of {self.kind} {self.version}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRowError("Withdrawals are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ImmutableRowError("Withdrawals are immutable.")


class TermsAcceptanceQuerySet(models.QuerySet["TermsAcceptance"]):
    def update(self, **kwargs: Any) -> int:
        # Detaching from a deleted account is the one change allowed.
        if kwargs != {"user": None}:
            raise ImmutableRowError("Terms acceptances are append-only.")
        return super().update(**kwargs)

    def delete(self) -> NoReturn:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise ImmutableRowError(
            "Terms acceptances are append-only; only purge_detached removes them."
        )

    def bulk_create(self, objs: Any, *args: Any, **kwargs: Any) -> list["TermsAcceptance"]:
        objs = list(objs)  # a generator would be used up by the loop below
        for obj in objs:
            if obj.user_id is not None:
                obj.user_reference = obj.user_id
        return super().bulk_create(objs, *args, **kwargs)

    def purge_detached(self, *, accepted_before: datetime) -> int:
        """Delete detached acceptances older than `accepted_before`: the one way rows leave
        at the end of their retention period. The caller (BEAV-153) picks the cutoff."""
        expired = self.filter(user__isnull=True, accepted_at__lt=accepted_before)
        return expired._raw_delete(expired.db)  # pyright: ignore[reportAttributeAccessIssue]


class TermsAcceptance(models.Model):
    """A user accepted `document` (its kind, version and language) at `accepted_at`.

    `platform` is where they accepted (web, iOS or Android). `sign_in_method` is set for
    the sign-up acceptance only: email code, Google or Apple. A checkout acceptance of
    the cancellation policy names its `booking` (bookings.checkout).

    Rows are legal evidence, so they are append-only: the model refuses to change or
    delete them, and a database trigger refuses any update other than detaching the row
    from its user. Retention (hard rule h): they are kept for 11 years, like the
    accounting and fiscal records. TODO(BEAV-153): account deletion moves them to the
    restricted archive, detached from the live account: `user` becomes NULL (SET_NULL)
    and `user_reference`, the account's numeric id, is the pseudonymous link the archive
    uses. Document, version, language, time and channel are kept; no name or email.
    The database still allows DELETE, for moving rows to the archive and for
    `purge_detached` at the end of retention; the ORM refuses every other delete.
    """

    class SignInMethod(models.TextChoices):
        EMAIL = "email"
        GOOGLE = ProviderIdentity.Provider.GOOGLE.value
        APPLE = ProviderIdentity.Provider.APPLE.value

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="terms_acceptances",
    )
    # The account's id, kept after the account is deleted.
    user_reference = models.BigIntegerField()
    document = models.ForeignKey(LegalDocument, on_delete=models.PROTECT, related_name="+")
    accepted_at = models.DateTimeField(default=timezone.now)
    platform = models.CharField(max_length=10, choices=Session.Platform.choices)
    sign_in_method = models.CharField(max_length=10, choices=SignInMethod.choices, blank=True)
    # The booking whose checkout accepted the cancellation policy; null for other kinds.
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="terms_acceptances",
    )

    objects: ClassVar[models.Manager["TermsAcceptance"]] = TermsAcceptanceQuerySet.as_manager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        indexes: ClassVar = [models.Index(fields=["user", "accepted_at"])]

    def __str__(self) -> str:
        return f"TermsAcceptance {self.pk}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRowError("Terms acceptances are append-only.")
        if self.user_id is not None:
            self.user_reference = self.user_id
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ImmutableRowError("Terms acceptances are append-only.")


class ConsentQuerySet(models.QuerySet["Consent"]):
    def update(self, **kwargs: Any) -> int:
        # Detaching from a deleted account is the one change allowed.
        if kwargs != {"user": None}:
            raise ImmutableRowError("Consents are append-only; record a new choice instead.")
        return super().update(**kwargs)


class Consent(models.Model):
    """One choice about an optional purpose: analytics, or news and offers by email or push
    (plan, GDPR section "Correction, objection and consent"; BEAV-83).

    Every change is a new row, so the latest row per purpose is the current choice and the
    history proves what was agreed and when. `document` is the text the choice referred to:
    the cookie policy for analytics, the privacy notice for marketing, in the version in
    force and the language it was given in (`version` repeats it so the proof reads without
    a join). It is empty only for a refusal while no such document was in force.

    A signed-in choice names its `user`; the web cookie banner before sign-in names the
    browser's random `visitor_id` instead (no IP address, no other identifier). Rows are
    append-only, refused by the model and the queryset, because they are the proof of
    consent. Deleting the account keeps them as that proof, detached like
    `TermsAcceptance`: `user` becomes NULL (SET_NULL) and `user_reference`, the account's
    numeric id, is the pseudonymous link the archive uses; no name or email is kept.
    TODO(BEAV-153): include `legal.consents.export_consents` in "Download my data", move
    detached rows to the restricted archive and purge them at the end of retention.
    """

    class Purpose(models.TextChoices):
        ANALYTICS = "ANALYTICS"
        MARKETING_EMAIL = "MARKETING_EMAIL"
        MARKETING_PUSH = "MARKETING_PUSH"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consents",
    )
    # The account's id, kept after the account is deleted; null for a visitor's choice.
    user_reference = models.BigIntegerField(null=True, blank=True)
    visitor_id = models.UUIDField(null=True, blank=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    granted = models.BooleanField()
    document = models.ForeignKey(
        LegalDocument, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    version = models.CharField(max_length=32, blank=True)
    language = models.CharField(max_length=2, choices=[(code, code) for code in SUPPORTED_LOCALES])
    platform = models.CharField(max_length=10, choices=Session.Platform.choices)
    given_at = models.DateTimeField(default=timezone.now)

    objects: ClassVar[models.Manager["Consent"]] = ConsentQuerySet.as_manager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=models.Q(user_reference__isnull=False)
                | models.Q(visitor_id__isnull=False),
                name="consent_has_user_or_visitor",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["user", "purpose", "given_at"]),
            models.Index(fields=["visitor_id", "purpose", "given_at"]),
        ]

    def __str__(self) -> str:
        return f"Consent {self.pk}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ImmutableRowError("Consents are append-only; record a new choice instead.")
        if self.user_id is not None:
            self.user_reference = self.user_id
        super().save(*args, **kwargs)

"""Accounts, email sign-in codes and sessions (plan: "Sign-up and login").

There are no passwords: people sign in with an emailed code, Google or Apple. A `User`
row exists only once the account-details step is done; until then the verified email
(and, for Google and Apple, the provider account) lives in a signed sign-up ticket
(accounts/signin.py).
"""

import uuid
from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.crypto import EncryptedTextField
from core.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES
from core.models import AuditEvent


def normalize_email(email: str) -> str:
    """The one spelling an address is stored and looked up under: trimmed, lower case."""
    return email.strip().lower()


class UserManager(BaseUserManager["User"]):
    def create_user(self, email: str, password: str | None = None, **fields: object) -> "User":
        user = self.model(email=normalize_email(email), **fields)
        # Members sign in by email code; only staff get a password, for the admin.
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **fields: object) -> "User":
        fields.setdefault("is_staff", True)
        fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **fields)


class User(AbstractBaseUser, PermissionsMixin):
    """A TaskBeavo account. One account posts tasks and, after onboarding, helps.

    `locale` is the language emails, push and SMS go out in (`core.i18n.LocaleRecipient`).

    The mobile number is verified by SMS code (accounts/phone.py) before the account can
    post, offer or chat (`accounts.permissions`). It is encrypted at rest and shown only
    to its owner, masked (hard rule e).

    `status` holds the account states staff or the deletion flow set: ACTIVE, SUSPENDED
    or DELETION_PENDING. The fourth state, TERMS_UPDATE_REQUIRED, is never stored: it is
    worked out from the TERMS version in force and the user's acceptances
    (`accounts.states`), so it can't drift from them.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        # A person restricted the account (trust-safety): it can still sign in, see the
        # reason, appeal, finish open bookings, export its data and delete itself.
        SUSPENDED = "SUSPENDED"
        # Deletion was asked for while bookings or payouts are still open; it runs once
        # they close (gdpr-data-request).
        DELETION_PENDING = "DELETION_PENDING"

    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    locale = models.CharField(
        max_length=2,
        choices=[(code, code) for code in SUPPORTED_LOCALES],
        default=DEFAULT_LOCALE,
    )
    # ISO 3166-1 alpha-2 country the person lives in; posters can live anywhere.
    country = models.CharField(max_length=2)
    # IANA time zone, e.g. "Europe/Berlin", for showing times in the poster's own clock.
    time_zone = models.CharField(max_length=64)
    # When the person confirmed they are 18 or older (plan: 18+ only).
    adult_confirmed_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    # The verified mobile number in E.164, encrypted; "" until one is verified.
    phone_number = EncryptedTextField(blank=True, default="")
    # `core.crypto.pii_digest("phone", phone_number)`: one verified number, one account.
    phone_digest = models.CharField(max_length=64, null=True, blank=True, unique=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    objects: ClassVar[UserManager] = UserManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []  # pyright: ignore[reportIncompatibleVariableOverride]

    def __str__(self) -> str:
        return f"User {self.pk}"


class EmailCode(models.Model):
    """A 6-digit sign-in code: stored only as a keyed hash, valid 10 minutes, used once.

    Requesting a new code deletes the address's earlier unused ones. Rows are purged a day
    after they expire (accounts/tasks.py), so addresses that never became accounts go too.
    """

    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    # Wrong guesses against this code; at the maximum the code is burnt.
    failed_attempts = models.PositiveSmallIntegerField(default=0)

    def __str__(self) -> str:
        return f"EmailCode {self.pk}"


class PhoneCode(models.Model):
    """A 6-digit SMS code proving `user` holds `phone_number`: a keyed hash, used once.

    Asking for a new code deletes the account's earlier unused ones. Rows are purged a day
    after they expire (accounts/tasks.py), so numbers that were never verified go too.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="phone_codes")
    # The number the code was sent to, in E.164, encrypted.
    phone_number = EncryptedTextField()
    phone_digest = models.CharField(max_length=64)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    # Wrong guesses against this code; at the maximum the code is burnt.
    failed_attempts = models.PositiveSmallIntegerField(default=0)

    def __str__(self) -> str:
        return f"PhoneCode {self.pk}"


class SignInNonce(models.Model):
    """A one-time nonce for a Google or Apple sign-in, issued by `requestSignInNonce`.

    The client hands it to the provider, which signs it into the ID token, so a stolen
    token can't be replayed. Stored as a SHA-256 hash; used once, then purged a day after
    expiry (accounts/tasks.py).
    """

    nonce_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"SignInNonce {self.pk}"


class ProviderIdentity(models.Model):
    """A Google or Apple account that signs in to `user`, keyed by the provider's `sub`.

    Created with the account in the details step, or when a verified provider email
    matches an existing account. Apple may hide the person's address behind a private
    relay (`...@privaterelay.appleid.com`); that relay address is then the account's email.
    """

    class Provider(models.TextChoices):
        GOOGLE = "google"
        APPLE = "apple"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="provider_identities")
    provider = models.CharField(max_length=10, choices=Provider.choices)
    # The provider's stable account ID (`sub`); the email can change, this can't.
    subject = models.CharField(max_length=255)
    # Apple's "Hide My Email": the account's email is a relay address.
    is_private_relay_email = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["provider", "subject"], name="unique_provider_identity")
        ]

    def __str__(self) -> str:
        return f"ProviderIdentity {self.pk}"


class PendingProviderName(models.Model):
    """The name Apple handed the app on a first sign-in whose account isn't created yet.

    Apple sends the name only once, so it waits here until the details step creates the
    account (then it is deleted), and is purged a day after it expires (accounts/tasks.py).
    """

    provider = models.CharField(max_length=10, choices=ProviderIdentity.Provider.choices)
    subject = models.CharField(max_length=255)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["provider", "subject"], name="unique_pending_provider_name"
            )
        ]

    def __str__(self) -> str:
        return f"PendingProviderName {self.pk}"


class Session(models.Model):
    """One signed-in device. Its refresh tokens form a family that rotates on every refresh.

    Revoking the session (reuse of a rotated refresh token, sign-out, log out of all
    devices) ends every access token and WebSocket token issued for it, since those name
    the session in `sid`, and closes its open WebSockets.
    """

    class Platform(models.TextChoices):
        WEB = "web"
        IOS = "ios"
        ANDROID = "android"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    platform = models.CharField(max_length=10, choices=Platform.choices)
    device_name = models.CharField(max_length=100, blank=True)
    # SHA-256 of the device token the client kept from an earlier sign-in on this device
    # (accounts/sessions.py): how a sign-in is recognised as a known device. "" if none.
    device_hash = models.CharField(max_length=64, blank=True, db_index=True)
    # Where it signed in, coarsely ("Munich, Germany"; accounts/geo.py), for the new-device
    # email; "" when unknown. The client address itself is never stored.
    location = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Session {self.pk}"


class RefreshToken(models.Model):
    """One refresh token of a session, stored as a SHA-256 hash.

    A token is used once: refreshing marks it used and issues the next one. Presenting a
    used token again is reuse, which revokes the whole session (see accounts/sessions.py).
    """

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="refresh_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"RefreshToken {self.pk}"


class PosterBusiness(models.Model):
    """The obrt or company a poster books for (decision A4, guardrail 3; the decision
    record is kept outside the public repository). Once a poster gives one, they are a
    business poster: a business that pays a private helper would itself be the payer
    with a withholding duty, so their tasks take offers from registered helpers only
    (`accounts.business`).

    Personal data (hard rules e and h): an obrt's OIB is its owner's personal OIB, so
    `oib` is encrypted at rest, shown to its owner only masked, never to other users, and
    never logged or sent to Sentry or Honeycomb. Deleting the user deletes it (CASCADE).
    """

    class Kind(models.TextChoices):
        OBRT = "OBRT", "Obrt (sole trader)"
        COMPANY = "COMPANY", "Company"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="poster_business")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=200)
    oib = EncryptedTextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "poster_businesses"

    def __str__(self) -> str:
        # No name or OIB: __str__ ends up in logs and admin history.
        return f"PosterBusiness {self.pk}"


class PosterBusinessEvent(AuditEvent):
    """Audit trail of a poster's business: every time it is set, changed or removed. Removal
    stays possible (correcting one's data), but removing and re-adding it around a booking
    of a private helper shows here. `data` holds the kind and counts only, never the name
    or OIB."""

    class Type(models.TextChoices):
        SET = "set"
        CHANGED = "changed"
        CLEARED = "cleared"

    poster = models.ForeignKey(User, on_delete=models.CASCADE, related_name="business_events")

    class Meta(AuditEvent.Meta):
        db_table = "poster_business_events"

"""The helper profile (plan: HELPER_PROFILE, "Becoming a helper"): status declaration,
identity and tax data for DAC7, service areas and task categories.

A user becomes a helper by filling it in; until it is complete (`helpers.services`) they
can browse but not send offers.
"""

from typing import ClassVar

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from areas.models import ServiceArea
from core.categories import TaskCategory
from core.crypto import EncryptedTextField


class BusinessVerification(models.TextChoices):
    """How a company's name and OIB were confirmed."""

    STAFF = "STAFF", "Checked by staff"
    # TODO: the court register (sudreg-data.gov.hr) check, a later ticket.
    COURT_REGISTER = "COURT_REGISTER", "Matched in the court register"


class HelperProfile(models.Model):
    """One user's helper data. Personal data throughout (hard rules e and h):

    - `oib`, `business_oib`, `iban` and `vat_id` are encrypted at rest. `oib_digest` (a keyed
      `pii_digest`) makes one OIB belong to one helper.
    - Only the owner reads the identity, address, OIB and IBAN, and the OIB and IBAN only
      masked. Other users see a trader's business name and business OIB (a trader must
      show them), its categories and service areas, nothing else. A trader's registered
      business address reaches only the poster of a booking, in the contract confirmation.
    - None of it is logged or sent to Sentry or Honeycomb.
    - Deletion: the identity, address, OIB, IBAN and earnings are DAC7 records, kept
      restricted for the DAC7 retention period, so the user row can't be deleted under the
      profile (PROTECT). TODO(BEAV-153): the account deletion flow moves the DAC7 fields
      to the restricted archive (`helpers.gdpr`) and then deletes this row.
    """

    class Status(models.TextChoices):
        PRIVATE_INDIVIDUAL = "PRIVATE_INDIVIDUAL", "Private individual"
        BUSINESS = "BUSINESS", "Registered business (obrt or company)"
        PENSIONER = "PENSIONER", "Pensioner"
        STUDENT = "STUDENT", "Student"

    class BusinessKind(models.TextChoices):
        OBRT = "OBRT", "Obrt (sole trader)"
        COMPANY = "COMPANY", "Company"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="helper_profile"
    )
    declared_status = models.CharField(max_length=20, choices=Status.choices, blank=True)
    # Only for BUSINESS; cleared when the status changes to another one.
    business_kind = models.CharField(max_length=10, choices=BusinessKind.choices, blank=True)
    business_name = models.CharField(max_length=200, blank=True)
    # An obrt has no OIB of its own: it is the owner's OIB, so for OBRT it must equal
    # `oib`. A company's OIB can't be checked yet. NEEDS REVIEW: verify it against the
    # court register (sudski registar) before trusting it further.
    business_oib = EncryptedTextField(blank=True)
    # `pii_digest("oib", ...)` of a company's OIB, so a person's OIB can't be both a
    # helper's personal OIB and someone's company OIB. Empty for an obrt.
    business_oib_digest = models.CharField(max_length=64, blank=True, db_index=True)
    # A company's name and OIB are checked before they count or are shown publicly
    # (`helpers.services.verify_business`); an obrt needs no check, its OIB is the
    # owner's. Changing the company's name or OIB clears it.
    business_verified_at = models.DateTimeField(null=True, blank=True)
    business_verified_by = models.CharField(
        max_length=20, choices=BusinessVerification.choices, blank=True
    )
    # The trader's own statement that they issue a fiscalized invoice for each task.
    issues_fiscalized_invoices = models.BooleanField(default=False)

    legal_first_name = models.CharField(max_length=100, blank=True)
    legal_last_name = models.CharField(max_length=100, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    # The registered business address of an obrt or company (CRD Art. 6(1)(c)): a trader
    # must give it. The poster of a task sees it on the trader's offer, at checkout and in
    # the contract confirmation email; the public profile shows only the town, since many
    # obrts are registered at the owner's home (user decisions 2026-11-04 and 2026-11-07,
    # BEAV-192). Never the home address
    # below, which a trader may share with the business but which is never used in its
    # place. The helper sets it with updateHelperProfile and staff can edit it in the admin;
    # the onboarding screen that asks for it is BEAV-97.
    business_address_street = models.CharField(max_length=200, blank=True)
    business_address_postal_code = models.CharField(max_length=10, blank=True)
    business_address_city = models.CharField(max_length=100, blank=True)
    business_address_country = models.CharField(max_length=2, blank=True)

    # Home address. Helpers need one in Croatia (hard rule j), so `address_country` is HR.
    address_street = models.CharField(max_length=200, blank=True)
    address_postal_code = models.CharField(max_length=10, blank=True)
    address_city = models.CharField(max_length=100, blank=True)
    address_country = models.CharField(max_length=2, blank=True)
    oib = EncryptedTextField(blank=True)
    oib_digest = models.CharField(max_length=64, null=True, blank=True, unique=True)
    # Electronic form, e.g. HR0600000000000000160. Reported under DAC7; payouts use the
    # account the helper gives Stripe (BEAV-95).
    iban = EncryptedTextField(blank=True)
    # Encrypted too: a Croatian VAT ID is HR followed by the OIB.
    vat_id = EncryptedTextField(blank=True)
    # DAC7 member state of residence, ISO 3166-1 alpha-2.
    residence_country = models.CharField(max_length=2, blank=True)

    # Stable keys of `core.categories.TaskCategory`.
    categories = ArrayField(
        models.CharField(max_length=20, choices=TaskCategory.choices), default=list, blank=True
    )
    service_areas = models.ManyToManyField(ServiceArea, blank=True, related_name="helpers")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "helper_profiles"
        ordering: ClassVar = ["created_at", "id"]

    def __str__(self) -> str:
        # No names or OIB: __str__ ends up in logs and admin history.
        return f"HelperProfile {self.pk}"

    @property
    def needs_business_verification(self) -> bool:
        """A company whose name and OIB haven't been confirmed yet."""
        return (
            self.is_trader
            and self.business_kind == self.BusinessKind.COMPANY
            and self.business_verified_at is None
        )

    @property
    def business_address(self) -> str:
        """The registered business address on one line, or "" while any part is missing."""
        parts = (
            self.business_address_street,
            self.business_address_postal_code,
            self.business_address_city,
            self.business_address_country,
        )
        if not all(parts):
            return ""
        street, postal_code, city, country = parts
        return f"{street}, {postal_code} {city}, {country.upper()}"

    @property
    def is_trader(self) -> bool:
        """A registered business (obrt or company): labelled "Trader", with trader rules."""
        return self.declared_status == self.Status.BUSINESS

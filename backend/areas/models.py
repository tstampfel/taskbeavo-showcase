"""Where TaskBeavo works (hard rule j) and who is waiting for it to open elsewhere.

A `ServiceArea` is a polygon of a Croatian city TaskBeavo is open in, edited on a map in
the admin; only active Croatian areas accept tasks (areas/services.py). A
`WaitlistEntry` records someone who tried a place outside them and agreed to one email
when TaskBeavo launches there.
"""

import uuid
from typing import ClassVar

from django.contrib.gis.db import models

from core.categories import TaskCategory

# What a waitlist consent covers: exactly one "we've launched in <city>" email for the
# entry's city, and nothing else (no newsletter, no other marketing). The consent text
# the client shows next to the checkbox must say exactly that; bump the version when the
# wording changes, so each entry proves which text was agreed to.
WAITLIST_CONSENT_VERSION = "waitlist-launch-email-v1"


class ServiceArea(models.Model):
    """A city polygon. Tasks are allowed only inside an active area in Croatia.

    `country_code` is limited to HR in the admin; areas in other countries are never
    used by the check, whatever their `active` flag, until TaskBeavo expands (docs/PLAN.md).
    """

    class Country(models.TextChoices):
        HR = "HR", "Croatia"

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    country_code = models.CharField(max_length=2, choices=Country.choices, default=Country.HR)
    active = models.BooleanField(default=False)
    polygon = models.MultiPolygonField(srid=4326)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class WaitlistEntry(models.Model):
    """Someone waiting for TaskBeavo in a city (or country) it doesn't serve yet.

    Personal data: `email`, stored as accounts store it (trimmed, lower case). GDPR
    deletion and data export (BEAV-153) must cover these rows, matched by email as they
    aren't linked to an account. The email is used only for the single launch email the
    consent covers, and never goes to Sentry or Honeycomb.

    Consent proof: `consent_version`, `consent_given_at` and `consent_locale` prove the
    latest join. Joining again for the same email and city replaces them (and anyone can
    join with any address, so they prove the box was ticked, not who ticked it); BEAV-83
    moves consent history into the CONSENT log.

    Withdrawal: the launch email links to `leaveWaitlist(token)` with a token from
    `areas.services.issue_leave_token`; only its SHA-256 is stored, in `leave_token_digest`.

    Retention: the Beat task `areas.purge_waitlist` deletes an entry once its launch email
    is sent, and in any case WAITLIST_RETENTION_DAYS (730, within 24 months) after
    `created_at`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    # The spelling the person typed most recently, for the launch email.
    city = models.CharField(max_length=100)
    # The city casefolded with whitespace collapsed: what entries and demand are keyed by.
    city_key = models.CharField(max_length=100)
    country_code = models.CharField(max_length=2)
    category = models.CharField(max_length=20, choices=TaskCategory.choices)
    consent_launch_email = models.BooleanField()
    consent_version = models.CharField(max_length=50)
    consent_given_at = models.DateTimeField()
    # The language the consent text was shown in, and the launch email goes out in.
    consent_locale = models.CharField(max_length=2)
    leave_token_digest = models.CharField(max_length=64, unique=True, null=True, blank=True)
    launch_email_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "waitlist entries"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["email", "city_key", "country_code"], name="waitlist_one_entry_per_city"
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["country_code", "city_key"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        # No email here: __str__ ends up in logs and admin history.
        return f"{self.city}, {self.country_code}"

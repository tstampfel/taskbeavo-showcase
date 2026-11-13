"""On-site contacts (plan: CARE_CONTACT): the person a helper meets, e.g. "Mama Kata".

An on-site contact never has an account; they exist only as a record on the poster's
account, so one diaspora account can manage help for a mother in Gruž and an uncle in
Konavle. Only name, phone, area and language are collected, never health details, and
the poster confirms they have the person's agreement (plan: "GDPR flows").
"""

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.crypto import EncryptedTextField
from core.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES


class CareContact(models.Model):
    """One on-site contact of `owner`, the poster.

    The phone is encrypted at rest and shown only to the owner and to the helper of an
    active accepted booking for this contact (hard rule e, `care_contacts.policy`). There
    is one live record: an edit reaches every active booking at once.

    Deleting the owner deletes their contacts (CASCADE); `care_contacts.gdpr` holds the
    deletion and export helpers for the account deletion and "Download my data" flows.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="care_contacts"
    )
    name = models.CharField(max_length=100)
    # E.164, encrypted. Mobile or landline (care_contacts/phone.py).
    phone_number = EncryptedTextField()
    # Whether TaskBeavo can text the number: a mobile in a country it has an SMS sender
    # for. Reminders skip SMS when False (landlines).
    sms_capable = models.BooleanField(default=False)
    # Free text such as "Gruž, Dubrovnik". The exact address lives on the task.
    area = models.CharField(max_length=100)
    # The language SMS to the contact go out in.
    language = models.CharField(
        max_length=2,
        choices=[(code, code) for code in SUPPORTED_LOCALES],
        default=DEFAULT_LOCALE,
    )
    # Reminders go to these contacts by SMS instead of push, if `sms_capable`.
    no_smartphone = models.BooleanField(default=False)
    # When the poster confirmed they have the contact's agreement to store their details.
    agreement_confirmed_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "care_contacts"
        ordering: ClassVar = ["created_at", "id"]

    def __str__(self) -> str:
        return f"CareContact {self.pk}"

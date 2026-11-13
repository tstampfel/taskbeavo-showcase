"""Recompute the keyed digests (core.crypto.pii_digest) after PII_DIGEST_KEY changed.

Run it right after deploying a new PII_DIGEST_KEY: until it finishes, a stored digest
doesn't match a new one, so a number verified on one account could be verified on a
second. Unused SMS codes are deleted, because their hash is bound to the old digest;
people waiting for one ask again. Helper OIB digests (helpers.HelperProfile) are
recomputed too; a new digest column is added here as well.
"""

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import PhoneCode, User
from core.crypto import pii_digest
from helpers.models import HelperProfile

_BATCH = 500


class Command(BaseCommand):
    help = "Recompute PII digests from the decrypted values under the current PII_DIGEST_KEY."

    def handle(self, *args: Any, **options: Any) -> None:
        verified = User.objects.filter(phone_digest__isnull=False).only("pk", "phone_number")
        count = 0
        with transaction.atomic():
            rows = list(verified.iterator(chunk_size=_BATCH))
            # Clear first, so swapping digests between rows can't trip the unique constraint.
            verified.update(phone_digest=None)
            for user in rows:
                user.phone_digest = pii_digest("phone", user.phone_number)
                user.save(update_fields=["phone_digest"])
                count += 1
            codes, _ = PhoneCode.objects.filter(used_at__isnull=True).delete()
            helpers = HelperProfile.objects.filter(oib_digest__isnull=False).only("pk", "oib")
            profiles = list(helpers.iterator(chunk_size=_BATCH))
            helpers.update(oib_digest=None)
            for profile in profiles:
                profile.oib_digest = pii_digest("oib", profile.oib)
                profile.save(update_fields=["oib_digest"])
            companies = HelperProfile.objects.exclude(business_oib_digest="")
            for profile in companies.only("pk", "business_oib").iterator(chunk_size=_BATCH):
                profile.business_oib_digest = pii_digest("oib", profile.business_oib)
                profile.save(update_fields=["business_oib_digest"])
        self.stdout.write(
            f"{count} phone digests recomputed, {codes} unused phone codes deleted, "
            f"{len(profiles)} OIB digests recomputed"
        )

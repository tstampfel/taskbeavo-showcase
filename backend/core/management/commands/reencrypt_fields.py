"""Re-encrypt every EncryptedTextField under the first FIELD_ENCRYPTION_KEY.

Rotation: put the new key first in FIELD_ENCRYPTION_KEY (keep the old one after it),
deploy, run this, then drop the old key. Values are only decrypted and re-encrypted in
memory; nothing is printed but counts.
"""

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

from core.crypto import EncryptedTextField

_BATCH = 500


class Command(BaseCommand):
    help = "Re-encrypt encrypted columns under the first FIELD_ENCRYPTION_KEY."

    def handle(self, *args: Any, **options: Any) -> None:
        for model in apps.get_models():
            fields = [f.name for f in model._meta.get_fields() if isinstance(f, EncryptedTextField)]
            if not fields:
                continue
            count = 0
            # Loading decrypts with any configured key; saving encrypts with the first.
            for row in model._default_manager.only("pk", *fields).iterator(chunk_size=_BATCH):
                with transaction.atomic():
                    row.save(update_fields=fields)
                count += 1
            self.stdout.write(f"{model._meta.label}: {count} rows re-encrypted")

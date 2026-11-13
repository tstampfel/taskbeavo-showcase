"""Deleting an upload row deletes its R2 objects, after the transaction commits.

Covers every path: the sweep, a cascade from account deletion, an owning object that
deletes its photos, discardUpload. Every object the row may have (`stored_objects`) is
deleted at once, and again once nothing can still write it (services.second_delete_after):
after the presigned PUT expires, so a PUT landing after a discard is removed, and after
any processing or publishing run still writing has ended, so a run that dies between its
writes and its re-check can't leave an evidence original or a public copy behind.
"""

from typing import Any

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from uploads.models import Upload


@receiver(post_delete, sender=Upload, dispatch_uid="uploads-delete-objects")
def delete_objects_of(sender: type[Upload], instance: Upload, **_kwargs: Any) -> None:
    objects = instance.stored_objects()
    if not objects:
        return
    from uploads.services import delete_later, second_delete_after
    from uploads.tasks import delete_objects

    keys = [[bucket, key] for bucket, key in objects]
    transaction.on_commit(lambda: delete_objects.delay(keys))
    delete_later(objects, second_delete_after(instance))

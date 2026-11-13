from uuid import UUID

from celery import shared_task

from uploads import services, storage


@shared_task(
    name="uploads.process",
    autoretry_for=(storage.StorageError,),
    retry_backoff=10,
    retry_backoff_max=600,
    max_retries=6,
    soft_time_limit=120,
    time_limit=150,
)
def process_upload(upload_id: str) -> str:
    """Check and re-encode one uploaded photo (uploads/services.py `process`)."""
    return services.process(UUID(upload_id))


@shared_task(
    name="uploads.publish",
    autoretry_for=(storage.StorageError,),
    retry_backoff=10,
    retry_backoff_max=600,
    max_retries=8,
    # Bounds how long a run can still be copying (services.PUBLISH_RETRY_WINDOW).
    soft_time_limit=60,
    time_limit=90,
)
def publish_upload(upload_id: str) -> str:
    """Copy a claimed task photo to the public bucket (uploads/services.py `publish`)."""
    return services.publish(UUID(upload_id))


@shared_task(
    name="uploads.delete_objects",
    autoretry_for=(storage.StorageError,),
    retry_backoff=30,
    retry_backoff_max=3600,
    max_retries=10,
)
def delete_objects(keys: list[list[str]]) -> int:
    """Delete R2 objects of deleted uploads. Arguments are bucket names and ID-only keys."""
    services.delete_objects((bucket, key) for bucket, key in keys)
    return len(keys)


@shared_task(name="uploads.sweep")
def sweep_uploads() -> int:
    """Beat task: delete abandoned uploads and re-queue stuck ones."""
    return services.sweep()

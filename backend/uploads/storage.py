"""Cloudflare R2 through its S3-compatible API.

Two buckets, both from the environment (backend-deploy skill):

- `R2_PRIVATE_BUCKET`, never public: upload originals, evidence photos and, later, fiscal
  receipt PDFs (`put_private`). Read only through short-lived presigned GET URLs.
- `R2_PUBLIC_BUCKET`, served at `R2_PUBLIC_BASE_URL`: only metadata-free task photos that
  a task has claimed (`copy`, from the private bucket).

Tests replace the client with `use_client` (uploads/tests/fakes.py); nothing here is
called over the network in tests.
"""

from functools import cache
from typing import Any, NamedTuple, Protocol, cast

from django.conf import settings

from core.errors import SafeMessageError


class StorageError(SafeMessageError):
    """R2 refused or couldn't be reached. Retried by the Celery task."""


class ObjectMissingError(StorageError):
    """No object at the key."""


class ObjectChangedError(StorageError):
    """The object at the key is no longer the one whose ETag was recorded: it was replaced.

    Not worth retrying: the caller rejects what it was reading.
    """


class Head(NamedTuple):
    length: int
    content_type: str
    # Changes whenever the object is replaced, so it pins the exact bytes that were seen.
    etag: str


class Client(Protocol):
    """The part of botocore's S3 client this module uses."""

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int
    ) -> str: ...
    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...
    def get_object(self, *, Bucket: str, Key: str, IfMatch: str) -> dict[str, Any]: ...
    def copy_object(
        self, *, Bucket: str, Key: str, CopySource: dict[str, str]
    ) -> dict[str, Any]: ...
    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> dict[str, Any]: ...
    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...


_override: Client | None = None


def use_client(client: Client | None) -> None:
    """Replace the R2 client (tests), or go back to the real one with None."""
    global _override
    _override = client


@cache
def _r2_client() -> Client:
    import boto3
    from botocore.config import Config

    if not (settings.R2_ENDPOINT_URL and settings.R2_ACCESS_KEY_ID):
        raise StorageError("R2 is not configured; set the R2_* environment variables.")
    r2 = boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            # R2 doesn't take the CRC checksums newer SDKs add to every request.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
    return cast(Client, r2)


def client() -> Client:
    return _override if _override is not None else _r2_client()


def presigned_put(bucket: str, key: str, content_type: str, size_bytes: int) -> str:
    """A PUT URL that signs the content type and exact length: R2 refuses anything else."""
    return client().generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ContentType": content_type,
            "ContentLength": size_bytes,
        },
        ExpiresIn=settings.UPLOAD_URL_TTL_SECONDS,
    )


def presigned_get(bucket: str, key: str) -> str:
    return client().generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=settings.UPLOAD_READ_URL_TTL_SECONDS,
    )


def head(bucket: str, key: str) -> Head:
    try:
        found = client().head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        raise _storage_error(exc, "head") from exc
    return Head(
        int(found["ContentLength"]), str(found.get("ContentType", "")), str(found.get("ETag", ""))
    )


def read(bucket: str, key: str, max_bytes: int, *, etag: str) -> bytes:
    """The object's bytes, reading at most `max_bytes` + 1 so an oversized one is caught.

    Only the object `head` saw with `etag`: R2 refuses the GET (If-Match) when the object
    was replaced since, and the ETag of what it returns is checked too. Either way it
    raises ObjectChangedError.
    """
    if not etag:
        # Nothing pins the object (a row from before ETags were recorded): never read it.
        raise ObjectChangedError("R2 get: no ETag to match")
    try:
        found = client().get_object(Bucket=bucket, Key=key, IfMatch=etag)
        if str(found.get("ETag", "")) != etag:
            raise ObjectChangedError("R2 get: object changed")
        return found["Body"].read(max_bytes + 1)
    except StorageError:
        raise
    except Exception as exc:
        raise _storage_error(exc, "get") from exc


def copy(source_bucket: str, source_key: str, bucket: str, key: str) -> None:
    """Copy an object inside R2 (same account), keeping its content type."""
    try:
        client().copy_object(
            Bucket=bucket, Key=key, CopySource={"Bucket": source_bucket, "Key": source_key}
        )
    except Exception as exc:
        raise _storage_error(exc, "copy") from exc


def put(bucket: str, key: str, body: bytes, content_type: str) -> None:
    try:
        client().put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
    except Exception as exc:
        raise _storage_error(exc, "put") from exc


def put_private(key: str, body: bytes, content_type: str) -> None:
    """Store a server-made file (a receipt PDF, an export) in the private bucket."""
    put(settings.R2_PRIVATE_BUCKET, key, body, content_type)


def delete(bucket: str, key: str) -> None:
    """Delete an object; deleting a missing one succeeds, so retries are safe."""
    try:
        client().delete_object(Bucket=bucket, Key=key)
    except Exception as exc:
        error = _storage_error(exc, "delete")
        if not isinstance(error, ObjectMissingError):
            raise error from exc


def public_url(key: str) -> str:
    return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"


def _storage_error(exc: Exception, operation: str) -> StorageError:
    # botocore's ClientError carries the S3 error code; its message can quote the key.
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    if code in {"404", "NoSuchKey", "NotFound"}:
        return ObjectMissingError(f"R2 {operation}: no such object")
    if code in {"412", "PreconditionFailed"}:
        return ObjectChangedError(f"R2 {operation}: object changed")
    return StorageError(f"R2 {operation} failed: {type(exc).__name__} {code}".rstrip())

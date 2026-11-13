"""An in-memory stand-in for R2 and photo fixtures. Tests never reach the network."""

import hashlib
import io
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from botocore.exceptions import ClientError
from PIL import Image
from PIL.ExifTags import GPS, IFD, Base

# Dubrovnik, Stradun: 42°38'27.6"N 18°06'36"E.
GPS_LATITUDE = (42.0, 38.0, 27.6)
GPS_LONGITUDE = (18.0, 6.0, 36.0)
TAKEN_AT = "2026:10:30 09:15:00"
XMP_WITH_GPS = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
    b'xmlns:exif="http://ns.adobe.com/exif/1.0/" exif:GPSLatitude="42,38.46N" '
    b'exif:GPSLongitude="18,6.6E"/></rdf:RDF></x:xmpmeta>'
)


def photo_exif() -> Image.Exif:
    """EXIF as a phone camera writes it: GPS position, capture time, camera."""
    exif = Image.Exif()
    exif[Base.Make] = "TestPhone"
    exif[Base.Model] = "Camera 1"
    exif[IFD.Exif] = {Base.DateTimeOriginal: TAKEN_AT, Base.OffsetTimeOriginal: "+01:00"}
    exif[IFD.GPSInfo] = {
        GPS.GPSLatitudeRef: "N",
        GPS.GPSLatitude: GPS_LATITUDE,
        GPS.GPSLongitudeRef: "E",
        GPS.GPSLongitude: GPS_LONGITUDE,
    }
    return exif


def photo_with_gps(fmt: str = "JPEG", size: tuple[int, int] = (64, 48)) -> bytes:
    """A photo as a phone camera writes it: EXIF with GPS (`photo_exif`), plus XMP."""
    image = Image.new("RGB", size, (31, 92, 70))
    out = io.BytesIO()
    options: dict[str, Any] = {"exif": photo_exif()}
    if fmt == "JPEG":
        options["xmp"] = XMP_WITH_GPS
    image.save(out, fmt, **options)
    return out.getvalue()


def has_gps(data: bytes) -> bool:
    image = Image.open(io.BytesIO(data))
    exif = image.getexif()
    return bool(exif.get_ifd(IFD.GPSInfo)) or b"GPSLatitude" in data


@dataclass
class StoredObject:
    body: bytes
    content_type: str

    @property
    def etag(self) -> str:
        # As S3 and R2 report it for a single-part upload: the quoted MD5 of the body.
        return f'"{hashlib.md5(self.body).hexdigest()}"'  # noqa: S324 - not security


@dataclass(frozen=True)
class SignedPut:
    bucket: str
    key: str
    content_type: str
    content_length: int


class FakeR2:
    """Buckets in a dict. `client_put` does what R2 does with a presigned PUT: it accepts
    the bytes only with the signed content type and exact signed length."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], StoredObject] = {}
        self.signed: dict[str, SignedPut] = {}
        self.signed_gets: list[tuple[str, str, int]] = []

    # --- the botocore client surface used by uploads/storage.py ---

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int
    ) -> str:
        bucket, key = Params["Bucket"], Params["Key"]
        token = f"sig{len(self.signed) + len(self.signed_gets)}"
        url = f"https://fake-r2.invalid/{bucket}/{key}?X-Amz-Expires={ExpiresIn}&sig={token}"
        if ClientMethod == "put_object":
            self.signed[token] = SignedPut(
                bucket, key, Params["ContentType"], int(Params["ContentLength"])
            )
        else:
            self.signed_gets.append((bucket, key, ExpiresIn))
        return url

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        found = self._get(Bucket, Key)
        return {
            "ContentLength": len(found.body),
            "ContentType": found.content_type,
            "ETag": found.etag,
        }

    def get_object(self, *, Bucket: str, Key: str, IfMatch: str) -> dict[str, Any]:
        found = self._get(Bucket, Key)
        if IfMatch != found.etag:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": "Precondition failed"}},
                "GetObject",
            )
        return {"Body": io.BytesIO(found.body), "ETag": found.etag}

    def copy_object(self, *, Bucket: str, Key: str, CopySource: dict[str, str]) -> dict[str, Any]:
        source = self._get(CopySource["Bucket"], CopySource["Key"])
        self.objects[(Bucket, Key)] = StoredObject(source.body, source.content_type)
        return {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> dict[str, Any]:
        self.objects[(Bucket, Key)] = StoredObject(Body, ContentType)
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.objects.pop((Bucket, Key), None)
        return {}

    # --- what a browser or the app does with the URL ---

    def client_put(self, url: str, body: bytes, content_type: str) -> int:
        """PUT `body` to a presigned URL; returns the HTTP status R2 would."""
        token = parse_qs(urlsplit(url).query)["sig"][0]
        signed = self.signed.get(token)
        if signed is None:
            return 403
        if content_type != signed.content_type or len(body) != signed.content_length:
            return 403  # SignatureDoesNotMatch
        self.objects[(signed.bucket, signed.key)] = StoredObject(body, content_type)
        return 200

    def body(self, bucket: str, key: str) -> bytes:
        return self._get(bucket, key).body

    def keys_in(self, bucket: str) -> set[str]:
        return {key for (found, key) in self.objects if found == bucket}

    def _get(self, bucket: str, key: str) -> StoredObject:
        found = self.objects.get((bucket, key))
        if found is None:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return found

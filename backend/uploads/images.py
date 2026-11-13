"""Checking an uploaded photo and re-encoding it without metadata.

A clean copy is built from the decoded pixels alone, so no EXIF (GPS, camera serial,
capture time), XMP or comment of the original can survive into it, whatever Pillow's
encoders would otherwise carry over.
"""

import io
import warnings
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.gis.geos import Point
from PIL import Image, ImageOps
from PIL.ExifTags import GPS, IFD, Base

# Declared content type -> the format the clean copy is saved in.
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
# Declared content type -> the formats Pillow may detect. Phone JPEGs with a
# Multi-Picture segment (HDR gain maps, depth) open as MPO; their first frame is the photo.
DETECTED_FORMATS = {
    "image/jpeg": frozenset({"JPEG", "MPO"}),
    "image/png": frozenset({"PNG"}),
    "image/webp": frozenset({"WEBP"}),
}
# Cameras write local time; without an offset tag, assume the service area's.
_LOCAL_TIME = ZoneInfo("Europe/Zagreb")


class InvalidImageError(Exception):
    """Not a decodable image of the declared type."""


class ImageTooLargeError(Exception):
    """More pixels than UPLOAD_MAX_PIXELS: refused before decoding (decompression bombs)."""


@dataclass(frozen=True)
class CleanImage:
    data: bytes
    width: int
    height: int


@dataclass(frozen=True)
class Capture:
    """Where and when an evidence photo was taken, from its EXIF. Either may be missing."""

    taken_at: datetime | None
    location: Point | None


def open_checked(data: bytes, content_type: str) -> Image.Image:
    """Decode `data`, which must be an image in the format `content_type` declares."""
    expected = DETECTED_FORMATS.get(content_type, frozenset())
    with warnings.catch_warnings():
        # Pillow warns between MAX_IMAGE_PIXELS and twice that, and raises above it.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = settings.UPLOAD_MAX_PIXELS
        try:
            image = Image.open(io.BytesIO(data))
            if image.format not in expected:
                raise InvalidImageError
            image.load()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageTooLargeError from exc
        except InvalidImageError:
            raise
        except (OSError, SyntaxError, ValueError) as exc:
            raise InvalidImageError from exc
        finally:
            Image.MAX_IMAGE_PIXELS = previous
    return image


def clean_copy(image: Image.Image, content_type: str) -> CleanImage:
    """Upright (EXIF orientation applied), at most UPLOAD_MAX_DIMENSION_PX, no metadata."""
    upright = ImageOps.exif_transpose(image)
    if upright.mode not in ("RGB", "RGBA", "L"):
        upright = upright.convert("RGBA" if "A" in upright.getbands() else "RGB")
    if content_type == "image/jpeg" and upright.mode == "RGBA":
        upright = upright.convert("RGB")
    limit = settings.UPLOAD_MAX_DIMENSION_PX
    upright.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    # Pixels only: the new image has an empty `info`, so nothing else is written.
    pixels = Image.frombytes(upright.mode, upright.size, upright.tobytes())
    out = io.BytesIO()
    pixels.save(out, FORMATS[content_type], **_save_options(content_type))
    return CleanImage(out.getvalue(), pixels.width, pixels.height)


def capture_of(image: Image.Image) -> Capture:
    """The capture time and GPS position recorded in the original's EXIF."""
    exif = image.getexif()
    return Capture(_taken_at(exif), _location(exif.get_ifd(IFD.GPSInfo)))


def _save_options(content_type: str) -> dict[str, object]:
    if content_type == "image/jpeg":
        return {"quality": 85, "optimize": True}
    if content_type == "image/webp":
        return {"quality": 85}
    return {"optimize": True}


def _taken_at(exif: Image.Exif) -> datetime | None:
    details = exif.get_ifd(IFD.Exif)
    raw = details.get(Base.DateTimeOriginal) or exif.get(Base.DateTime)
    if not isinstance(raw, str):
        return None
    try:
        local = datetime.strptime(raw.strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    offset = details.get(Base.OffsetTimeOriginal)
    if isinstance(offset, str):
        try:
            return datetime.strptime(f"{raw.strip()} {offset.strip()}", "%Y:%m:%d %H:%M:%S %z")
        except ValueError:
            pass
    return local.replace(tzinfo=_LOCAL_TIME)


def _location(gps: dict[int, object]) -> Point | None:
    try:
        latitude = _degrees(gps[GPS.GPSLatitude], gps.get(GPS.GPSLatitudeRef), "S")
        longitude = _degrees(gps[GPS.GPSLongitude], gps.get(GPS.GPSLongitudeRef), "W")
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return Point(longitude, latitude, srid=4326)


def _degrees(value: object, ref: object, negative: str) -> float:
    if not isinstance(value, tuple | list) or len(value) != 3:
        raise ValueError
    degrees, minutes, seconds = (float(part) for part in value)
    sign = -1 if isinstance(ref, str) and ref.strip().upper() == negative else 1
    return sign * (degrees + minutes / 60 + seconds / 3600)

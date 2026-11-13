"""Keeps the MaxMind GeoLite2 City database (GEOIP_CITY_DATABASE) current, as the GeoLite
EULA requires (BEAV-190).

The process that looks locations up keeps its own copy: the web service, at sign-in (manager
decision 2026-11-07; on Render each service has its own disk, so a copy the worker downloaded
would never reach it). config/asgi.py starts one background thread per process,
`start_background_updates`, which runs `update_database` at once and then every
CHECK_EVERY_SECONDS, whether or not anyone signs in (review R1-1); a request never waits for it,
and until a copy exists the lookup returns no city. It downloads a new release when there is
none or the copy in use was built DOWNLOAD_AFTER ago, so weekly (MaxMind publishes twice a
week), but at most once a day (RETRY_AFTER), so a late MaxMind release isn't downloaded every
hour. The download is streamed to disk and replaces the old file atomically, so no older copy
stays. A copy whose build is more than MAX_AGE old is deleted and never read (geo.py), even when
downloads keep failing or no MaxMind account is set. Only the city and country names are ever
read from it (geo.py; EULA §5 and §6.3). `manage.py update_geolite` runs the same step by hand.
"""

import datetime as dt
import fcntl
import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

import maxminddb
import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DOWNLOAD_URL = "https://download.maxmind.com/geoip/databases/GeoLite2-City/download"
DATABASE_MEMBER = "GeoLite2-City.mmdb"
DOWNLOAD_AFTER = dt.timedelta(days=7)
RETRY_AFTER = dt.timedelta(days=1)
MAX_AGE = dt.timedelta(days=30)
TIMEOUT_SECONDS = 120
CHUNK_BYTES = 1024 * 1024
TEMP_PREFIX = ".geolite-"
LOCK_NAME = ".geolite.lock"
CHECK_EVERY_SECONDS = 60 * 60

Outcome = Literal["updated", "current", "not_configured", "failed", "busy"]

_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def configured() -> bool:
    """Whether GeoLite2 is set up: a database path and the MaxMind account that keeps it
    current. Without the license key nothing is downloaded or looked up (local, test, CI)."""
    return bool(
        settings.GEOIP_CITY_DATABASE
        and settings.MAXMIND_ACCOUNT_ID
        and settings.MAXMIND_LICENSE_KEY
    )


def start_background_updates(stop: threading.Event | None = None) -> bool:
    """Starts this process's update thread once: an update now, then every
    CHECK_EVERY_SECONDS until `stop` is set. It runs with only a database path set too, so an
    old copy is still deleted after the MaxMind account is removed. True when it started."""
    global _worker
    if not settings.GEOIP_CITY_DATABASE:
        return False
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return False
        _worker = threading.Thread(
            target=run_updates,
            args=(stop or threading.Event(),),
            name="geolite-update",
            daemon=True,
        )
        _worker.start()
    return True


def run_updates(stop: threading.Event) -> None:
    """The update thread's loop. Never raises: a failure is logged and retried next round."""
    while True:
        try:
            update_database(
                settings.GEOIP_CITY_DATABASE,
                settings.MAXMIND_ACCOUNT_ID,
                settings.MAXMIND_LICENSE_KEY,
            )
        except Exception as exc:
            logger.error("GeoLite2 update failed: %s", type(exc).__name__)
        if stop.wait(CHECK_EVERY_SECONDS):
            return


class UpdateError(Exception):
    """The download could not be verified or read; the copy in use stays."""


def build_time(path: Path) -> dt.datetime | None:
    """When the database at `path` was built (its metadata), or None if it isn't readable."""
    try:
        reader = maxminddb.open_database(str(path))
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        return None
    try:
        return dt.datetime.fromtimestamp(reader.metadata().build_epoch, tz=dt.UTC)
    finally:
        reader.close()


def is_expired(built: dt.datetime, now: dt.datetime | None = None) -> bool:
    return (now or timezone.now()) - built > MAX_AGE


def update_database(
    path: str,
    account_id: str,
    license_key: str,
    *,
    http: Any = None,
    now: dt.datetime | None = None,
) -> Outcome:
    """Downloads a new GeoLite2 City release into `path` when the copy there is due, then
    deletes a copy older than MAX_AGE. Never raises for a failed download; "busy" when
    another process is updating the same file."""
    if not path:
        return "not_configured"
    http = http or requests
    now = now or timezone.now()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # One update at a time per disk: gunicorn's workers share the file.
    with open(target.parent / LOCK_NAME, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"
        return _update_locked(target, account_id, license_key, http, now)


def _update_locked(
    target: Path, account_id: str, license_key: str, http: Any, now: dt.datetime
) -> Outcome:
    outcome: Outcome
    built = build_time(target) if target.exists() else None
    if built is not None and (
        now - built < DOWNLOAD_AFTER or now - _downloaded_at(target) < RETRY_AFTER
    ):
        outcome = "current"
    elif not (account_id and license_key):
        outcome = "not_configured"
    else:
        try:
            _download(target, (account_id, license_key), http)
            outcome = "updated"
        except (requests.RequestException, UpdateError, OSError, tarfile.TarError) as exc:
            # The exception type only: a message could carry the license key from a URL.
            logger.error("GeoLite2 download failed: %s", type(exc).__name__)
            outcome = "failed"
    purge(target, now)
    return outcome


def _downloaded_at(target: Path) -> dt.datetime:
    """When the copy was written: os.replace keeps the downloaded file's modification time."""
    return dt.datetime.fromtimestamp(target.stat().st_mtime, tz=dt.UTC)


def purge(target: Path, now: dt.datetime) -> None:
    """Deletes the database when its build is older than MAX_AGE (or unreadable), and any
    download left over from an interrupted update."""
    if target.exists():
        built = build_time(target)
        if built is None or is_expired(built, now):
            target.unlink(missing_ok=True)
            logger.warning("GeoLite2 database deleted: older than %s days", MAX_AGE.days)
    if target.parent.is_dir():
        for leftover in target.parent.glob(f"{TEMP_PREFIX}*"):
            leftover.unlink(missing_ok=True)


def _download(target: Path, auth: tuple[str, str], http: Any) -> None:
    expected = _expected_checksum(_fetch(http, auth, "tar.gz.sha256"))
    fd, temp = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=target.parent)
    try:
        with tempfile.TemporaryFile(dir=target.parent, prefix=TEMP_PREFIX) as archive:
            if _stream(http, auth, "tar.gz", archive) != expected:
                raise UpdateError("checksum mismatch")
            archive.seek(0)
            with os.fdopen(fd, "wb") as out:
                _extract(archive, out)
        if build_time(Path(temp)) is None:
            raise UpdateError("not a readable database")
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)


def _expected_checksum(body: bytes) -> str:
    """The SHA-256 from MaxMind's `.sha256` file ("<hex>  <file name>")."""
    try:
        digest = body.decode("ascii").split()[0].lower()
    except (UnicodeDecodeError, IndexError):
        raise UpdateError("unreadable checksum") from None
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise UpdateError("unreadable checksum")
    return digest


def _fetch(http: Any, auth: tuple[str, str], suffix: str) -> bytes:
    response = http.get(DOWNLOAD_URL, params={"suffix": suffix}, auth=auth, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


def _stream(http: Any, auth: tuple[str, str], suffix: str, out: Any) -> str:
    """Streams the download into `out` (the archive is about 35 MB) and returns its SHA-256."""
    digest = hashlib.sha256()
    response = http.get(
        DOWNLOAD_URL, params={"suffix": suffix}, auth=auth, timeout=TIMEOUT_SECONDS, stream=True
    )
    try:
        response.raise_for_status()
        for chunk in response.iter_content(CHUNK_BYTES):
            digest.update(chunk)
            out.write(chunk)
    finally:
        response.close()
    return digest.hexdigest()


def _extract(archive: Any, out: Any) -> None:
    """Copies the database out of the archive without holding it in memory."""
    with tarfile.open(fileobj=archive, mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and Path(member.name).name == DATABASE_MEMBER:
                data = tar.extractfile(member)
                if data is not None:
                    shutil.copyfileobj(data, out, CHUNK_BYTES)
                    return
    raise UpdateError("no database in the archive")

"""The GeoLite2 City database stays current and never older than 30 days (BEAV-190)."""

import datetime as dt
import hashlib
import io
import os
import tarfile
import threading
from pathlib import Path
from typing import Any

import pytest
import requests

from accounts import geo, geolite

NOW = dt.datetime(2026, 11, 7, 12, tzinfo=dt.UTC)


class FakeReader:
    """Stands in for maxminddb: the file's bytes are its build date (ISO), or junk."""

    def __init__(self, path: str) -> None:
        text = Path(path).read_text()
        try:
            self.built = dt.datetime.fromisoformat(text)
        except ValueError:
            raise geolite.maxminddb.InvalidDatabaseError("junk") from None

    def metadata(self) -> Any:
        return type("Metadata", (), {"build_epoch": int(self.built.timestamp())})()

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def fake_maxminddb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geolite.maxminddb, "open_database", FakeReader)
    geo._open.cache_clear()


def archive(content: bytes, name: str = "GeoLite2-City_20261106/GeoLite2-City.mmdb") -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


class Response:
    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content = content
        self.status = status
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")

    def iter_content(self, chunk_size: int) -> Any:
        for start in range(0, len(self.content), 7):
            yield self.content[start : start + 7]

    def close(self) -> None:
        self.closed = True


class MaxMind:
    """The download endpoint: the archive and its SHA-256 file."""

    def __init__(
        self,
        tarball: bytes,
        checksum: str | None = None,
        status: int = 200,
        checksum_body: bytes | None = None,
    ) -> None:
        self.tarball = tarball
        self.checksum = checksum or hashlib.sha256(tarball).hexdigest()
        self.checksum_body = checksum_body
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Response:
        self.calls.append({"url": url, **kwargs})
        if kwargs["params"]["suffix"] == "tar.gz.sha256":
            if self.checksum_body is not None:
                return Response(self.checksum_body, self.status)
            return Response(
                f"{self.checksum}  GeoLite2-City_20261106.tar.gz\n".encode(), self.status
            )
        return Response(self.tarball, self.status)


def files(directory: Path) -> list[str]:
    """The directory's files, without the update lock."""
    return sorted(p.name for p in directory.iterdir() if p.name != geolite.LOCK_NAME)


def write_database(path: Path, built: dt.datetime, downloaded: dt.datetime | None = None) -> None:
    """A database built at `built`, downloaded (its modification time) then or at `downloaded`."""
    path.write_text(built.isoformat())
    stamp = (downloaded or built).timestamp()
    os.utime(path, (stamp, stamp))


def update(path: Path, http: MaxMind, now: dt.datetime = NOW, key: str = "secret") -> str:
    return geolite.update_database(str(path), "123", key, http=http, now=now)


def test_downloads_a_new_release_once_the_copy_is_a_week_old(tmp_path: Path) -> None:
    path = tmp_path / "GeoLite2-City.mmdb"
    write_database(path, NOW - dt.timedelta(days=7))
    http = MaxMind(archive(b"2026-11-06T00:00:00+00:00"))

    assert update(path, http) == "updated"
    assert geolite.build_time(path) == dt.datetime(2026, 11, 6, tzinfo=dt.UTC)
    assert http.calls[0]["url"] == geolite.DOWNLOAD_URL
    assert http.calls[0]["auth"] == ("123", "secret")
    # The archive is streamed to disk, never held whole in memory (review R1-5).
    assert http.calls[1]["stream"] is True
    # Replaced in place: no older copy and no temporary file stays.
    assert files(tmp_path) == ["GeoLite2-City.mmdb"]


def test_keeps_a_copy_younger_than_seven_days(tmp_path: Path) -> None:
    path = tmp_path / "GeoLite2-City.mmdb"
    write_database(path, NOW - dt.timedelta(days=6))
    http = MaxMind(archive(b"unused"))

    assert update(path, http) == "current"
    assert http.calls == []


def test_downloads_the_first_copy(tmp_path: Path) -> None:
    path = tmp_path / "geoip" / "GeoLite2-City.mmdb"

    assert update(path, MaxMind(archive(b"2026-11-06T00:00:00+00:00"))) == "updated"
    assert path.exists()


@pytest.mark.parametrize(
    "http",
    [
        MaxMind(archive(b"2026-11-06T00:00:00+00:00"), checksum="0" * 64),
        MaxMind(archive(b"2026-11-06T00:00:00+00:00"), status=401),
        MaxMind(archive(b"2026-11-06T00:00:00+00:00", name="README.txt")),
        MaxMind(archive(b"not a database")),
        MaxMind(archive(b"2026-11-06T00:00:00+00:00"), checksum_body=b""),
        MaxMind(archive(b"2026-11-06T00:00:00+00:00"), checksum_body=b"\xff\xfe"),
        MaxMind(archive(b"2026-11-06T00:00:00+00:00"), checksum_body=b"abc  x.tar.gz"),
    ],
    ids=[
        "checksum",
        "http-error",
        "no-database",
        "unreadable",
        "empty-checksum",
        "binary-checksum",
        "short-checksum",
    ],
)
def test_a_failed_download_keeps_the_copy_in_use(tmp_path: Path, http: MaxMind) -> None:
    path = tmp_path / "GeoLite2-City.mmdb"
    write_database(path, NOW - dt.timedelta(days=10))

    assert update(path, http) == "failed"
    assert geolite.build_time(path) == NOW - dt.timedelta(days=10)
    assert files(tmp_path) == ["GeoLite2-City.mmdb"]


def test_a_copy_older_than_30_days_is_deleted_even_when_downloads_fail(tmp_path: Path) -> None:
    path = tmp_path / "GeoLite2-City.mmdb"
    write_database(path, NOW - dt.timedelta(days=31))

    assert update(path, MaxMind(b"", status=500)) == "failed"
    assert not path.exists()


def test_without_a_maxmind_account_nothing_is_downloaded_but_old_copies_go(
    tmp_path: Path,
) -> None:
    path = tmp_path / "GeoLite2-City.mmdb"
    write_database(path, NOW - dt.timedelta(days=31))
    http = MaxMind(archive(b"unused"))

    assert update(path, http, key="") == "not_configured"
    assert http.calls == []
    assert not path.exists()
    assert geolite.update_database("", "123", "secret", http=http, now=NOW) == "not_configured"


def test_a_download_interrupted_earlier_is_cleaned_up(tmp_path: Path) -> None:
    path = tmp_path / "GeoLite2-City.mmdb"
    write_database(path, NOW - dt.timedelta(days=1))
    (tmp_path / ".geolite-abc").write_bytes(b"partial")

    assert update(path, MaxMind(archive(b"unused"))) == "current"
    assert files(tmp_path) == ["GeoLite2-City.mmdb"]


@pytest.fixture
def configured(settings, tmp_path: Path) -> Path:
    path = tmp_path / "GeoLite2-City.mmdb"
    settings.GEOIP_CITY_DATABASE = str(path)
    settings.MAXMIND_ACCOUNT_ID = "123"
    settings.MAXMIND_LICENSE_KEY = "secret"
    return path


@pytest.fixture
def no_thread(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records update threads instead of starting them."""
    started: list[str] = []
    monkeypatch.setattr(geolite, "_worker", None)

    class Thread:
        def __init__(self, target: Any, args: Any = (), **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            started.append("started")

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(geolite.threading, "Thread", Thread)
    return started


def test_a_copy_built_8_days_ago_but_downloaded_today_is_not_downloaded_again(
    tmp_path: Path,
) -> None:
    """A late MaxMind release isn't fetched every hour (review R1-4)."""
    path = tmp_path / "GeoLite2-City.mmdb"
    write_database(path, NOW - dt.timedelta(days=8), downloaded=NOW - dt.timedelta(hours=3))
    http = MaxMind(archive(b"unused"))

    assert update(path, http) == "current"
    assert http.calls == []

    write_database(path, NOW - dt.timedelta(days=8), downloaded=NOW - dt.timedelta(days=2))
    assert update(path, MaxMind(archive(b"2026-11-06T00:00:00+00:00"))) == "updated"


def test_another_process_updating_the_same_file_is_left_alone(tmp_path: Path) -> None:
    import fcntl

    path = tmp_path / "GeoLite2-City.mmdb"
    http = MaxMind(archive(b"unused"))
    with open(tmp_path / geolite.LOCK_NAME, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert update(path, http) == "busy"
    assert http.calls == []


def test_without_the_license_key_nothing_is_downloaded_or_looked_up(
    configured: Path, settings, monkeypatch: pytest.MonkeyPatch, no_thread: list[str]
) -> None:
    settings.MAXMIND_LICENSE_KEY = ""
    write_database(configured, dt.datetime.now(dt.UTC))

    assert not geo.geoip_available()
    assert geo._reader(str(configured)) is None
    write_database(configured, NOW - dt.timedelta(days=10))
    http = MaxMind(archive(b"unused"))
    assert update(configured, http, key="") == "not_configured"
    assert http.calls == []


def test_one_update_thread_starts_per_process(configured: Path, no_thread: list[str]) -> None:
    assert geolite.start_background_updates()
    assert not geolite.start_background_updates()
    # A lookup doesn't start a second one, and returns no city while there is no copy yet.
    assert geo.coarse_location("81.2.69.160", "en") == ""
    assert no_thread == ["started"]


def test_without_a_database_path_no_thread_starts(settings, no_thread: list[str]) -> None:
    settings.GEOIP_CITY_DATABASE = ""

    assert not geolite.start_background_updates()
    assert no_thread == []


class Clock(threading.Event):
    """A stop event whose waits count rounds: `rounds` waits pass, then the loop stops."""

    def __init__(self, rounds: int) -> None:
        super().__init__()
        self.waits: list[float] = []
        self.rounds = rounds

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout or 0)
        return len(self.waits) > self.rounds


def test_the_update_thread_deletes_an_expired_copy_without_any_sign_in(
    configured: Path, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review R1-1: the 30-day deletion runs on the thread's timer, not at a lookup. Even with
    the MaxMind account removed and downloads impossible."""
    settings.MAXMIND_LICENSE_KEY = ""
    write_database(configured, dt.datetime.now(dt.UTC) - dt.timedelta(days=29))
    ages = iter([dt.timedelta(0), dt.timedelta(days=2)])
    real_now = geolite.timezone.now
    monkeypatch.setattr(geolite.timezone, "now", lambda: real_now() + next(ages))

    geolite.run_updates(Clock(rounds=1))

    assert not configured.exists()


def test_the_update_thread_refreshes_weekly_and_survives_failures(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = MaxMind(archive(b"2026-11-06T00:00:00+00:00"))
    calls: list[str] = []

    def get(url: str, **kwargs: Any) -> Response:
        calls.append(kwargs["params"]["suffix"])
        if len(calls) == 1:
            raise requests.ConnectionError("down")
        return http.get(url, **kwargs)

    monkeypatch.setattr(
        geolite,
        "requests",
        type("R", (), {"get": get, "RequestException": requests.RequestException}),
    )
    monkeypatch.setattr(geolite.timezone, "now", lambda: NOW)
    clock = Clock(rounds=2)

    geolite.run_updates(clock)

    # Round 1 failed, round 2 downloaded, round 3 found the copy current; an hour between each.
    assert geolite.build_time(configured) == dt.datetime(2026, 11, 6, tzinfo=dt.UTC)
    assert calls == ["tar.gz.sha256", "tar.gz.sha256", "tar.gz"]
    assert clock.waits == [geolite.CHECK_EVERY_SECONDS] * 3


def test_an_unexpected_error_doesnt_end_the_update_thread(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[str] = []

    def broken(*args: Any, **kwargs: Any) -> str:
        runs.append("run")
        raise RuntimeError("boom")

    monkeypatch.setattr(geolite, "update_database", broken)

    geolite.run_updates(Clock(rounds=2))

    assert runs == ["run", "run", "run"]


def test_lookups_stop_using_a_copy_older_than_30_days(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = configured
    monkeypatch.setattr(geolite, "start_background_updates", lambda: False)
    write_database(path, dt.datetime.now(dt.UTC) - dt.timedelta(days=2))
    assert geo._reader(str(path)) is not None

    write_database(path, dt.datetime.now(dt.UTC) - dt.timedelta(days=31))
    # A new modification time: the replaced file is reopened.
    os.utime(path, ns=(1, 1))
    assert geo._reader(str(path)) is None
    assert geo.coarse_location("81.2.69.160", "en") == ""


def test_only_the_city_and_country_names_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        def get(self, address: str) -> dict[str, Any]:
            return {
                "city": {"names": {"en": "Munich"}},
                "country": {"names": {"en": "Germany"}},
                "location": {"latitude": 48.1, "longitude": 11.6, "accuracy_radius": 20},
                "postal": {"code": "80331"},
                "subdivisions": [{"names": {"en": "Bavaria"}}],
            }

    monkeypatch.setattr(geo, "_reader", lambda path: Reader())

    assert geo.coarse_location("81.2.69.160", "en") == "Munich, Germany"

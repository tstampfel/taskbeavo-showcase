"""Publishing versions: four languages, the 30-day rule, ordering and drafts."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from legal.models import LegalDocument
from legal.services import PublishRefused, current_document, live_drafts
from legal.tests.factories import bodies, publish_version

pytestmark = pytest.mark.django_db

DAY = timedelta(days=1)


@contextmanager
def freeze_now(now: datetime) -> Iterator[None]:
    with mock.patch("django.utils.timezone.now", return_value=now):
        yield


def version_at(kind: str, language: str, *, at: datetime) -> str | None:
    document = current_document(kind, language, at=at)
    return document.version if document else None


def test_a_version_is_stored_once_per_language() -> None:
    documents = publish_version(version="2026-11")

    assert sorted(d.language for d in documents) == ["de", "en", "fr", "hr"]
    hr = next(d for d in documents if d.language == "hr")
    assert hr.body == "Terms (hr)"
    assert len(hr.body_sha256) == 64


@pytest.mark.parametrize("missing", ["hr", "en", "de", "fr"])
def test_a_version_missing_a_language_is_refused(missing: str) -> None:
    texts = bodies()
    del texts[missing]

    with pytest.raises(PublishRefused, match=f"missing: {missing}"):
        publish_version(bodies=texts)

    assert not LegalDocument.objects.exists()


def test_an_empty_text_counts_as_missing() -> None:
    with pytest.raises(PublishRefused, match="missing: de"):
        publish_version(bodies=bodies() | {"de": "  \n"})


def test_an_unsupported_language_is_refused() -> None:
    with pytest.raises(PublishRefused, match="Unsupported languages: it"):
        publish_version(bodies=bodies() | {"it": "Termini"})


def test_the_same_version_cant_be_published_twice() -> None:
    now = timezone.now()
    publish_version(version="2026-11", now=now)

    with pytest.raises(PublishRefused, match="already exists"):
        publish_version(version="2026-11", effective_at=now + DAY, now=now)


def test_a_version_cant_take_effect_in_the_past() -> None:
    now = timezone.now()

    with pytest.raises(PublishRefused, match="in the past"):
        publish_version(effective_at=now - DAY, now=now)


def test_a_material_change_cant_take_effect_before_a_scheduled_version() -> None:
    now = timezone.now()
    publish_version(version="v1", now=now)
    publish_version(version="v2", effective_at=now + 40 * DAY, now=now)

    with pytest.raises(PublishRefused, match="after every scheduled version"):
        publish_version(version="v3", material_change=True, effective_at=now + 35 * DAY, now=now)


def test_a_small_fix_can_go_in_while_a_material_change_waits() -> None:
    now = timezone.now()
    publish_version(version="v1", now=now)
    publish_version(version="v2", material_change=True, effective_at=now + 30 * DAY, now=now)

    publish_version(version="v1.1", effective_at=now + DAY, now=now)

    assert version_at("TERMS", "en", at=now + 2 * DAY) == "v1.1"
    assert version_at("TERMS", "en", at=now + 30 * DAY) == "v2"


def test_a_material_change_with_under_30_days_notice_is_refused() -> None:
    now = timezone.now()
    publish_version(version="v1", now=now)

    with pytest.raises(PublishRefused, match="30 days' notice"):
        publish_version(
            version="v2",
            material_change=True,
            effective_at=now + 30 * DAY - timedelta(minutes=1),
            now=now,
        )

    assert not LegalDocument.objects.filter(version="v2").exists()


@pytest.mark.parametrize("version", ["2027-03 ", "", "v 2", "x" * 33, "2027/03"])
def test_a_malformed_version_is_refused(version: str) -> None:
    with pytest.raises(PublishRefused, match="1 to 32 letters"):
        publish_version(version=version)


def test_two_versions_cant_take_effect_at_the_same_moment() -> None:
    now = timezone.now()
    publish_version(version="v1", effective_at=now + DAY, now=now)

    with pytest.raises(PublishRefused, match="exact moment"):
        publish_version(version="v2", effective_at=now + DAY, now=now)


def test_a_material_change_with_30_days_notice_is_published() -> None:
    now = timezone.now()
    publish_version(version="v1", now=now)

    documents = publish_version(
        version="v2", material_change=True, effective_at=now + 30 * DAY, now=now
    )

    assert all(d.material_change for d in documents)


def test_a_small_fix_takes_effect_at_once() -> None:
    now = timezone.now()
    publish_version(version="v1", now=now)

    publish_version(version="v1.1", effective_at=now + timedelta(seconds=1), now=now)

    assert version_at("TERMS", "de", at=now + DAY) == "v1.1"


def test_the_first_real_version_needs_no_notice() -> None:
    now = timezone.now()
    with override_settings(LEGAL_DRAFTS_ALLOWED=True):
        publish_version(version="draft", is_draft=True, now=now)

    publish_version(version="v1", material_change=True, effective_at=now + DAY, now=now)


def test_the_30_day_rule_is_per_kind() -> None:
    now = timezone.now()
    publish_version(LegalDocument.Kind.PRIVACY, "v1", now=now)

    publish_version(LegalDocument.Kind.TERMS, "v1", material_change=True, now=now)


def test_the_current_version_is_the_latest_in_effect() -> None:
    now = timezone.now()
    publish_version(version="v1", now=now)
    publish_version(version="v2", material_change=True, effective_at=now + 30 * DAY, now=now)

    assert version_at("TERMS", "fr", at=now + DAY) == "v1"
    assert version_at("TERMS", "fr", at=now + 30 * DAY) == "v2"
    assert current_document("PRIVACY", "fr", at=now + DAY) is None


@override_settings(LEGAL_DRAFTS_ALLOWED=False)
def test_drafts_cant_be_published_where_they_arent_allowed() -> None:
    with pytest.raises(PublishRefused, match="LEGAL_DRAFTS_ALLOWED"):
        publish_version(is_draft=True)


@override_settings(LEGAL_DRAFTS_ALLOWED=True)
def test_live_drafts_are_the_current_and_scheduled_ones() -> None:
    now = timezone.now()
    publish_version(LegalDocument.Kind.TERMS, "draft-1", is_draft=True, now=now)
    publish_version(LegalDocument.Kind.TERMS, "v1", effective_at=now + DAY, now=now)
    publish_version(LegalDocument.Kind.PRIVACY, "v1", now=now)
    publish_version(
        LegalDocument.Kind.PRIVACY, "draft-2", is_draft=True, effective_at=now + DAY, now=now
    )

    # TERMS draft-1 is current until v1 takes effect; PRIVACY draft-2 is scheduled.
    assert {(d.kind, d.version) for d in live_drafts(at=now)} == {
        ("TERMS", "draft-1"),
        ("PRIVACY", "draft-2"),
    }
    assert {(d.kind, d.version) for d in live_drafts(at=now + 2 * DAY)} == {("PRIVACY", "draft-2")}


# --- the command ------------------------------------------------------------------


def write_texts(tmp_path: Path, languages: tuple[str, ...] = ("hr", "en", "de", "fr")) -> list[str]:
    args = []
    for code in languages:
        path = tmp_path / f"terms-{code}.md"
        path.write_text(f"# Uvjeti ({code})\n", encoding="utf-8")
        args += ["--file", f"{code}={path}"]
    return args


def test_the_command_publishes_all_four_languages(tmp_path: Path) -> None:
    call_command(
        "publish_legal_document", "--kind", "TERMS", "--version", "2026-11", *write_texts(tmp_path)
    )

    documents = LegalDocument.objects.filter(kind="TERMS", version="2026-11")
    assert sorted(d.language for d in documents) == ["de", "en", "fr", "hr"]
    assert documents.get(language="de").body == "# Uvjeti (de)\n"
    assert not any(d.material_change or d.is_draft for d in documents)


def test_the_command_refuses_a_missing_language(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="missing: fr"):
        call_command(
            "publish_legal_document",
            "--kind",
            "TERMS",
            "--version",
            "v1",
            *write_texts(tmp_path, ("hr", "en", "de")),
        )


def test_the_command_refuses_a_material_change_with_short_notice(tmp_path: Path) -> None:
    files = write_texts(tmp_path)
    call_command("publish_legal_document", "--kind", "TERMS", "--version", "v1", *files)
    soon = (timezone.localdate() + timedelta(days=10)).isoformat()

    with pytest.raises(CommandError, match="30 days' notice"):
        call_command(
            "publish_legal_document",
            "--kind",
            "TERMS",
            "--version",
            "v2",
            "--material",
            "--effective",
            soon,
            *files,
        )


@pytest.mark.parametrize("utc_time", ["00:30", "10:00", "22:30", "23:30"])
def test_the_suggested_effective_date_is_accepted(tmp_path: Path, utc_time: str) -> None:
    files = write_texts(tmp_path)
    now = datetime.fromisoformat(f"2027-03-16T{utc_time}+00:00")
    publish_version(version="v1", now=now)
    with pytest.raises(PublishRefused) as refused:
        publish_version(version="v2", material_change=True, effective_at=now + DAY, now=now)
    suggested = re.search(r"on or after (\d{4}-\d{2}-\d{2})", str(refused.value))
    assert suggested is not None

    with freeze_now(now):
        call_command(
            "publish_legal_document",
            "--kind",
            "TERMS",
            "--version",
            "v2",
            "--material",
            "--effective",
            suggested[1],
            *files,
        )

    assert LegalDocument.objects.filter(version="v2").count() == 4


def test_the_command_reads_an_effective_date_as_midnight_in_croatia(tmp_path: Path) -> None:
    files = write_texts(tmp_path)
    call_command("publish_legal_document", "--kind", "TERMS", "--version", "v1", *files)
    later = timezone.localdate() + timedelta(days=40)

    call_command(
        "publish_legal_document",
        "--kind",
        "TERMS",
        "--version",
        "v2",
        "--material",
        "--effective",
        later.isoformat(),
        *files,
    )

    effective = LegalDocument.objects.filter(version="v2")[0].effective_at
    local = timezone.localtime(effective, timezone.get_default_timezone())
    assert (local.date(), local.hour, local.minute) == (later, 0, 0)


def test_the_command_refuses_a_malformed_file_argument(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="LANG=PATH"):
        call_command("publish_legal_document", "--kind", "TERMS", "--version", "v1", "--file", "hr")


@override_settings(LEGAL_DRAFTS_ALLOWED=False)
def test_the_command_refuses_a_draft_where_drafts_arent_allowed(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="LEGAL_DRAFTS_ALLOWED"):
        call_command(
            "publish_legal_document",
            "--kind",
            "TERMS",
            "--version",
            "v1",
            "--draft",
            *write_texts(tmp_path),
        )


# --- the production guard ---------------------------------------------------------


@override_settings(LEGAL_DRAFTS_ALLOWED=False)
def test_the_guard_fails_while_a_draft_is_live() -> None:
    with override_settings(LEGAL_DRAFTS_ALLOWED=True):
        publish_version(LegalDocument.Kind.IMPRINT, "draft", is_draft=True)

    with pytest.raises(CommandError, match=r"IMPRINT draft \(hr\)"):
        call_command("check_legal_documents")


@override_settings(LEGAL_DRAFTS_ALLOWED=False)
def test_the_guard_passes_once_the_drafts_are_superseded() -> None:
    now = timezone.now()
    with override_settings(LEGAL_DRAFTS_ALLOWED=True):
        publish_version(version="draft", is_draft=True, now=now)
    publish_version(version="v1", effective_at=now + timedelta(seconds=1), now=now)

    with pytest.raises(CommandError):
        call_command("check_legal_documents")
    # Once v1 is in effect, the draft is history.
    assert live_drafts(at=now + DAY) == []


@override_settings(LEGAL_DRAFTS_ALLOWED=True)
def test_the_guard_allows_drafts_where_they_are_allowed() -> None:
    publish_version(is_draft=True)

    call_command("check_legal_documents")

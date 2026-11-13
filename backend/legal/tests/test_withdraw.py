"""Withdrawing a scheduled version before it takes effect."""

from datetime import datetime, timedelta

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from django.utils import timezone

from accounts.tests.factories import make_user
from legal.models import ImmutableRowError, LegalDocument, LegalDocumentWithdrawal, TermsAcceptance
from legal.services import (
    PublishRefused,
    WithdrawRefused,
    current_document,
    live_drafts,
    withdraw,
)
from legal.tests.factories import publish_version

pytestmark = pytest.mark.django_db

DAY = timedelta(days=1)


def scheduled() -> tuple[datetime, datetime]:
    """v1 in force, a material v2 scheduled 30 days out."""
    now = timezone.now()
    publish_version(version="v1", now=now)
    publish_version(version="v2", material_change=True, effective_at=now + 30 * DAY, now=now)
    return now, now + 30 * DAY


def test_a_withdrawn_version_never_takes_effect() -> None:
    now, effective = scheduled()

    withdraw("TERMS", "v2", "Wrong fee table", now=now)

    current = current_document("TERMS", "hr", at=effective + DAY)
    assert current is not None and current.version == "v1"
    # The version itself stays, unchanged, as evidence of what was scheduled.
    assert LegalDocument.objects.filter(version="v2").count() == 4


def test_a_corrected_version_can_replace_it_at_the_same_moment() -> None:
    now, effective = scheduled()
    withdraw("TERMS", "v2", "Wrong fee table", now=now)

    publish_version(version="v2b", material_change=True, effective_at=effective, now=now)

    current = current_document("TERMS", "de", at=effective)
    assert current is not None and current.version == "v2b"


def test_a_withdrawn_version_string_isnt_reused() -> None:
    now, effective = scheduled()
    withdraw("TERMS", "v2", "Wrong fee table", now=now)

    with pytest.raises(PublishRefused, match="already exists"):
        publish_version(version="v2", material_change=True, effective_at=effective, now=now)


def test_a_version_in_effect_cant_be_withdrawn() -> None:
    now, _ = scheduled()

    with pytest.raises(WithdrawRefused, match="already in effect"):
        withdraw("TERMS", "v1", "Too late", now=now)


def test_an_accepted_version_cant_be_withdrawn() -> None:
    now, _ = scheduled()
    document = LegalDocument.objects.get(version="v2", language="hr")
    TermsAcceptance.objects.create(user=make_user("ana"), document=document, platform="web")

    with pytest.raises(WithdrawRefused, match="has accepted"):
        withdraw("TERMS", "v2", "Wrong fee table", now=now)


@pytest.mark.parametrize(
    ("version", "reason", "message"),
    [("v9", "Typo", "doesn't exist"), ("v2", "  ", "Give a reason")],
)
def test_bad_withdrawals_are_refused(version: str, reason: str, message: str) -> None:
    now, _ = scheduled()

    with pytest.raises(WithdrawRefused, match=message):
        withdraw("TERMS", version, reason, now=now)


def test_a_version_is_withdrawn_once() -> None:
    now, _ = scheduled()
    withdraw("TERMS", "v2", "Wrong fee table", now=now)

    with pytest.raises(WithdrawRefused, match="already withdrawn"):
        withdraw("TERMS", "v2", "Again", now=now)


def test_a_withdrawn_version_no_longer_holds_back_a_material_change() -> None:
    now = timezone.now()
    publish_version(version="v1", now=now)
    publish_version(version="v2", material_change=True, effective_at=now + 40 * DAY, now=now)
    withdraw("TERMS", "v2", "Wrong fee table", now=now)

    # Before the withdrawal this was refused: it comes before the scheduled v2.
    publish_version(version="v3", material_change=True, effective_at=now + 35 * DAY, now=now)

    current = current_document("TERMS", "fr", at=now + 41 * DAY)
    assert current is not None and current.version == "v3"


@override_settings(LEGAL_DRAFTS_ALLOWED=True)
def test_a_withdrawn_draft_doesnt_fail_the_guard() -> None:
    now = timezone.now()
    publish_version(version="v1", now=now)
    publish_version(version="draft-2", is_draft=True, effective_at=now + DAY, now=now)

    withdraw("TERMS", "draft-2", "Placeholder", now=now)

    assert live_drafts(at=now) == []


def test_withdrawals_cant_be_changed_or_deleted() -> None:
    now, _ = scheduled()
    withdrawal = withdraw("TERMS", "v2", "Wrong fee table", now=now)
    withdrawal.reason = "Something else"

    with pytest.raises(ImmutableRowError):
        withdrawal.save()
    with pytest.raises(ImmutableRowError):
        withdrawal.delete()
    with pytest.raises(ImmutableRowError):
        LegalDocumentWithdrawal.objects.all().delete()
    with pytest.raises(ImmutableRowError):
        LegalDocumentWithdrawal.objects.update(reason="x")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE legal_legaldocumentwithdrawal SET reason = 'x'",
        "DELETE FROM legal_legaldocumentwithdrawal",
    ],
)
def test_the_database_refuses_to_change_a_withdrawal(statement: str) -> None:
    now, _ = scheduled()
    withdraw("TERMS", "v2", "Wrong fee table", now=now)

    with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
        connection.cursor().execute(statement)

    assert LegalDocumentWithdrawal.objects.get().reason == "Wrong fee table"


def test_the_command_withdraws_a_scheduled_version() -> None:
    scheduled()

    call_command(
        "withdraw_legal_document",
        "--kind",
        "TERMS",
        "--version",
        "v2",
        "--reason",
        "Wrong fee table",
    )

    assert LegalDocumentWithdrawal.objects.filter(kind="TERMS", version="v2").exists()


def test_the_command_refuses_a_version_in_effect() -> None:
    scheduled()

    with pytest.raises(CommandError, match="already in effect"):
        call_command(
            "withdraw_legal_document", "--kind", "TERMS", "--version", "v1", "--reason", "Too late"
        )

"""Legal records can't be rewritten: not through the ORM, and not in SQL either."""

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from accounts.tests.factories import make_user
from legal.models import ImmutableRowError, LegalDocument, TermsAcceptance
from legal.tests.factories import publish_version

pytestmark = pytest.mark.django_db


def test_a_published_document_cant_be_saved_again() -> None:
    document = publish_version()[0]
    document.body = "Something else"

    with pytest.raises(ImmutableRowError):
        document.save()


def test_a_published_document_cant_be_deleted() -> None:
    document = publish_version()[0]

    with pytest.raises(ImmutableRowError):
        document.delete()
    with pytest.raises(ImmutableRowError):
        LegalDocument.objects.filter(pk=document.pk).delete()


def test_documents_cant_be_bulk_updated() -> None:
    publish_version()

    with pytest.raises(ImmutableRowError):
        LegalDocument.objects.update(body="Something else")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE legal_legaldocument SET body = 'rewritten'",
        "UPDATE legal_legaldocument SET is_draft = NOT is_draft",
        "DELETE FROM legal_legaldocument",
    ],
)
def test_the_database_refuses_to_change_a_document(statement: str) -> None:
    publish_version()

    with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
        connection.cursor().execute(statement)

    assert LegalDocument.objects.filter(body__startswith="Terms").count() == 4


def accept(user: object) -> TermsAcceptance:
    document = publish_version()[0]
    return TermsAcceptance.objects.create(user=user, document=document, platform="ios")


def test_an_acceptance_keeps_the_account_reference() -> None:
    ana = make_user("ana")

    acceptance = accept(ana)

    assert acceptance.user_reference == ana.pk


def test_an_acceptance_cant_be_changed_or_deleted_through_the_model() -> None:
    acceptance = accept(make_user("ana"))
    acceptance.platform = "web"

    with pytest.raises(ImmutableRowError):
        acceptance.save()
    with pytest.raises(ImmutableRowError):
        acceptance.delete()


@pytest.mark.parametrize(
    "assignment",
    [
        "platform = 'web'",
        "accepted_at = accepted_at - interval '1 day'",
        "user_reference = user_reference + 1",
        "user_id = NULL, platform = 'web'",
    ],
)
def test_the_database_refuses_to_change_an_acceptance(assignment: str) -> None:
    acceptance = accept(make_user("ana"))

    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        connection.cursor().execute(f"UPDATE legal_termsacceptance SET {assignment}")  # noqa: S608

    acceptance.refresh_from_db()
    assert acceptance.platform == "ios"


def test_the_database_refuses_to_move_an_acceptance_to_another_user() -> None:
    acceptance = accept(make_user("ana"))
    luka = make_user("luka")

    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        connection.cursor().execute(
            "UPDATE legal_termsacceptance SET user_id = %s WHERE id = %s", [luka.pk, acceptance.pk]
        )


def test_deleting_the_account_detaches_its_acceptances_and_keeps_the_evidence() -> None:
    ana = make_user("ana")
    acceptance = accept(ana)
    ana_id = ana.pk

    ana.delete()

    acceptance.refresh_from_db()
    assert acceptance.user_id is None
    assert acceptance.user_reference == ana_id
    assert (acceptance.document.kind, acceptance.platform) == ("TERMS", "ios")


def test_acceptances_cant_be_bulk_deleted() -> None:
    ana = make_user("ana")
    accept(ana)

    with pytest.raises(ImmutableRowError):
        TermsAcceptance.objects.filter(user=ana).delete()
    with pytest.raises(ImmutableRowError):
        ana.terms_acceptances.all().delete()

    assert TermsAcceptance.objects.count() == 1


def test_acceptances_can_only_be_bulk_detached() -> None:
    ana = make_user("ana")
    acceptance = accept(ana)

    with pytest.raises(ImmutableRowError):
        TermsAcceptance.objects.update(platform="web")
    with pytest.raises(ImmutableRowError):
        TermsAcceptance.objects.update(user=make_user("luka"))
    TermsAcceptance.objects.filter(user=ana).update(user=None)

    acceptance.refresh_from_db()
    assert (acceptance.user_id, acceptance.user_reference) == (None, ana.pk)


def test_bulk_created_acceptances_keep_the_account_reference() -> None:
    ana = make_user("ana")
    document = publish_version()[0]

    TermsAcceptance.objects.bulk_create(
        [TermsAcceptance(user=ana, document=document, platform="web")]
    )

    assert TermsAcceptance.objects.get().user_reference == ana.pk


def test_bulk_create_takes_a_generator() -> None:
    users = [make_user("ana"), make_user("luka")]
    document = publish_version()[0]

    TermsAcceptance.objects.bulk_create(
        TermsAcceptance(user=user, document=document, platform="web") for user in users
    )

    assert sorted(a.user_reference for a in TermsAcceptance.objects.all()) == sorted(
        u.pk for u in users
    )


def test_purge_removes_only_detached_acceptances_past_the_cutoff() -> None:
    ana, luka = make_user("ana"), make_user("luka")
    ana_id = ana.pk
    document = publish_version()[0]
    old = timezone.now() - timedelta(days=365 * 12)
    for user in (ana, luka):
        TermsAcceptance.objects.create(
            user=user, document=document, platform="ios", accepted_at=old
        )
    TermsAcceptance.objects.create(user=ana, document=document, platform="web")
    ana.delete()

    purged = TermsAcceptance.objects.purge_detached(  # pyright: ignore[reportAttributeAccessIssue]
        accepted_before=timezone.now() - timedelta(days=365 * 11)
    )

    # Ana's old acceptance goes; Luka's (still his account) and Ana's recent one stay.
    assert purged == 1
    assert sorted((a.user_reference, a.platform) for a in TermsAcceptance.objects.all()) == sorted(
        [(luka.pk, "ios"), (ana_id, "web")]
    )

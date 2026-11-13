"""On-site contacts are a third party's personal data: they leave with the poster's account
and appear in the poster's data export (plan: "GDPR flows")."""

import pytest

from accounts.tests.factories import make_user
from care_contacts.gdpr import delete_care_contacts, export_care_contacts
from care_contacts.models import CareContact
from care_contacts.tests.factories import make_contact

pytestmark = pytest.mark.django_db


def test_deleting_the_account_deletes_its_contacts() -> None:
    ana, luka = make_user("ana"), make_user("luka")
    make_contact(ana)
    kept = make_contact(luka, name="Baka Mare")

    ana.delete()

    assert list(CareContact.objects.all()) == [kept]


def test_delete_care_contacts_removes_only_that_posters_contacts() -> None:
    ana, luka = make_user("ana"), make_user("luka")
    make_contact(ana)
    make_contact(ana, name="Ujak Ivo")
    kept = make_contact(luka, name="Baka Mare")

    assert delete_care_contacts(ana) == 2
    assert list(CareContact.objects.all()) == [kept]


def test_the_export_lists_the_posters_contacts_in_full() -> None:
    ana = make_user("ana")
    contact = make_contact(ana, no_smartphone=True)
    make_contact(make_user("luka"), name="Baka Mare")

    exported = export_care_contacts(ana)

    assert exported == [
        {
            "id": contact.pk,
            "name": "Mama Kata",
            "phone_number": "+385912345678",
            "sms_capable": True,
            "area": "Gruž, Dubrovnik",
            "language": "hr",
            "no_smartphone": True,
            "agreement_confirmed_at": contact.agreement_confirmed_at.isoformat(),
            "created_at": contact.created_at.isoformat(),
            "updated_at": contact.updated_at.isoformat(),
        }
    ]

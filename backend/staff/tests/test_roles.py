import importlib

import pytest
from django.contrib.auth.models import Group, Permission

from accounts.tests.factories import make_user
from staff.roles import GROUP_ONLY, ROLE_CAPABILITIES, Capability, Role, can
from staff.tests.factories import make_staff

roles_migration = importlib.import_module("staff.migrations.0002_staff_roles")

pytestmark = pytest.mark.django_db


def test_migration_grants_exactly_the_role_matrix() -> None:
    for role, capabilities in ROLE_CAPABILITIES.items():
        granted = set(
            Group.objects.get(name=role)
            .permissions.filter(content_type__app_label="staff")
            .values_list("codename", flat=True)
        )
        assert granted == {capability.value for capability in capabilities}
        frozen = {p.split(".")[1] for p in roles_migration.ROLES[role] if p.startswith("staff.")}
        assert frozen == granted


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (
            Role.SUPPORT,
            {Capability.EXACT_PLACE, Capability.CHAT, Capability.PHOTOS, Capability.STRIPE_IDS},
        ),
        (Role.FINANCE, {Capability.STRIPE_IDS, Capability.REVEAL_TAX_IDS}),
        (
            Role.TRUST,
            {
                Capability.EXACT_PLACE,
                Capability.CHAT,
                Capability.PHOTOS,
                Capability.CONTACT_PHONE,
                Capability.RESTRICTED_NOTES,
            },
        ),
    ],
)
def test_each_role_has_only_its_capabilities(role: Role, allowed: set[Capability]) -> None:
    user = make_staff(role)
    assert {capability for capability in Capability if can(user, capability)} == allowed


def test_restricted_notes_are_trust_only() -> None:
    assert not can(make_staff(Role.SUPPORT), Capability.RESTRICTED_NOTES)
    assert not can(make_staff(Role.FINANCE), Capability.RESTRICTED_NOTES)
    assert can(make_staff(Role.TRUST), Capability.RESTRICTED_NOTES)


def test_superuser_needs_the_group_for_group_only_capabilities() -> None:
    superuser = make_staff(is_superuser=True)
    assert can(superuser, Capability.EXACT_PLACE)
    for capability in GROUP_ONLY:
        assert not can(superuser, capability)
    superuser.groups.add(Group.objects.get(name=Role.TRUST))
    assert can(superuser, Capability.RESTRICTED_NOTES)
    assert can(superuser, Capability.CONTACT_PHONE)
    assert not can(superuser, Capability.REVEAL_TAX_IDS)


def test_a_permission_given_to_the_user_directly_does_not_open_restricted_notes() -> None:
    user = make_staff(Role.SUPPORT)
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="staff", codename="restricted_notes")
    )
    assert not can(user, Capability.RESTRICTED_NOTES)


def test_non_staff_and_inactive_users_have_nothing() -> None:
    member = make_user("member")
    member.groups.add(Group.objects.get(name=Role.TRUST))
    inactive = make_staff(Role.TRUST, is_active=False)
    for capability in Capability:
        assert not can(member, capability)
        assert not can(inactive, capability)

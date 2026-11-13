"""Support and finance may correct a trader's registered business address in the helper
profile admin (manager decision 2026-11-04, BEAV-110): the BUSINESS_ADDRESS_REQUIRED
error sends helpers to support. `helpers.admin.HelperProfileAdmin` keeps every other field
read-only, and the change is audited like any admin change. Trust gets nothing here."""

from django.db import migrations

GRANTS = {
    "support": ["view_helperprofile", "change_helperprofile"],
    "finance": ["change_helperprofile"],
}


def _each(apps):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    for name, codenames in GRANTS.items():
        group = Group.objects.filter(name=name).first()
        if group is None:
            continue
        for codename in codenames:
            perm = Permission.objects.filter(
                content_type__app_label="helpers", codename=codename
            ).first()
            if perm is not None:
                yield group, perm


def grant(apps, schema_editor):
    for group, perm in _each(apps):
        group.permissions.add(perm)


def revoke(apps, schema_editor):
    for group, perm in _each(apps):
        group.permissions.remove(perm)


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0003_finance_without_user_admin"),
        ("helpers", "0002_business_address"),
    ]

    operations = [migrations.RunPython(grant, revoke)]

"""Support and trust work the helper review queue: two helper-fault cancellations within
30 days open a `bookings.HelperReviewItem` for a person to look at (BEAV-123; PLAN.md,
"Other edge cases"). They view the items and mark them reviewed; nothing else. Finance,
which works from IDs, gets nothing here."""

from django.contrib.auth.management import create_permissions
from django.db import migrations

GRANTS = {
    "support": ["view_helperreviewitem", "change_helperreviewitem"],
    "trust": ["view_helperreviewitem", "change_helperreviewitem"],
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
                content_type__app_label="bookings", codename=codename
            ).first()
            if perm is not None:
                yield group, perm


def grant(apps, schema_editor):
    # Permissions are normally created after all migrations (post_migrate).
    app_config = apps.get_app_config("bookings")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None
    for group, perm in _each(apps):
        group.permissions.add(perm)


def revoke(apps, schema_editor):
    for group, perm in _each(apps):
        group.permissions.remove(perm)


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0004_support_edits_business_address"),
        ("bookings", "0008_helper_and_no_fault_cancellation"),
    ]

    operations = [migrations.RunPython(grant, revoke)]

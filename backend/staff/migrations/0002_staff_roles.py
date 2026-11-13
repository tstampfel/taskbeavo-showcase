"""The staff role groups and their permissions (staff.roles; user decision 2026-11-04).

Frozen copy of `staff.roles.ROLE_CAPABILITIES` plus the admin model permissions each role
needs to open its pages; `staff/tests/test_roles.py` checks the two agree. Staff are added
to the groups by hand.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations

ROLES = {
    "support": [
        "staff.view_exact_place",
        "staff.view_chat",
        "staff.view_photos",
        "staff.view_stripe_ids",
        "bookings.view_booking",
        "accounts.view_user",
        "tasks.view_task",
    ],
    "finance": [
        "staff.view_stripe_ids",
        "staff.reveal_tax_ids",
        "bookings.view_booking",
        "accounts.view_user",
        "helpers.view_helperprofile",
    ],
    "trust": [
        "staff.view_exact_place",
        "staff.view_chat",
        "staff.view_photos",
        "staff.view_contact_phone",
        "staff.restricted_notes",
        "bookings.view_booking",
        "accounts.view_user",
        "tasks.view_task",
    ],
}


def create_roles(apps, schema_editor):
    # Permissions are normally created after all migrations (post_migrate); create them
    # now so the groups can get them.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    for name, perms in ROLES.items():
        group, _ = Group.objects.get_or_create(name=name)
        for perm in perms:
            app_label, codename = perm.split(".")
            group.permissions.add(
                Permission.objects.get(content_type__app_label=app_label, codename=codename)
            )


def remove_roles(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("accounts", "0005_session_device_and_location"),
        ("bookings", "0002_checkout"),
        ("helpers", "0001_initial"),
        ("tasks", "0001_initial"),
    ]

    operations = [migrations.RunPython(create_roles, remove_roles)]

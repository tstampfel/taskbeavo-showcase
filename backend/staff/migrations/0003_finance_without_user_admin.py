"""Finance loses the user admin (user decision 2026-11-04, review BEAV-156 R1-1): names
and emails are for support and trust, who need them for cases. A new migration rather
than an edit of 0002, which may already be applied."""

from django.db import migrations


def _finance_view_user(apps):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    group = Group.objects.filter(name="finance").first()
    perm = Permission.objects.filter(
        content_type__app_label="accounts", codename="view_user"
    ).first()
    return group, perm


def remove(apps, schema_editor):
    group, perm = _finance_view_user(apps)
    if group and perm:
        group.permissions.remove(perm)


def restore(apps, schema_editor):
    group, perm = _finance_view_user(apps)
    if group and perm:
        group.permissions.add(perm)


class Migration(migrations.Migration):
    dependencies = [("staff", "0002_staff_roles")]

    operations = [migrations.RunPython(remove, restore)]

from django.contrib.admin.apps import AdminConfig


class StaffAdminConfig(AdminConfig):
    """`django.contrib.admin` with TaskBeavo's audited site as the default site, so every
    `@admin.register`, Django's own included, goes through `staff.sites.StaffAdminSite`.
    Kept out of staff/apps.py so `staff` has a single app config."""

    default_site = "staff.sites.StaffAdminSite"

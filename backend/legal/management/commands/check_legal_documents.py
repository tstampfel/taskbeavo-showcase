"""Fail when a draft legal document is current or scheduled and drafts aren't allowed.

Runs in the Render pre-deploy step after `migrate` (scripts/predeploy.sh), so a release
whose database still shows placeholder legal text never takes traffic in production
(plan: no real users before the lawyer-approved version is live).
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from legal.services import live_drafts


class Command(BaseCommand):
    help = "Fail if a current or scheduled legal document is a draft where drafts aren't allowed."

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.LEGAL_DRAFTS_ALLOWED:
            self.stdout.write("Legal drafts are allowed here; nothing to check.")
            return
        drafts = live_drafts()
        if drafts:
            listed = ", ".join(f"{d.kind} {d.version} ({d.language})" for d in drafts)
            raise CommandError(
                f"Draft legal documents are current or scheduled: {listed}. Publish the "
                "lawyer-approved versions before deploying."
            )
        self.stdout.write("No draft legal documents are current or scheduled.")

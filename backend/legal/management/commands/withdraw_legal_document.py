"""Withdraw a scheduled legal document version before it takes effect.

    manage.py withdraw_legal_document --kind TERMS --version 2027-03 --reason "Wrong fee table"

The version stays as published, in every language; a withdrawal record is added and the
version never takes effect. Refused once the version is in effect or if anyone accepted
it: then publish a new version instead. legal/services.py has the rules.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from legal.models import LegalDocument
from legal.services import WithdrawRefused, withdraw


class Command(BaseCommand):
    help = "Withdraw a scheduled legal document version before it takes effect."

    def create_parser(self, prog_name: str, subcommand: str, **kwargs: Any) -> CommandParser:
        # --version names the document version here, replacing Django's own --version.
        return super().create_parser(prog_name, subcommand, conflict_handler="resolve", **kwargs)

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--kind", required=True, choices=LegalDocument.Kind.values)
        parser.add_argument("--version", required=True, dest="doc_version")
        parser.add_argument("--reason", required=True, help="Why; kept with the record.")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            withdrawal = withdraw(options["kind"], options["doc_version"], options["reason"])
        except WithdrawRefused as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            f"Withdrew {withdrawal.kind} {withdrawal.version}; it will not take effect."
        )

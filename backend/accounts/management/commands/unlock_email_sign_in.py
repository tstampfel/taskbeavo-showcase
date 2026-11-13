"""Lift the 30-minute sign-in lockout of one email address (accounts/signin.py).

For support, once they have checked the person asking owns the address: someone else may
be locking it on purpose by typing wrong codes.

    uv run python manage.py unlock_email_sign_in ana@example.com
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from accounts.signin import unlock_address
from core.errors import ApiError


class Command(BaseCommand):
    help = "Lift the sign-in lockout of an email address and clear its wrong-code count."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("email")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            was_locked = unlock_address(options["email"])
        except ApiError as exc:
            raise CommandError(str(exc)) from None
        # The address isn't echoed back: command output can end up in logs.
        self.stdout.write("Unlocked." if was_locked else "The address wasn't locked.")

"""Staff confirm a helper company's name and OIB after checking them in the court
register (sudski registar), giving exactly what they checked:

    uv run python manage.py verify_helper_business --user-id 42 \\
        --business-oib 00000000948 --business-name "Beavo d.o.o."

Nothing is verified if the profile's company name or OIB differ from those, e.g. because
the helper changed them after the check. Until verified, the company's profile can't send
offers and its name and OIB aren't public. Prints the company name and OIB it verified
(public register data), never the helper's personal details.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from helpers.models import BusinessVerification, HelperProfile
from helpers.services import verify_business


class Command(BaseCommand):
    help = "Mark a helper company's checked name and OIB as verified by staff."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--business-oib", required=True, help="The OIB you checked.")
        parser.add_argument("--business-name", required=True, help="The name you checked.")

    def handle(self, *args: Any, **options: Any) -> None:
        profile_id = (
            HelperProfile.objects.filter(user_id=options["user_id"])
            .values_list("pk", flat=True)
            .first()
        )
        if profile_id is None:
            raise CommandError(f"User {options['user_id']} has no helper profile.")
        try:
            profile = verify_business(
                profile_id,
                business_oib=options["business_oib"],
                business_name=options["business_name"],
                by=BusinessVerification.STAFF,
            )
        except ValueError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            f"HelperProfile {profile.pk}: verified {profile.business_name} "
            f"(OIB {profile.business_oib}) by staff"
        )

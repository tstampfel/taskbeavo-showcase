from django.conf import settings
from django.core.management.base import BaseCommand

from accounts import geolite


class Command(BaseCommand):
    help = (
        "Download the MaxMind GeoLite2 City database into GEOIP_CITY_DATABASE when it is due, "
        "and delete a copy older than 30 days (the web process does this by itself, in the "
        "background; accounts/geolite.py)."
    )

    def handle(self, *args: object, **options: object) -> None:
        outcome = geolite.update_database(
            settings.GEOIP_CITY_DATABASE,
            settings.MAXMIND_ACCOUNT_ID,
            settings.MAXMIND_LICENSE_KEY,
        )
        self.stdout.write(outcome)
        if outcome == "failed":
            raise SystemExit(1)

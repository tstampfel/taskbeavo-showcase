"""Seed fake Dubrovnik demo data for the public showcase (bookings/demo_seed.py):

    DEMO_PAYMENTS=1 manage.py seed_demo

Idempotent: a second run creates nothing new. The seeded times are relative to the
seed, so run `seed_demo --reset` whenever the demo feels stale: it retires the demo users
(anonymised, never deleted; bookings and consent records are legal evidence) and seeds a
fresh set relative to now. Refused in production, next to a live Stripe key, and without
DEMO_PAYMENTS, since the seeded bookings are paid through the demo Stripe stand-in.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from bookings.demo_seed import (
    HELPERS,
    POSTERS,
    SCENARIOS,
    DemoSeedRefused,
    Seeder,
    refuse_outside_demo,
    retire_demo_users,
)


class Command(BaseCommand):
    help = "Seed fake Dubrovnik demo users, tasks, offers and bookings (demo payments only)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Retire the current demo users and seed a fresh demo relative to now.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            refuse_outside_demo()
        except DemoSeedRefused as refused:
            raise CommandError(str(refused)) from refused
        if options["reset"]:
            for email in retire_demo_users():
                self.stdout.write(f"retired  {email}")
        report = Seeder().run()
        for line in report.created:
            self.stdout.write(f"created  {line}")
        for key in report.skipped:
            self.stdout.write(f"exists   {key}")
        self.stdout.write("\nSign in with any of these emails; the one-time code is printed")
        self.stdout.write("in the Django log (console email backend):\n")
        for demo in [*POSTERS, *HELPERS]:
            self.stdout.write(f"  {demo.email:<28} {demo.role:<7} {demo.note}")
        self.stdout.write("\nSeeded tasks and bookings:\n")
        for scenario in SCENARIOS:
            booking = report.bookings.get(scenario.key)
            task = report.tasks.get(scenario.key)
            if booking is not None:
                where = f"/bookings/{booking.pk}  ({booking.status})"
            elif task is not None:
                where = f"/tasks/{task.pk}/offers  (open, offers to accept)"
            else:
                continue
            self.stdout.write(f"  {scenario.key:<20} {where}")

"""Publish a new version of a legal document in all four languages.

    manage.py publish_legal_document --kind TERMS --version 2027-03 \\
        --file hr=terms-hr.md --file en=terms-en.md --file de=terms-de.md --file fr=terms-fr.md \\
        --material --effective 2027-04-15

The texts are the lawyer-approved, professionally translated files (never machine
translations); this command only stores them. Publishing without --draft records that
they are; placeholder text must be published with --draft. Without --effective the
version takes effect at once. A date means midnight in Croatia. legal/services.py has
the rules it enforces.

A CANCELLATION text marks the two checkout tick-box sentences once each, for private and
for trader helpers, in every language, and checkout shows exactly those words for the
offer's helper type (legal/checkout_consent.py):

    <!-- checkout:cancellation_policy_private -->
    I accept …
    <!-- /checkout:cancellation_policy_private -->
    <!-- checkout:early_start_private -->I ask …<!-- /checkout:early_start_private -->

and the same two with `_trader`.
A version without them is refused, drafts too.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from legal.models import LegalDocument
from legal.services import NewVersion, PublishRefused, publish


def _effective_at(value: str | None, now: datetime) -> datetime:
    if value is None:
        return now
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise CommandError(f"--effective {value} isn't an ISO date or date-time.") from None
    # A date or a time without an offset is read in Croatia (TIME_ZONE).
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


class Command(BaseCommand):
    help = "Publish a legal document version (all four languages at once)."

    def create_parser(self, prog_name: str, subcommand: str, **kwargs: Any) -> CommandParser:
        # --version names the document version here, replacing Django's own --version.
        return super().create_parser(prog_name, subcommand, conflict_handler="resolve", **kwargs)

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--kind", required=True, choices=LegalDocument.Kind.values)
        parser.add_argument("--version", required=True, dest="doc_version")
        parser.add_argument(
            "--file",
            action="append",
            default=[],
            metavar="LANG=PATH",
            help=(
                "The text in one language; give one per language (hr, en, de, fr). A "
                "CANCELLATION text marks its checkout tick-box sentences (see above)."
            ),
        )
        parser.add_argument("--effective", help="ISO date (midnight in Croatia) or date-time.")
        parser.add_argument(
            "--material",
            action="store_true",
            help="A material change: needs 30 days' notice and re-acceptance.",
        )
        parser.add_argument(
            "--draft", action="store_true", help="Placeholder text, not lawyer-approved."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        bodies: dict[str, str] = {}
        for item in options["file"]:
            language, sep, path = item.partition("=")
            if not sep or not path:
                raise CommandError(f"--file {item} must be LANG=PATH.")
            if language in bodies:
                raise CommandError(f"--file {language} is given twice.")
            try:
                bodies[language] = Path(path).read_text(encoding="utf-8")
            except OSError as exc:
                raise CommandError(f"Can't read {path}: {exc.strerror}.") from None

        now = timezone.now()
        new = NewVersion(
            kind=options["kind"],
            version=options["doc_version"],
            bodies=bodies,
            effective_at=_effective_at(options["effective"], now),
            material_change=options["material"],
            is_draft=options["draft"],
        )
        try:
            documents = publish(new, now=now)
        except PublishRefused as exc:
            raise CommandError(str(exc)) from None
        flags = [
            name for name, on in (("material", new.material_change), ("draft", new.is_draft)) if on
        ]
        self.stdout.write(
            f"Published {new.kind} {new.version} in {', '.join(d.language for d in documents)}, "
            f"effective {new.effective_at.isoformat()}"
            + (f" ({', '.join(flags)})" if flags else "")
            + "."
        )

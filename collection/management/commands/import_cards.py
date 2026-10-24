"""python manage.py import_cards my-cards.txt [--check]

Adds every card in a text file to your library. The file uses the same format
as the Import page, one card per line:

    4 Lightning Bolt (2XM) 117
    1 Sol Ring (CMM) 410 *F*

With --check, the list is matched against Scryfall but nothing is added.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from collection import importer, scryfall, services


class Command(BaseCommand):
    help = "Import a card list (one card per line) into your library."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Text file with one card per line")
        parser.add_argument("--check", action="store_true", help="Match the list but don't add anything")

    def handle(self, path, check=False, **options):
        file = Path(path)
        if not file.exists():
            raise CommandError(f"No file at {file}.")
        try:
            result = importer.resolve(file.read_text(encoding="utf-8"), scryfall.lookup)
        except scryfall.ScryfallError as exc:
            raise CommandError(str(exc)) from exc

        for m in result.matches:
            note = f"  ({m.note})" if m.note else ""
            self.stdout.write(f"  {m.line.quantity} x {m.card['name']} [{m.card['set_code'].upper()} #{m.card['collector_number']}, {m.finish}]{note}")
        for p in result.problems:
            self.stdout.write(self.style.WARNING(f"Line {p.number}: {p.raw.strip()}\n    {p.message}"))

        if check or not result.matches:
            self.stdout.write(f"{result.copies} cards matched, {len(result.problems)} lines to fix. Nothing was added.")
            return
        summary = services.import_items(result.items())
        self.stdout.write(self.style.SUCCESS(
            f"Added {summary['copies']} cards ({summary['cards']} printings). "
            f"{len(result.problems)} lines skipped."
        ))

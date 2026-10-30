"""python manage.py load_rules [--file PATH] [--url URL]

Loads the Magic Comprehensive Rules into the app so the Rules page can search
them. With no options it finds the newest rules file on magic.wizards.com/en/rules.
Run it again whenever Wizards publishes new rules (a few times a year).
"""
from django.core.management.base import BaseCommand, CommandError

from collection import rules


class Command(BaseCommand):
    help = "Download (or read) the Comprehensive Rules and load them for searching."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="A rules .txt file you've already downloaded")
        parser.add_argument("--url", help="A specific rules .txt URL")

    def handle(self, file=None, url=None, **options):
        try:
            edition = rules.load_file(file) if file else rules.load_latest(url)
        except (rules.RulesError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Loaded the Comprehensive Rules effective {edition.effective}: "
            f"{edition.rule_count} rules and {edition.term_count} glossary terms."
        ))

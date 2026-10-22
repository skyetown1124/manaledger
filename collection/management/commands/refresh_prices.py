"""python manage.py refresh_prices

Pulls current Scryfall prices for every card you own and saves today's
collection value. Run it once a day (Task Scheduler on Windows, cron on
Mac/Linux) to build up your value history automatically.
"""
from django.core.management.base import BaseCommand, CommandError

from collection import scryfall, services


class Command(BaseCommand):
    help = "Refresh Scryfall prices for every card in your collection."

    def handle(self, *args, **options):
        try:
            result = services.refresh_prices()
        except scryfall.ScryfallError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Updated {result['updated']} cards. Collection value: ${result['total']:,.2f}"
        ))
        for missing in result["missing"]:
            self.stdout.write(self.style.WARNING(f"Not found on Scryfall: {missing}"))

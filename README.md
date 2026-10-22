# Mana Ledger

A Django app that tracks what your Magic: The Gathering collection is worth.
You add the exact printings you own, it pulls current prices from
[Scryfall](https://scryfall.com/docs/api) (free, no API key), and the
dashboard shows your total value, your most valuable cards, and how the
value changes over time.

## Run it

You need Python 3.10 or newer.

```bash
cd manaledger
python -m venv .venv
# Windows:      .venv\Scripts\activate
# Mac / Linux:  source .venv/bin/activate
pip install -r requirements.txt

python manage.py makemigrations collection
python manage.py migrate
python manage.py createsuperuser      # optional, for /admin
python manage.py runserver
```

Open http://127.0.0.1:8000/ and use **Add Cards** to search for your first card.

## Using it

- **Add Cards**: type a card name (suggestions come from Scryfall as you type),
  optionally a set code like `cmm`, and every printing is listed with its
  prices. Pick the one you own, choose the finish, and add it. "Paid $" is
  optional and lets the app show your gain or loss.
- **My Cards**: your whole collection. Change quantities or what you paid,
  sort by value, name, set or date added. A quantity of 0 removes the card.
- **Dashboard**: total value, value by day with a trend line, value by set,
  color and rarity, top cards, biggest price moves since your last update, and
  a color × card type heat map.
- **Refresh prices** (top right) pulls fresh prices for everything you own
  and saves today's collection value. Each new day you refresh adds a bar to
  the "value by day" chart, so the history grows the more often you refresh.
- **Light mode** switches themes and remembers your choice in this browser.

## Refresh prices automatically

Scryfall updates prices about once a day, so a daily refresh is plenty:

```bash
python manage.py refresh_prices
```

- Mac / Linux (cron, every day at 9:15 AM):
  `15 9 * * * cd /path/to/manaledger && .venv/bin/python manage.py refresh_prices`
- Windows: create a daily task in Task Scheduler that runs
  `C:\path\to\manaledger\.venv\Scripts\python.exe manage.py refresh_prices`
  with "Start in" set to the `manaledger` folder.

## How the code is organized

| File | What it does |
| --- | --- |
| `collection/models.py` | `Card` (one printing), `CollectionItem` (copies you own, per finish), `PriceSnapshot` (a card's price on one day), `CollectionSnapshot` (your total on one day) |
| `collection/scryfall.py` | Scryfall client: search, autocomplete, batch lookups of 75 cards per request, polite rate limiting and headers |
| `collection/services.py` | Saving cards, adding to the collection, refreshing prices, recording snapshots |
| `collection/stats.py` | All dashboard math, in plain Python (no database calls), so it's easy to test |
| `collection/views.py` | Dashboard, My Cards, Add Cards, refresh, and the autocomplete endpoint |
| `collection/templates/collection/` | `base.html` (header, tabs, footer) and one template per page |
| `collection/static/collection/` | `style.css` (dark and light themes), `app.js` (theme toggle, name suggestions), `dashboard.js` (charts) |
| `collection/management/commands/refresh_prices.py` | The `refresh_prices` command |

Charts use Chart.js 4.4.1 and chartjs-plugin-datalabels 2.2.0, bundled in
`static/collection/vendor/` so the app works without a CDN (both MIT licensed).

## Notes

- Prices are Scryfall's USD figures, which come from TCGplayer market
  prices. Some cards (very old, or promo-only) have no USD price and count as
  $0 until one exists.
- Scryfall asks clients to identify themselves and wait between requests;
  `scryfall.py` does both. Set your own `SCRYFALL_USER_AGENT` in `settings.py`.
- `SECRET_KEY` and `DEBUG = True` in `settings.py` are for local use only.

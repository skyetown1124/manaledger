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
- **My Cards**: your whole collection. Search it by name, type or rules text
  (see "Searching your cards" below), change quantities or what you paid, and
  sort by value, name, mana value, set or date added. A quantity of 0 removes
  the card. Open "Card text" under a card to read it; search terms are
  highlighted.
- **Decks**: create a deck, pick a format, then search your library on the
  right side of the deck page and add cards. A deck can only use as many
  copies of a card as you own (basic lands are unlimited and don't need to be
  in your library). The deck page shows:
  - a checklist for the format: deck size, copy limit (4, or 1 in Commander),
    sideboard size, commander choice, commander color identity, banned cards,
    and whether you own every card,
  - the deck's value, average mana value, mana curve and colored mana symbols,
  - a plain-text deck list you can copy into MTG Arena, Moxfield or Archidekt.

  For Commander decks, add your commander first. The library search then shows
  only cards that fit your commander's colors (untick the box to see all).
- **Dashboard**: total value, value by day with a trend line, value by set,
  color and rarity, top cards, biggest price moves since your last update, and
  a color × card type heat map.
- **Refresh prices** (top right) pulls fresh prices for everything you own
  and saves today's collection value. Each new day you refresh adds a bar to
  the "value by day" chart, so the history grows the more often you refresh.
- **Light mode** switches themes and remembers your choice in this browser.

## Searching your cards

Plain words match a card's name, type line or rules text. Add filters with
spaces between them, use "quotes" for phrases, and put `-` in front of a
filter to exclude it. The same search works on My Cards and in the deck builder.

| Filter | Finds | Example |
| --- | --- | --- |
| `o:` | rules text contains | `o:"draw a card"` |
| `t:` | type line contains | `t:legendary t:creature` |
| `k:` | keyword ability | `k:flying` |
| `c:` `c=` `c<=` | colors: includes / exactly / only these (`c:c` colorless, `c:m` multicolor) | `c:ur`, `c=izzet` |
| `id:` | fits in a Commander deck of these colors | `id:wub` |
| `mv` | mana value with `= < <= > >=` | `mv<=2` |
| `r:` | rarity (c, u, r, m) | `r:m` |
| `s:` | set code | `s:mh2` |
| `f:` | legal in a format | `f:modern` |
| `is:commander` | can be your commander | `is:commander c:b` |
| `name:` | name contains | `name:dragon` |

Example: `t:instant c:u mv<=2 -o:counter` finds cheap blue instants that
aren't counterspells.

Card text, mana costs and format legality are saved when you add a card. If
you added cards before this feature existed, press **Refresh prices** once to
load their text.

## Run the tests

```bash
python manage.py test collection
```

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
| `collection/models.py` | `Card` (one printing, with its rules text), `CollectionItem` (copies you own, per finish), `Deck` and `DeckCard` (a deck and the cards in it), `PriceSnapshot` (a card's price on one day), `CollectionSnapshot` (your total on one day) |
| `collection/scryfall.py` | Scryfall client: search, autocomplete, batch lookups of 75 cards per request, polite rate limiting and headers |
| `collection/services.py` | Saving cards, adding to the collection, refreshing prices, recording snapshots |
| `collection/search.py` | Turns a search like `t:creature mv<=3` into a database filter, plus a plain-English summary |
| `collection/decks.py` | Deck rules and stats: format checks, mana curve, colors, export text (plain Python, no database calls) |
| `collection/stats.py` | All dashboard math, in plain Python (no database calls), so it's easy to test |
| `collection/views.py` | Dashboard, My Cards, Add Cards, Decks, refresh, and the autocomplete endpoint |
| `collection/tests.py` | Tests for search, deck rules, Scryfall parsing and the deck pages |
| `collection/templates/collection/` | `base.html` (header, tabs, footer) and one template per page |
| `collection/templatetags/` | `money` (dollar formatting) and `cards` (mana symbols, color pips, highlighting) |
| `collection/static/collection/` | `style.css` (dark and light themes), `app.js` (theme toggle, name suggestions), `dashboard.js` and `deck.js` (charts) |
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

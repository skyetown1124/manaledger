# Mana Ledger

A Django app that tracks what your Magic: The Gathering collection is worth.
You add the exact printings you own, it pulls current prices from
[Scryfall](https://scryfall.com/docs/api) (free, no API key), and the
dashboard shows your total value, your most valuable cards, and how the
value changes over time.

## Run it in your browser (GitHub Codespaces)

No installs needed:

1. On the repository page, click the green **Code** button, open the
   **Codespaces** tab, and click **Create codespace on main**.
2. Wait a minute or two while it installs Django and sets up the database.
3. The app starts on its own and opens in a new tab. If it doesn't, open the
   **Ports** tab at the bottom and click the globe icon next to port 8000.

Your collection is saved inside that codespace. Stop it when you're done
(github.com/codespaces → … → Stop) and reopen it later to pick up where you
left off. If you reopen it and the app isn't running, type
`python manage.py runserver 0.0.0.0:8000` in the terminal.

## Run it on your computer

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

  - a composition breakdown: how many creatures, instants, sorceries,
    artifacts, enchantments, planeswalkers, battles and lands, each as a share
    of the deck with its average CMC, plus a grid of spells by type and CMC.

  The deck builder enforces each format's rules as you add cards, and the
  checklist shows anything that still needs fixing (for example after you
  change a deck's format):

  | Rule | Commander | Other formats |
  | --- | --- | --- |
  | Copies of a card (all printings, main deck and sideboard together) | 1 | 4; 1 for cards restricted in Vintage |
  | Basic lands and "any number of cards named" cards | unlimited | unlimited |
  | "Up to seven/nine cards named" cards (Seven Dwarves, Nazgûl) | that number | that number |
  | Banned or not legal in the format | refused | refused |
  | Deck size | exactly 100 with commander(s) | at least 60, sideboard up to 15 |
  | Commander | a legendary creature, or a card that says it can be your commander | |
  | Two commanders | both have Partner, Partner with each other, the same Partner—group, Friends forever, Choose a Background + a Background, or Doctor's companion + a Time Lord Doctor | |
  | Color identity | every card, basic lands included, fits within the commander(s) | |

  Banned, restricted and legal status comes from Scryfall and updates when you
  press **Refresh prices**.

- **Commander bracket** (Commander decks): the deck page shows which bracket
  (1–5) the deck actually plays at and why. It applies the official rules
  (Game Changers, mass land denial, extra turns, two-card combos) and adds an
  **impact score**, like a salt score for power: about 175 strong cards that
  aren't Game Changers (fast mana, efficient tutors, free interaction, runaway
  engines, stax, former Game Changers) add 1–5 points each, and a high total
  pushes the deck up a bracket even when no rule does. Set the bracket you mean
  the deck for, and it's flagged if it plays higher. **Check combos with
  Commander Spellbook** looks up every combo in the deck online. The research
  and sources are in [docs/BRACKETS.md](docs/BRACKETS.md).
- **Playtest** (button on each deck page): goldfish the deck like solitaire.
  - Shuffle and draw 7, then keep or mulligan. Mulligans follow the London
    rule (draw 7, then put one card on the bottom per mulligan), and the first
    mulligan in Commander is free. Choose whether you're on the play (no draw
    on turn 1) or on the draw.
  - Play turns: **Next turn** untaps and draws. Double-click a card in hand to
    play it (lands and permanents go to the battlefield, instants and sorceries
    to the graveyard), and double-click a permanent to tap or untap it. Click
    any card for more options: discard, exile, return to hand, top or bottom of
    library, and so on.
  - Library tools: draw, mill, shuffle, **Look at top…** (scry-style: keep on
    top in any order, bottom, graveyard or hand), and **Search** (tutor; the
    library shuffles afterwards). Graveyard and exile can be browsed and cards
    moved back.
  - Commander zone with commander tax, a life counter (40 in Commander, 20
    otherwise), tokens, a count of untapped lands, and a game log.
  - Keyboard: `D` draw, `N` next turn, `U` untap all, `L` look at the top.
  - **Opening hand odds**: exact chances of drawing 0–7 lands in your opening
    hand, of hitting each land drop by turn 6, and of drawing any chosen card by
    a given turn, on the play or on the draw.

  For Commander decks, add your commander first. The library search then shows
  only cards that fit your commander's colors (untick the box to see all).
- **Dashboard**: total value, value by day with a trend line, value by set,
  color and rarity, top cards, biggest price moves since your last update, and
  a color × card type heat map.
- **Refresh prices** (top right) pulls fresh prices for everything you own
  and saves today's collection value. Each new day you refresh adds a bar to
  the "value by day" chart, so the history grows the more often you refresh.
- **Light mode** switches themes and remembers your choice in this browser.

## Rules and rulings

- **Card pages:** click any card name in My Cards or a deck to open its page:
  image, text, legality in each format, how many you own, which decks use it,
  the Comprehensive Rules for its keywords (Deathtouch → 702.2), and Wizards'
  **official rulings** (from Scryfall). Rulings are saved after the first look
  and rechecked monthly, or when you press **Check for new rulings**. In
  Playtest, a card's menu links to this page.
- **Rules tab:** the full Magic Comprehensive Rules, searchable. Type words
  (every word must appear; use "quotes" for phrases) to see matching rules and
  glossary entries, or a rule number like `702.19` to jump to it. Rule numbers
  inside rules, rulings and the glossary are links. There's also a table of
  contents and an A–Z glossary.
- **Loading the rules:** new codespaces load them during setup. Otherwise press
  **Download the latest rules** on the Rules tab, or run
  `python manage.py load_rules`. It finds the newest file on
  magic.wizards.com/en/rules; run it again when Wizards updates the rules (a
  few times a year), or use **Check for newer rules**. Offline? Download the
  .txt yourself and run `python manage.py load_rules --file MagicCompRules.txt`.

## Importing a card list

The **Import** tab adds many cards at once. Paste one card per line and press
**Check list**: every line is looked up on Scryfall and you see what matched,
with prices, plus any lines that need fixing. Nothing is added until you press
**Add to my library**.

```
4 Lightning Bolt (2XM) 117      exact printing: set code and collector number
4x Lightning Bolt (2XM)         a printing from that set
Lightning Bolt                  1 copy of Scryfall's default printing
1 Sol Ring (CMM) 410 *F*        foil (*E* or "etched" for etched foil)
```

This is the format MTG Arena, Moxfield and Archidekt export, so lists from
those work as they are. On cards printed since about 2020, the set code and
collector number are in the bottom-left corner (for example `DMU · 107`). If a
line's number belongs to a different card than the name says, the check flags
it instead of guessing.

### Reading cards from photos

At the top of the Import page, **Read photos** takes up to 20 photos of
cards and fills the list for you, using the free Tesseract OCR program (no
account or API key). Several cards per photo is fine.

- Cards printed since about 2015 are matched to their exact printing from the
  small set code and collector number in the bottom-left corner. That print is
  tiny, so the app cross-checks it against the card name in the photo.
- Older cards without that corner info are found by name, with Scryfall's
  default printing. Change the set in the list if yours is different.
- Foils can't be told apart in photos; add `*F*` to those lines.
- Everything read lands in the list, and nothing is added until you check it
  and press **Add**.

Photo reading needs Tesseract installed:

- **New codespace:** installed automatically.
- **Existing codespace:** run
  `sudo apt-get update && sudo apt-get install -y tesseract-ocr && pip install -r requirements.txt`
  (or use **Rebuild Container** from the Command Palette).
- **Mac:** `brew install tesseract`. **Windows:** install it from
  https://github.com/UB-Mannheim/tesseract/wiki and add it to your PATH.

Without Tesseract the rest of the app works, and the Import page says what to
install.

### From the terminal

The same thing works with a text file:

```bash
python manage.py import_cards my-cards.txt --check   # match only, add nothing
python manage.py import_cards my-cards.txt
```

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
node --test "collection/js_tests/*.test.js"   # playtest engine (needs Node.js 18+)
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
| `collection/rules.py` | Downloads, parses and indexes the Comprehensive Rules |
| `collection/management/commands/load_rules.py` | The `load_rules` command |
| `collection/brackets.py` | Commander bracket rules, Game Changers list, impact card points, known combos |
| `collection/spellbook.py` | Commander Spellbook bracket and combo check |
| `collection/ocr.py` | Reads card photos with Tesseract and identifies each card |
| `collection/importer.py` | Reads a pasted card list and matches each line to a Scryfall printing |
| `collection/search.py` | Turns a search like `t:creature mv<=3` into a database filter, plus a plain-English summary |
| `collection/decks.py` | Deck rules and stats: format checks, mana curve, colors, export text (plain Python, no database calls) |
| `collection/stats.py` | All dashboard math, in plain Python (no database calls), so it's easy to test |
| `collection/views.py` | Dashboard, My Cards, Add Cards, Decks, refresh, and the autocomplete endpoint |
| `collection/static/collection/playtest-engine.js` | Playtest rules (shuffle, mulligans, zones, turns, odds) as plain functions |
| `collection/static/collection/playtest.js` | The playtest table: drawing cards, menus, dialogs, keyboard shortcuts |
| `collection/js_tests/` | Node tests for the playtest engine |
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

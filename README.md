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

## Desktop icon (Windows)

Download `tools/windows/Install Mana Ledger icon.cmd` and double-click it once.
It saves the Mana Ledger icon in your user folder (`%LOCALAPPDATA%\ManaLedger`)
and puts a **Mana Ledger** shortcut on your desktop. If Windows asks whether to
run it, choose **Run** (or **More info → Run anyway**). Nothing is installed; delete
the shortcut like any other.

Double-clicking the icon opens your Codespace in your browser. The Codespace
starts Mana Ledger by itself (`.devcontainer/start-app.sh`, run whenever it starts
or you open it), and the app opens in a new tab. If your browser blocks that tab,
allow pop-ups for github.dev, or open the **Ports** tab and click the globe next
to port 8000. The shortcut points at the "grim monster" Codespace; if you make a
new one, edit the URL in the installer.

## Permanent database

By default the app keeps everything in `db.sqlite3`, a file inside that copy of
the app, so a new Codespace starts empty. To keep one permanent collection that
every copy uses, give the app a free online Postgres database:

1. **Back up what you have.** In the app, click **Backup & database** at the
   bottom of any page and press **Download backup**.
2. **Create the database.** Sign up at [neon.tech](https://neon.tech) (free
   plan), create a project, and copy its connection string. It looks like
   `postgresql://user:password@ep-something.us-east-2.aws.neon.tech/neondb?sslmode=require`.
   [Supabase](https://supabase.com) works too (Project Settings → Database →
   connection string).
3. **Give it to Codespaces.** On GitHub go to your profile picture →
   **Settings → Codespaces → Secrets → New secret**. Name it `DATABASE_URL`,
   paste the connection string, and under **Repository access** pick
   `manaledger`. Keep this string private: it contains the database password.
4. **Start a new Codespace** (or in an open one: Ctrl+Shift+P → "Codespaces:
   Rebuild Container"). Setup creates the tables in the new database.
5. **Restore your backup** on the **Backup & database** page. The page now
   says "Postgres at ..." instead of "Local file".

From then on, any Codespace you open uses the same data. To use the same
database on your own computer, set `DATABASE_URL` there before starting the app
(PowerShell: `$env:DATABASE_URL="postgresql://..."`; Mac/Linux:
`export DATABASE_URL="postgresql://..."`), then run `python manage.py migrate`
once. Without `DATABASE_URL` the app goes back to the local file.

Backups are plain JSON in the same format as `python manage.py dumpdata`, so
`python manage.py loaddata manaledger-backup-....json` also restores one.

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
  right side of the deck page and add cards. The library panel sits beside
  the deck list in every view (Stacks, Text and Table) and stays in view as
  you scroll; on a phone it moves below the list, and **＋ Add cards** jumps
  to it. Cards you don't own enough of are marked "Need".
- **Adding new cards to your collection from a deck**: in the deck page's
  **Add cards** panel, switch from **Your library** to **All cards** and search
  any Magic card (from Scryfall; a full card name lists every printing). Pick
  how many and the finish, then **Add to collection**, or **+ deck too** to put
  it in the deck as well. If your library search finds nothing, the "Not here?"
  link switches to All cards for you. Want-list entries for the card are
  reduced automatically.
- **Deleting decks**: use **Delete deck…** in a deck tile's ⋮ menu, or press
  **Select decks** on the Decks page, tick several (click the tiles) and press
  **Delete**. You're always asked to confirm. Your cards stay in your library,
  and deleted decks go to **Recently deleted** at the bottom of the Decks page
  for 30 days, where **Put back** restores the deck with its cards, saved
  versions and want-list links. Two
  other ways to fill a deck, covered in "Deck lists and precons" below: paste
  a list from Moxfield, Archidekt, Arena or EDHREC, or build an official
  precon. The deck page shows:
  - a checklist for the format: deck size, copy limit (4, or 1 in Commander),
    sideboard size, commander choice, commander color identity, banned cards,
    and (as information, not a problem to fix) whether you own every card,
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
  - **Interaction simulator**: hypothetical opponents push back between your
    turns. Turn it on from the new game screen or **Game menu**. After the turn
    you set it to start on, **Next turn** (or N) first shows what your
    opponents did: attacks (with the attacking creatures listed), creature and
    permanent removal, board wipes, counterspells held up for your next turn,
    discard, graveyard hate, drain and theft, each as a real card like Beast
    Within or Swords to Plowshares. Nothing happens on its own: you choose the
    targets an opponent would choose, declare blocks, and press the life
    button to take damage. **Interaction settings** controls the number of
    opponents, the start turn, the most interactions per round, and the
    chance of each kind per opponent turn, with archetype presets (balanced,
    casual, aggro, control, blue, graveyard hate, cEDH). Opponents only aim at
    what you have: no creature removal against an empty board, wipes mostly
    when you have several creatures. A widget beside the table shows held
    counterspells and recent interaction. Settings are saved in your browser.
    It isn't a real game, but it shows where your deck is fragile in a way
    goldfishing can't.
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

## Deck views

The **Decks** tab shows each deck as a tile like Archidekt's: the cover art
(the commander by default), a color bar for the deck's colors, card count,
value and when it was last changed, the format and bracket, and a ⋮ menu with
shortcuts (stacks, text, table, playtest, suggestions, proxies). The footer
says whether the deck is ready, has rules to fix, needs cards, or plays above
its bracket.

On a deck page, switch the deck list between three views (the app remembers
your choice):

- **Stacks**: card images in overlapping piles by type, with each pile's count
  and price. Hover a card (or tab to it) to see all of it, with **+** / **−**
  to change copies, **★** to make it the deck's cover, and **i** for details.
  Cards you don't own enough of are marked "Need".
- **Text**: columns of card names and mana costs by type. Hover a card to see
  it in the preview on the right; click it to keep it there and change copies,
  remove it, or make it the cover.
- **Table**: the editing view, for moving cards between sections and marking
  proxies. Each card has a **Keep** box: untick the cards you're cutting and the
  bar at the bottom shows the deck size after removing them ("117 cards → 100"),
  then press **Remove unticked cards**. Unticked boxes are remembered in your
  browser until you remove them, and a version is saved first so you can undo it.

Every view has **−** / **+** next to each card to change the number of copies
(− on the last copy removes the card; + is greyed out at the format's limit), and
the same **Keep** boxes and bottom bar, so you can trim the deck from whichever
view you like.

**Show changes.** Tick **Show changes** above the deck list to see what's
different from an earlier version, in any view: cards added are marked **New**,
changed counts show **+1** / **−1**, cards moved between the main deck and
sideboard say where from, and removed cards are listed under "Removed since …"
with a **Put back** button. By default it compares with the previous version;
pick any saved version from the "since" list. Printings of the same card count
together.

Versions are saved automatically before you edit (one per editing session: if
the last one is over 30 minutes old), before a list import or Archidekt update
replaces the deck, and the first time you open a deck. **Versions** (next to the
checkbox) lets you save one by hand with a name, compare with any version, or
**Restore** a whole version (the list you had is saved first, so you can undo it).

Pick a specific cover under **Deck settings**, or with ★ in the stacks view.

## Proxies

Mark any card in a deck as a **proxy**, a stand-in you play instead of the
real card. In the deck list, tick **Proxy** under a card (for several copies,
set how many are proxies), or press **Proxy** next to a card in "Cards you
need" (or **Proxy all of these**). Proxied copies come off "Cards you need",
the shopping list and "Add these to my want list"; the deck shows how many
proxies it has.

**Print proxies** (deck list header) makes a printable sheet at real card size
(63 × 88 mm), 9 to a page with cut lines: just the proxied cards or the whole
deck, as card images or ink-saving text, optionally marked "Proxy". Print at
100% scale. Proxies are for playtesting and casual games; they aren't allowed
at sanctioned events.

## Want list

The **Want List** tab keeps track of cards you want to buy.

- **Add cards** by name (leave the set blank if any printing will do), with
  how many you want, the most you'd pay, a priority, and optionally the deck
  it's for. Or press **Want this printing** / **Want any printing** on a card
  in Add Cards, **Add to want list** on a card's page, or **Add these to my
  want list** in a deck's "Cards you need" panel. **Paste a list** takes the
  same format as Import.
- **Prices.** Each card shows today's price for the finish you want; cards at
  or under your "pay up to" price are marked. **Refresh prices** updates want
  list cards too. The tiles show how many cards you want, the estimated cost,
  and how many are at your price.
- **Got it** moves the copies you bought into your collection (choose the
  finish and, optionally, what you paid) and takes them off the list.
- **Shopping list** text for TCGplayer Mass Entry or Card Kingdom. Filter the
  list by deck. Want list cards are included in backups.

## Suggestions from your collection

**Suggestions** (button on each deck page) lists cards you own that would fit
the deck, without pushing it past the power level you want to keep:

- **Power level.** Pick the bracket to keep at the top of the page (or leave it
  at the bracket the deck plays at now). Every candidate card is added to the
  deck and the bracket estimate run again; anything that would push it higher
  (a Game Changer over the limit, mass land denial, a second extra-turn card, a
  combo, too much impact score) is left out. The tiles show how much room is
  left: Game Changers and impact points.
- **Swaps.** If the deck already plays above the bracket you want, it lists
  cuts that bring it down (extra Game Changers, combo pieces, the highest
  impact cards), each paired with a card from your collection that does a
  similar job.
- **Roles and themes.** It counts ramp, card draw, removal, wipes and lands
  against a typical Commander deck and favours what the deck is short on. It
  also finds what the deck is about (tokens, sacrifice, graveyard, a creature
  type, ...) and boosts cards that share it.
- Cards must be in your collection, legal in the format and inside the
  commander's colors. It says when a card is already in another of your decks.
  **Add** puts it in the deck; **Cut** removes every copy of a card.

**Commander ideas** (Decks tab) lists legendary creatures you own, ranked by
how many of your other cards fit their colors. Pick a bracket and press **Start
deck** to create the deck with that commander and go straight to its
suggestions.

## Deck lists and precons

**Import a deck list.** On a deck page press **Import list**, or paste a list
into "Paste a deck list" when creating a deck. Paste the text export from
Moxfield, Archidekt, MTG Arena, MTGO, EDHREC or TappedOut, press **Check
list**, review the matches, then add them. Section headers (`Commander`,
`Deck`, `Sideboard`, `Companion`) and tags (`*CMDR*`, `[Commander]`,
`[Sideboard]`, `SB:`) put each card in the right part of the deck;
`Maybeboard` and `Considering` sections are skipped. You don't need to own the
cards. Tick **Replace** to swap out what's already in the deck, or **I own
these cards** to add them to your collection as well.

**Cards you need.** Every deck page lists the cards it uses that you don't
own enough copies of (any printing you own counts; basic lands are left out),
with the price of each and the total. Copy the shopping list into TCGplayer
Mass Entry or Card Kingdom. Once the cards arrive, tick "I've got these
cards" and press the button to add them to your collection. Deck tiles on the
Decks tab show how many cards each deck still needs.

**Archidekt.** **Decks → Import from Archidekt**: enter your Archidekt
username (or profile link, like `archidekt.com/u/yourname`) and press **Link
account**. No password is needed; only the username is saved. Your public decks
are listed, each with an **Import** button that copies the exact printings,
puts the commander in the command zone and the Sideboard category in the
sideboard, and skips the Maybeboard. Unlisted decks don't appear in the list,
but you can paste a deck's link into **Import by link**; private decks can't be
read. After changing a deck on Archidekt, press **Update from Archidekt** on its
deck page to replace the list here with the current one. Archidekt has no
official API, so this uses the public data its own site reads; if Archidekt
changes it, the app shows a message instead of importing.

**Precons.** **Decks → Start from a precon** lists every official
preconstructed deck Wizards has released (Commander decks by default; pick
"All kinds" for Challenger decks, starter kits and so on). The list and the
exact printings come from [MTGJSON](https://mtgjson.com), free open data; the
list is refreshed weekly, or press **Refresh list**. **Build deck** makes a
Commander deck from a Commander precon (others are Casual). Tick **I own
this** to add every card to your collection too, foils included.

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
| `collection/importer.py` | Reads a pasted card or deck list (with Commander/Deck/Sideboard sections) and matches each line to a Scryfall printing |
| `collection/precons.py` | Official precon catalog and decklists from MTGJSON |
| `collection/suggest.py` | Deck suggestions: roles, themes, bracket-safe candidates, swaps, commander ideas |
| `collection/archidekt.py` | Public Archidekt decks: a user's deck list and one deck's cards |
| `collection/backup.py` | Backup download and all-or-nothing restore |
| `collection/search.py` | Turns a search like `t:creature mv<=3` into a database filter, plus a plain-English summary |
| `collection/decks.py` | Deck rules and stats: format checks, mana curve, colors, cards you need and the shopping list, export text (plain Python, no database calls) |
| `collection/stats.py` | All dashboard math, in plain Python (no database calls), so it's easy to test |
| `collection/views.py` | Dashboard, My Cards, Add Cards, Decks, refresh, and the autocomplete endpoint |
| `collection/static/collection/playtest-engine.js` | Playtest rules (shuffle, mulligans, zones, turns, odds) as plain functions |
| `collection/static/collection/interaction-engine.js` | Interaction simulator: categories, presets, board-aware rolls (plain functions) |
| `collection/static/collection/playtest.js` | The playtest table: drawing cards, menus, dialogs, keyboard shortcuts, interaction simulator screens |
| `collection/js_tests/` | Node tests for the playtest engine and the interaction simulator |
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

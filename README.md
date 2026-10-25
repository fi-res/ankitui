# AnkiTUI

A terminal client for [Anki](https://apps.ankiweb.net) collections, built with
[Textual](https://textual.textualize.io). Its scheduling follows Anki desktop
and [AnkiDroid](https://github.com/ankidroid/Anki-Android): both use Anki's Rust
backend (`rslib`), and the v3 (SM-2) scheduler from that backend is ported here
to Python.

## Features

- **Decks**: deck list with New / Learn / Due counts, subdecks (`Parent::Child`),
  collapse/expand, create. Each deck has its own page (`v`) with its counts and
  all of its actions: study, custom study, options, add cards, browse,
  statistics, rename, export, delete, and rebuild/empty for filtered decks.
- **Settings** (`,`): AnkiWeb account (log in / log out), sync options and the
  sync engine, icons and theme, and the collection-wide study options (next day
  start hour, learn ahead limit, new cards ignore review limit, parent limits).
- **Notes and cards**: add and edit notes. The stock note types are included
  (Basic, Basic (and reversed card), Basic (optional reversed card),
  Basic (type in the answer), Cloze). You can manage note types (fields, card
  templates, styling) and preview cards before adding them.
- **Studying**: question/answer review with the four answer buttons and their
  next intervals, type-in-the-answer comparison, undo, bury and suspend (card or
  note), flags, marking, a card info screen with the review history, and editing
  a note in the middle of a review.
- **Scheduler**: learning and relearning steps, graduating and easy intervals,
  ease factors, hard / easy / interval multipliers, lapse handling, leeches,
  interval fuzz, daily limits (children capped by their parents), sibling burying,
  new card gather and sort orders, new/review mixing, and the day rollover hour.
- **Custom study**, with the same six options as AnkiDroid: increase today's new
  card limit, increase today's review limit, review forgotten cards, review ahead,
  preview new cards, and study by card state or tag.
- **Filtered decks**: search, limits, order, rescheduling or preview mode, and an
  optional second filter.
- **Deck options**: presets with all of the SM-2 options.
- **Browser**: Anki search syntax (`deck:`, `tag:`, `is:due`, `rated:`, `prop:`,
  `added:`, `field:value`, `-negation`, `or`, parentheses, and more), with a
  card preview.
- **Import**: `.apkg` (legacy and modern zstd packages from Anki 2.1.50+ or
  AnkiDroid), `.colpkg` (replaces the whole collection, keeping a backup), and
  CSV/TSV text files (Anki `#separator:` style headers are understood).
- **Export**: `.apkg` (a deck or everything, with or without scheduling and
  media), `.colpkg`, and plain-text notes.
- **Statistics** (`s` in the deck list, `t` in the overview), for one deck or the
  whole collection, over 1 month, 3 months, 1 year or the deck's life: today's
  summary, card counts, future due forecast, a review calendar heatmap with
  streaks, reviews and review time per day (learning, young, mature, relearning,
  filtered) with a 7-day average line, interval and ease distributions, hourly
  breakdown, answer buttons, cards added, and a true retention table. The charts
  are drawn with [textual-plot](https://github.com/davidfokkema/textual-plot).
- **AnkiWeb sync** (`y`): log in with your AnkiWeb ID and password, then sync
  in both directions with Anki desktop, AnkiDroid and AnkiMobile: incremental
  sync of notes, cards, review history, decks, presets, note types, tags and
  deletions, one-way (full) upload/download when needed, and media sync.
  Self-hosted Anki sync servers are supported too.
- **Interface**: Nerd Font icons, a details panel for the highlighted deck, a
  "today" banner with your study streak, a session progress bar in the reviewer,
  a keyboard shortcut reference (`?` or `F1`) and command palette entries
  (`Ctrl+P`). The theme you pick in the palette is remembered.

The collection is stored in Anki's schema 11 SQLite format, the format inside
legacy packages. Anki desktop and AnkiDroid can open the file directly, and the
`.apkg` files that AnkiTUI exports import into them.

## Install and run

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run ankitui                # runs from the project's .venv

uv tool install .             # or install the `ankitui` command globally (~/.local/bin)
```

With pip:

```bash
pip install .
ankitui                       # uses ~/.local/share/ankitui/collection.anki2
ankitui -c path/to/collection.anki2
```

`uv sync` installs into `.venv`, so `ankitui` is only on your PATH after
`source .venv/bin/activate`. Without activating, use `uv run ankitui`.

You can also set the collection path with `ANKITUI_COLLECTION`.

### Syncing with AnkiWeb

Press `y` in the deck list (or pick "Sync with AnkiWeb" from `Ctrl+P` or
"Sync Now" in Settings, `,`). The first time, you log in with your AnkiWeb ID and password. As in
Anki, the password is not stored: AnkiTUI keeps only the sync key the server
returns, in `~/.config/ankitui/settings.json` (readable only by you). To use a
self-hosted server, tick "Use a self-hosted sync server" on the login screen.

- The first sync, and any sync after a schema change (for example editing a
  note type's fields or templates), is a one-way sync: you choose whether to
  upload this collection or download the one on AnkiWeb. If one side is empty,
  this happens automatically.
- After that, syncs are incremental and merge changes from all your devices.
- Media files are synced after the collection. Settings (`,`) has switches for
  media sync and "Sync when AnkiTUI starts", and the "Log Out" button, which
  forgets the stored sync key (also "Log out of AnkiWeb" in `Ctrl+P`).

#### Your own sync server (`ankitui server`)

To sync your phone and computer without AnkiWeb, run Anki's own sync server
on the computer (it needs the `anki` package: `uv sync --extra ankiweb`):

```bash
ankitui server -u alice              # asks for alice's password
ankitui server -u alice:secret -p 8080 --data ~/anki-sync
```

It prints the address to use, for example `http://192.168.1.20:8080/`. Enter it
as the sync server in AnkiDroid (Settings → Sync → Custom sync server), in Anki
desktop (Preferences → Syncing → Self-hosted sync server) and in AnkiTUI (tick
"Use a self-hosted sync server" on the login screen), then log in as `alice`.
The devices must be able to reach the computer: the same Wi-Fi, or a VPN such as
Tailscale when away from home. `ankitui server --help` lists all options;
the `SYNC_*` environment variables of `python -m anki.syncserver` work too.

#### Two sync engines

- **The Anki library (recommended for AnkiWeb).** When the official `anki`
  Python package is installed (`pip install "ankitui[ankiweb]"`, or with uv:
  `uv sync --extra ankiweb`), AnkiTUI syncs through it. This is Anki's own
  sync code, the same Rust core Anki desktop and AnkiDroid use, so AnkiWeb sees
  a regular Anki client. During a sync the collection is opened with the
  library, and afterwards it is converted back to the format AnkiTUI uses.
- **The built-in client.** Without the `anki` package, AnkiTUI uses its own
  implementation of the same sync protocol (v11). It works with Anki's
  [self-hosted sync server](https://docs.ankiweb.net/sync-server.html), but
  AnkiWeb answers it with "Server error 400". AnkiWeb only accepts approved
  clients, and a client that identifies itself as AnkiTUI is not one of them.

The engine can be switched in Settings ("Sync engine"). Whether AnkiWeb's
[terms](https://ankiweb.net/account/terms) cover third-party apps that use the
official library is for you to judge. Using a self-hosted server avoids the
question.

Both engines were tested against Anki's own sync server
(`python -m anki.syncserver`), with the official Anki client syncing alongside.

### Icons

The interface uses [Nerd Font](https://www.nerdfonts.com) icons, so your
terminal needs a Nerd Font (for example "JetBrainsMono Nerd Font", or "Symbols
Nerd Font" as a fallback font). Without one, switch to plain Unicode symbols in
any of these ways:

- `ankitui --no-icons`
- `ANKITUI_ICONS=0 ankitui`
- the "Nerd Font icons" switch in Settings (`,`), or the "Toggle Nerd Font
  icons" command (`Ctrl+P`)

The choice and the theme are saved in `~/.config/ankitui/settings.json`.

## Keys

| Screen | Keys |
| --- | --- |
| Deck list | `Enter` study, `v` deck page, `y` sync, `a` add, `b` browse, `s` statistics, `n` new deck, `f` filtered deck, `Space` collapse, `i` import, `u` undo, `,` settings, `q` quit |
| Deck page | `s`/`Enter` study, `c` custom study, `o` options, `t` statistics, `a` add, `b` browse, `n` rename, `e` export, `Del` delete, `r`/`E` rebuild/empty (filtered), `u` unbury, `Esc` back |
| Settings | `y` sync, `Esc` back |
| Statistics | `d` deck/collection, `p` period, `r` refresh |
| Everywhere | `?`/`F1` shortcuts, `Ctrl+P` command palette, `Ctrl+Q` quit |
| Reviewer | `Space`/`Enter` show answer, then Good; `1`–`4` answer; `u` undo; `e` edit; `*` mark; `f` flag; `-` bury card; `=` bury note; `@` suspend card; `!` suspend note; `i` card info; `o` deck options; `Ctrl+D` delete note |
| Editor | `Ctrl+S` add/save, `Ctrl+L` cloze, `Ctrl+P` preview, `Esc` close; when editing: `Ctrl+D` delete note, and from the reviewer `Ctrl+R` reset card to new and a Deck Options button |
| Browser | `/` search, `Enter`/`e` edit, `s` suspend, `f` flag, `d` change deck, `t`/`T` add/remove tags, `m` mark, `r` reset, `u` set due date, `o` sort, `Del` delete |

## Fidelity to Anki

The scheduler was checked against Anki's Rust implementation (the `anki` Python
package with fuzz turned off):

- Card states: 21,660 random answers across 1,500 random option presets, with
  no differences in type, queue, interval, ease, lapses, remaining steps or due
  date.
- Deck list counts and study queue counts: random deck trees with per-deck
  limits and sibling burying, with no differences.

Not implemented: FSRS, audio playback and image display
(media is shown as `[image: name]` and `♪ name` placeholders).

## Development

```bash
pip install -e .[dev]
pytest
```

`tests/fixtures` holds packages generated with the official Anki backend. They
cover import of both the modern and legacy formats.

To run the sync integration test against a real Anki sync server:

```bash
pip install anki
SYNC_USER1=alice:secret SYNC_PORT=27701 python -m anki.syncserver &
ANKITUI_SYNC_TEST_URL=http://127.0.0.1:27701/ pytest tests/test_sync.py tests/test_ui.py
```

## License

AGPL-3.0-or-later. The scheduling logic is derived from Anki (AGPL-3.0).

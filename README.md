# AnkiTUI

A terminal client for [Anki](https://apps.ankiweb.net) collections, built with
[Textual](https://textual.textualize.io). Its scheduling follows Anki desktop
and [AnkiDroid](https://github.com/ankidroid/Anki-Android): both use Anki's Rust
backend (`rslib`), and the v3 (SM-2) scheduler from that backend is ported here
to Python.

## Features

- **Decks**: deck list with New / Learn / Due counts, subdecks (`Parent::Child`),
  collapse/expand, create, rename, delete.
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

### Icons

The interface uses [Nerd Font](https://www.nerdfonts.com) icons, so your
terminal needs a Nerd Font (for example "JetBrainsMono Nerd Font", or "Symbols
Nerd Font" as a fallback font). Without one, switch to plain Unicode symbols in
any of these ways:

- `ankitui --no-icons`
- `ANKITUI_ICONS=0 ankitui`
- the "Toggle Nerd Font icons" command (`Ctrl+P`, or `m` in the deck list)

The choice and the theme are saved in `~/.config/ankitui/settings.json`.

## Keys

| Screen | Keys |
| --- | --- |
| Deck list | `Enter` study, `v` overview, `a` add, `b` browse, `s` statistics, `n` new deck, `f` filtered deck, `c` custom study, `o` options, `r` rename, `Del` delete, `Space` collapse, `i` import, `e` export, `u` undo, `m` more, `q` quit |
| Overview | `s`/`Enter` study, `c` custom study, `o` options, `t` statistics, `u` unbury, `Esc` back |
| Statistics | `d` deck/collection, `p` period, `r` refresh |
| Everywhere | `?`/`F1` shortcuts, `Ctrl+P` command palette, `Ctrl+Q` quit |
| Reviewer | `Space`/`Enter` show answer, then Good; `1`–`4` answer; `u` undo; `e` edit; `*` mark; `-` bury card; `=` bury note; `@` suspend card; `!` suspend note; `f` flag; `i` card info; `Ctrl+D` delete note; `m` more |
| Editor | `Ctrl+S` add/save, `Ctrl+L` cloze, `Ctrl+P` preview, `Esc` close |
| Browser | `/` search, `Enter`/`e` edit, `s` suspend, `d` change deck, `t`/`T` add/remove tags, `m` mark, `r` reset, `u` set due date, `o` sort, `Del` delete |

## Fidelity to Anki

The scheduler was checked against Anki's Rust implementation (the `anki` Python
package with fuzz turned off):

- Card states: 21,660 random answers across 1,500 random option presets, with
  no differences in type, queue, interval, ease, lapses, remaining steps or due
  date.
- Deck list counts and study queue counts: random deck trees with per-deck
  limits and sibling burying, with no differences.

Not implemented: FSRS, sync with AnkiWeb, and audio playback and image display
(media is shown as `[image: name]` and `♪ name` placeholders).

## Development

```bash
pip install -e .[dev]
pytest
```

`tests/fixtures` holds packages generated with the official Anki backend. They
cover import of both the modern and legacy formats.

## License

AGPL-3.0-or-later. The scheduling logic is derived from Anki (AGPL-3.0).

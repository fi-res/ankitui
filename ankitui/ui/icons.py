"""Nerd Font icons with plain-Unicode fallbacks.

Glyphs come from the Font Awesome set bundled in Nerd Fonts (nf-fa-*), which
have stable codepoints across Nerd Fonts v2 and v3.
"""

from __future__ import annotations

# name -> (nerd font glyph, fallback)
_ICONS: dict[str, tuple[str, str]] = {
    "app": ("\uf19d", "◆"),           # graduation-cap
    "deck": ("\uf02d", "▪"),          # book
    "folder": ("\uf07b", "▣"),        # folder
    "folder_open": ("\uf07c", "▣"),   # folder-open
    "filtered": ("\uf0b0", "◇"),      # filter
    "new": ("\uf067", "+"),           # plus
    "learn": ("\uf0eb", "~"),         # lightbulb
    "review": ("\uf021", "↻"),        # refresh
    "add": ("\uf067", "+"),
    "browse": ("\uf002", "⌕"),        # search
    "stats": ("\uf080", "▤"),         # bar-chart
    "options": ("\uf013", "⚙"),       # cog
    "import": ("\uf019", "↓"),        # download
    "export": ("\uf093", "↑"),        # upload
    "undo": ("\uf0e2", "↶"),          # undo
    "study": ("\uf04b", "▶"),         # play
    "custom": ("\uf0d0", "✦"),        # magic
    "rebuild": ("\uf021", "↻"),
    "empty": ("\uf12d", "⌫"),         # eraser
    "unbury": ("\uf06e", "◉"),        # eye
    "flag": ("\uf024", "⚑"),          # flag
    "star": ("\uf005", "★"),          # star
    "clock": ("\uf017", "◷"),         # clock
    "calendar": ("\uf073", "▦"),      # calendar
    "fire": ("\uf06d", "♨"),          # fire
    "check": ("\uf00c", "✓"),         # check
    "times": ("\uf00d", "✗"),         # times
    "again": ("\uf00d", "✗"),
    "hard": ("\uf071", "!"),          # warning
    "good": ("\uf00c", "✓"),
    "easy": ("\uf005", "★"),
    "eye": ("\uf06e", "◉"),
    "edit": ("\uf040", "✎"),          # pencil
    "trash": ("\uf1f8", "✗"),         # trash
    "info": ("\uf05a", "ⓘ"),          # info-circle
    "help": ("\uf059", "?"),          # question-circle
    "tag": ("\uf02b", "#"),           # tag
    "trophy": ("\uf091", "♛"),        # trophy
    "bolt": ("\uf0e7", "⚡"),          # bolt
    "cards": ("\uf0c5", "❐"),         # copy
    "notes": ("\uf15c", "≡"),         # file-text
    "pie": ("\uf200", "◔"),           # pie-chart
    "line": ("\uf201", "↗"),          # line-chart
    "area": ("\uf1fe", "▁"),          # area-chart
    "hourglass": ("\uf254", "⧗"),     # hourglass
    "keyboard": ("\uf11c", "⌨"),      # keyboard
    "suspend": ("\uf04c", "‖"),       # pause
    "bury": ("\uf070", "◌"),          # eye-slash
    "leech": ("\uf188", "✱"),         # bug
    "target": ("\uf140", "◎"),        # bullseye
    "sun": ("\uf185", "☼"),           # sun
    "back": ("\uf060", "←"),          # arrow-left
    "notetype": ("\uf0ca", "≔"),      # list-ul
    "theme": ("\uf1fc", "◐"),         # paint-brush
}

_enabled = True


def set_enabled(value: bool) -> None:
    global _enabled
    _enabled = bool(value)


def enabled() -> bool:
    return _enabled


def icon(name: str, space: bool = True) -> str:
    """Return the icon glyph (followed by a space), or its fallback."""
    nerd, fallback = _ICONS.get(name, ("", ""))
    glyph = nerd if _enabled else fallback
    if not glyph:
        return ""
    return glyph + (" " if space else "")


def label(name: str, text: str) -> str:
    return icon(name) + text

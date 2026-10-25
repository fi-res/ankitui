"""Keyboard shortcut reference."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from .icons import icon

SECTIONS = [
    ("deck", "Deck list", [
        ("Enter", "Study the selected deck"), ("v", "Deck overview"), ("a", "Add note"),
        ("b", "Browse cards"), ("s", "Statistics"), ("n", "Create deck"), ("f", "Create filtered deck"),
        ("c", "Custom study"), ("o", "Deck options"), ("r", "Rename deck"), ("Del / x", "Delete deck"),
        ("Space", "Collapse / expand"), ("i / e", "Import / export"), ("u", "Undo"), ("m", "More actions"),
    ]),
    ("study", "Reviewer", [
        ("Space / Enter", "Show answer, then answer Good"), ("1 2 3 4", "Again / Hard / Good / Easy"),
        ("u", "Undo"), ("e", "Edit note"), ("*", "Mark note"), ("-", "Bury card"), ("=", "Bury note"),
        ("@", "Suspend card"), ("!", "Suspend note"), ("f", "Flag card"), ("i", "Card info"),
        ("Ctrl+D", "Delete note"), ("Esc", "Back"),
    ]),
    ("edit", "Note editor", [
        ("Ctrl+S", "Add / save"), ("Ctrl+L", "Wrap selection in a cloze"), ("Ctrl+P", "Preview"), ("Tab", "Next field"),
    ]),
    ("browse", "Browser", [
        ("/", "Search"), ("Enter / e", "Edit note"), ("s", "Suspend / unsuspend"), ("d", "Change deck"),
        ("t / T", "Add / remove tags"), ("m", "Mark"), ("r", "Reset to new"), ("u", "Set due date"),
        ("o", "Sort"), ("Del", "Delete note"),
    ]),
    ("stats", "Statistics", [("d", "Deck / whole collection"), ("p", "Change period"), ("r", "Refresh")]),
    ("app", "Everywhere", [("Ctrl+P", "Command palette (themes, icons, ...)"), ("? / F1", "This help"), ("Ctrl+Q", "Quit")]),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark,f1", "dismiss_help", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog dialog-wide"):
            yield Label(icon("keyboard") + "Keyboard shortcuts", classes="dialog-title")
            with VerticalScroll():
                for ico, title, keys in SECTIONS:
                    table = Table(box=None, show_header=False, padding=(0, 2), expand=False)
                    table.add_column(style="bold orange1", min_width=16)
                    table.add_column()
                    for key, desc in keys:
                        table.add_row(key, desc)
                    yield Static(Text(icon(ico) + title, style="bold underline"), classes="help-title")
                    yield Static(table)

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

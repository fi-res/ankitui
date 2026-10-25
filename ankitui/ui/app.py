"""The Textual application."""

from __future__ import annotations

import os

from textual.app import App
from textual.binding import Binding

from ..core import Collection


def default_collection_path() -> str:
    env = os.environ.get("ANKITUI_COLLECTION")
    if env:
        return os.path.expanduser(env)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "ankitui", "collection.anki2")


class AnkiTUI(App):
    CSS_PATH = "app.tcss"
    TITLE = "AnkiTUI"
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", priority=True)]

    def __init__(self, collection_path: str | None = None):
        super().__init__()
        self.collection_path = collection_path or default_collection_path()
        self.col: Collection | None = None

    def on_mount(self) -> None:
        self.open_collection()
        from .deck_picker import DeckPickerScreen

        self.push_screen(DeckPickerScreen())

    def open_collection(self) -> None:
        if self.col is not None:
            self.col.close()
        self.col = Collection(self.collection_path)
        self.sub_title = self.collection_path

    def reopen_collection(self) -> None:
        """Close and reopen the collection (after a full .colpkg import)."""
        if self.col is not None:
            self.col.close()
            self.col = None
        self.open_collection()

    def on_unmount(self) -> None:
        if self.col is not None:
            self.col.close()
            self.col = None

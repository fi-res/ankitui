"""The Textual application."""

from __future__ import annotations

import os
from typing import Iterable

from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.screen import Screen

from ..core import Collection
from . import icons
from .settings import load_settings, save_settings


def default_collection_path() -> str:
    env = os.environ.get("ANKITUI_COLLECTION")
    if env:
        return os.path.expanduser(env)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "ankitui", "collection.anki2")


def _env_icons() -> bool | None:
    value = os.environ.get("ANKITUI_ICONS")
    if value is None:
        return None
    return value.strip().lower() not in ("0", "false", "no", "off")


class AnkiTUI(App):
    CSS_PATH = "app.tcss"
    TITLE = "AnkiTUI"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True, show=False),
        Binding("f1,question_mark", "help", "Help", show=False),
    ]

    def __init__(self, collection_path: str | None = None, use_icons: bool | None = None):
        super().__init__()
        self.collection_path = collection_path or default_collection_path()
        self.col: Collection | None = None
        self.settings = load_settings()
        choice = use_icons if use_icons is not None else _env_icons()
        if choice is None:
            choice = self.settings.get("icons", True)
        icons.set_enabled(choice)
        theme = self.settings.get("theme")
        if theme and theme in self.available_themes:
            self.theme = theme

    def on_mount(self) -> None:
        self.open_collection()
        from .deck_picker import DeckPickerScreen

        self.push_screen(DeckPickerScreen())
        from .sync import start_sync, sync_auth

        if self.settings.get("sync_on_start") and sync_auth(self) is not None:
            self.call_after_refresh(lambda: start_sync(self, lambda _ok: self._refresh_deck_list()))

    def _refresh_deck_list(self) -> None:
        hook = getattr(self.screen, "refresh_decks", None)
        if callable(hook):
            hook()

    def watch_theme(self, theme: str) -> None:
        if getattr(self, "settings", None) is not None and self.settings.get("theme") != theme:
            self.settings["theme"] = theme
            save_settings(self.settings)

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

    # ------------------------------------------------------------- actions

    def action_help(self) -> None:
        from .help import HelpScreen

        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())

    def action_toggle_icons(self) -> None:
        icons.set_enabled(not icons.enabled())
        self.settings["icons"] = icons.enabled()
        save_settings(self.settings)
        self.notify(f"Nerd Font icons {'enabled' if icons.enabled() else 'disabled'}")
        self.refresh_all_screens()

    def refresh_all_screens(self) -> None:
        for screen in self.screen_stack:
            hook = getattr(screen, "refresh_view", None)
            if callable(hook):
                hook()
        # footers and headers re-render with the new labels
        self.screen.refresh(layout=True)

    def action_open_stats(self) -> None:
        from .stats import StatsScreen

        self.push_screen(StatsScreen(self.col.current_deck_id()))

    def action_open_browser(self) -> None:
        from .browser import BrowserScreen

        self.push_screen(BrowserScreen())

    def action_add_note(self) -> None:
        from .note_editor import AddNoteScreen

        self.push_screen(AddNoteScreen())

    def action_open_notetypes(self) -> None:
        from .notetypes import NotetypesScreen

        self.push_screen(NotetypesScreen())

    def action_open_settings(self) -> None:
        from .settings_screen import SettingsScreen

        if not isinstance(self.screen, SettingsScreen):
            self.push_screen(SettingsScreen())

    def action_sync(self) -> None:
        from .sync import start_sync

        start_sync(self, lambda _ok: self._refresh_deck_list())

    def action_logout(self) -> None:
        from .sync import logout

        logout(self)
        self.notify("Logged out of AnkiWeb")

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        if self.col is None:
            return
        yield SystemCommand("Add note", "Add a new note", self.action_add_note)
        yield SystemCommand("Browse cards", "Search and edit cards", self.action_open_browser)
        yield SystemCommand("Statistics", "Show study statistics", self.action_open_stats)
        yield SystemCommand("Settings", "Account, sync, appearance and study options", self.action_open_settings)
        yield SystemCommand("Note types", "Manage note types, fields and templates", self.action_open_notetypes)
        yield SystemCommand("Sync with AnkiWeb", "Synchronise the collection and media", self.action_sync)
        if (self.settings.get("sync") or {}).get("hkey"):
            yield SystemCommand("Log out of AnkiWeb", "Forget the stored sync key", self.action_logout)
        yield SystemCommand(
            "Toggle Nerd Font icons",
            "Switch between Nerd Font icons and plain Unicode symbols",
            self.action_toggle_icons,
        )
        yield SystemCommand("Keyboard shortcuts", "Show all key bindings", self.action_help)

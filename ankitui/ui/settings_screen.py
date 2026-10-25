"""Settings: account and sync, appearance, collection-wide study options."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, Switch

from ..core import ankilib_sync
from ..core.sync import DEFAULT_ENDPOINT
from .dialogs import ConfirmDialog, MessageDialog
from .icons import icon
from .settings import save_settings

ENGINE_AUTO = "auto"
ENGINE_BUILTIN = "builtin"


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("y", "sync", "Sync"),
    ]

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        settings = self.app.settings
        conf = self.col.conf
        yield Header()
        with VerticalScroll(id="settings"):
            yield Label(icon("user") + "Account & Sync", classes="opt-section")
            yield Static("", id="account-status")
            with Horizontal(classes="settings-buttons"):
                yield Button("Log In", variant="primary", id="login")
                yield Button("Log Out", variant="error", id="logout")
                yield Button("Sync Now", id="sync-now")
            with Horizontal(classes="opt-row"):
                yield Label("Sync media files")
                yield Switch(bool(settings.get("sync_media", True)), id="sync-media")
            with Horizontal(classes="opt-row"):
                yield Label("Sync when AnkiTUI starts")
                yield Switch(bool(settings.get("sync_on_start")), id="sync-on-start")
            with Horizontal(classes="opt-row"):
                yield Label("Sync engine")
                yield Select(
                    [("Anki library (when installed)", ENGINE_AUTO), ("Built-in client", ENGINE_BUILTIN)],
                    allow_blank=False,
                    value=ENGINE_BUILTIN if settings.get("sync_backend") == ENGINE_BUILTIN else ENGINE_AUTO,
                    id="sync-engine",
                )
            yield Static("", id="engine-help", classes="opt-help")

            yield Label(icon("theme") + "Appearance", classes="opt-section")
            with Horizontal(classes="opt-row"):
                yield Label("Nerd Font icons")
                yield Switch(self._icons_enabled(), id="icons")
            with Horizontal(classes="opt-row"):
                yield Label("Theme")
                yield Select(
                    [(name, name) for name in sorted(self.app.available_themes)],
                    allow_blank=False,
                    value=self.app.theme,
                    id="theme",
                )

            yield Label(icon("study") + "Studying", classes="opt-section")
            yield Static("These options apply to all decks and are synced with the collection.", classes="opt-help")
            with Horizontal(classes="opt-row"):
                yield Label("Next day starts at (hour)")
                yield Input(str(conf.get("rollover", 4)), type="integer", id="rollover")
            with Horizontal(classes="opt-row"):
                yield Label("Learn ahead limit (minutes)")
                yield Input(str(int(conf.get("collapseTime", 1200)) // 60), type="integer", id="learn-ahead")
            with Horizontal(classes="opt-row"):
                yield Label("New cards ignore review limit")
                yield Switch(bool(conf.get("newCardsIgnoreReviewLimit")), id="ignore-review-limit")
            with Horizontal(classes="opt-row"):
                yield Label("Apply limits of all parent decks")
                yield Switch(bool(conf.get("applyAllParentLimits")), id="parent-limits")
            yield Static("", id="study-error", classes="opt-help")

            yield Label(icon("cards") + "Collection", classes="opt-section")
            yield Static("", id="collection-info", classes="opt-help")
            with Horizontal(classes="settings-buttons"):
                yield Button(icon("notetype") + "Note Types", id="notetypes")
                yield Button(icon("import") + "Import", id="import")
                yield Button(icon("export") + "Export", id="export")
            with Horizontal(classes="settings-buttons"):
                yield Button(icon("help") + "Keyboard Shortcuts", id="help")
                yield Button(icon("info") + "About", id="about")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Settings"
        self.refresh_view()

    def on_screen_resume(self) -> None:
        self.refresh_view()

    @staticmethod
    def _icons_enabled() -> bool:
        from . import icons

        return icons.enabled()

    def refresh_view(self) -> None:
        from .sync import needs_sync, sync_auth

        auth = sync_auth(self.app)
        status = Text()
        if auth:
            status.append(icon("user") + "Logged in as ")
            status.append(auth.get("username") or "?", style="bold")
            server = auth.get("endpoint") or DEFAULT_ENDPOINT
            status.append(f"\n{icon('sync')}Server: {server}", style="dim")
            if needs_sync(self.col):
                status.append("\nThere are changes that have not been synced yet.", style="yellow")
            else:
                status.append("\nEverything is synced.", style="green")
        else:
            status.append("Not logged in. Log in with your AnkiWeb account, or a self-hosted sync server, "
                          "to sync with Anki desktop, AnkiDroid and AnkiMobile.", style="dim")
        self.query_one("#account-status", Static).update(status)
        self.query_one("#login", Button).display = auth is None
        self.query_one("#logout", Button).display = auth is not None
        self.query_one("#login", Button).label = icon("user") + "Log In"
        self.query_one("#logout", Button).label = icon("logout") + "Log Out"
        self.query_one("#sync-now", Button).label = icon("sync") + "Sync Now"

        if ankilib_sync.available():
            engine_help = "The anki package is installed: AnkiWeb sees a regular Anki client."
        else:
            engine_help = ("The anki package is not installed, so the built-in client is used. "
                           "AnkiWeb rejects it; install ankitui[ankiweb] or use a self-hosted server.")
        self.query_one("#engine-help", Static).update(engine_help)

        info = Text()
        info.append(f"{self.col.path}\n")
        info.append(f"{self.col.card_count()} cards, {self.col.note_count()} notes, {len(self.col.decks)} decks")
        self.query_one("#collection-info", Static).update(info)

    # ------------------------------------------------------------ account

    @on(Button.Pressed, "#login")
    def login(self) -> None:
        from .sync import LoginScreen

        self.app.push_screen(LoginScreen(), lambda _ok: self.refresh_view())

    @on(Button.Pressed, "#logout")
    def logout(self) -> None:
        from .sync import logout, sync_auth

        auth = sync_auth(self.app) or {}

        def done(ok: bool) -> None:
            if ok:
                logout(self.app)
                self.notify("Logged out")
                self.refresh_view()

        self.app.push_screen(
            ConfirmDialog(
                "Log Out",
                f"Log out of {auth.get('username') or 'your account'}? "
                "Unsynced changes stay in this collection and are sent at the next sync after you log in again.",
                yes="Log Out",
                danger=True,
            ),
            done,
        )

    @on(Button.Pressed, "#sync-now")
    def action_sync(self) -> None:
        from .sync import start_sync

        start_sync(self.app, lambda _ok: self.refresh_view())

    @on(Switch.Changed, "#sync-media")
    def _sync_media(self, event: Switch.Changed) -> None:
        self._store("sync_media", event.value)

    @on(Switch.Changed, "#sync-on-start")
    def _sync_on_start(self, event: Switch.Changed) -> None:
        self._store("sync_on_start", event.value)

    @on(Select.Changed, "#sync-engine")
    def _sync_engine(self, event: Select.Changed) -> None:
        if event.value is not Select.NULL:
            self._store("sync_backend", event.value)

    def _store(self, key: str, value) -> None:
        if self.app.settings.get(key) != value:
            self.app.settings[key] = value
            save_settings(self.app.settings)

    # --------------------------------------------------------- appearance

    @on(Switch.Changed, "#icons")
    def _icons(self, event: Switch.Changed) -> None:
        if event.value != self._icons_enabled():
            self.app.action_toggle_icons()

    @on(Select.Changed, "#theme")
    def _theme(self, event: Select.Changed) -> None:
        if event.value is not Select.NULL and event.value != self.app.theme:
            self.app.theme = event.value

    # ----------------------------------------------------------- studying

    @on(Switch.Changed, "#ignore-review-limit")
    def _ignore_review_limit(self, event: Switch.Changed) -> None:
        self._store_conf("newCardsIgnoreReviewLimit", event.value)

    @on(Switch.Changed, "#parent-limits")
    def _parent_limits(self, event: Switch.Changed) -> None:
        self._store_conf("applyAllParentLimits", event.value)

    @on(Input.Changed, "#rollover")
    def _rollover(self, event: Input.Changed) -> None:
        value = self._int(event, 0, 23, "The next day must start between hour 0 and 23.")
        if value is not None:
            self._store_conf("rollover", value, 4)

    @on(Input.Changed, "#learn-ahead")
    def _learn_ahead(self, event: Input.Changed) -> None:
        value = self._int(event, 0, 24 * 60, "The learn ahead limit must be between 0 and 1440 minutes.")
        if value is not None:
            self._store_conf("collapseTime", value * 60, 1200)

    def _int(self, event: Input.Changed, low: int, high: int, message: str) -> int | None:
        error = self.query_one("#study-error", Static)
        try:
            value = int(event.value)
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            error.update(Text(message, style="red") if event.value else "")
            return None
        error.update("")
        return value

    def _store_conf(self, key: str, value, default=False) -> None:
        conf = self.col.conf
        # an unchanged value must not bump the collection's mtime (it would need a sync)
        if conf.get(key, default) == value:
            return
        conf[key] = value
        self.col.save()
        self.col.sched.clear_queues()

    # --------------------------------------------------------- collection

    @on(Button.Pressed, "#notetypes")
    def _notetypes(self) -> None:
        self.app.action_open_notetypes()

    @on(Button.Pressed, "#import")
    def _import(self) -> None:
        from .import_export import start_import

        start_import(self.app, self.refresh_view)

    @on(Button.Pressed, "#export")
    def _export(self) -> None:
        from .import_export import ExportScreen

        self.app.push_screen(ExportScreen(None))

    @on(Button.Pressed, "#help")
    def _help(self) -> None:
        self.app.action_help()

    @on(Button.Pressed, "#about")
    def _about(self) -> None:
        from .. import __version__

        self.app.push_screen(
            MessageDialog(
                "About",
                f"AnkiTUI {__version__}\n\nA terminal client for Anki collections.\n"
                f"Collection: {self.col.path}\n\nThe scheduler is a port of the Anki/AnkiDroid "
                "v3 (SM-2) scheduler. Collections are stored in Anki's schema 11 format.",
            )
        )

    def action_close(self) -> None:
        self.app.pop_screen()

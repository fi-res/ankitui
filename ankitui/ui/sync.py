"""AnkiWeb login and sync screens."""

from __future__ import annotations

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, LoadingIndicator, Static

from ..core.sync import (
    DEFAULT_ENDPOINT,
    CollectionSyncer,
    HttpSyncClient,
    MediaSyncer,
    SyncError,
    login,
    media_db_path,
)
from .icons import icon
from .settings import save_settings


def sync_auth(app) -> dict | None:
    auth = app.settings.get("sync") or {}
    return auth if auth.get("hkey") else None


def store_auth(app, username: str, hkey: str, endpoint: str | None) -> None:
    app.settings["sync"] = {"username": username, "hkey": hkey, "endpoint": endpoint or None}
    save_settings(app.settings)


def logout(app) -> None:
    app.settings.pop("sync", None)
    save_settings(app.settings)


def needs_sync(col) -> bool:
    return col.mod > col.ls


class LoginScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        auth = self.app.settings.get("sync") or {}
        with Vertical(classes="dialog"):
            yield Label(icon("sync") + "Log in to AnkiWeb", classes="dialog-title")
            yield Static(
                "Use your AnkiWeb account (the same one as in Anki desktop or AnkiDroid). "
                "Don't have one? Sign up for free at https://ankiweb.net.",
                classes="opt-help",
            )
            yield Label("AnkiWeb ID (email)")
            yield Input(auth.get("username", ""), placeholder="you@example.com", id="username")
            yield Label("Password")
            yield Input("", password=True, id="password")
            yield Checkbox("Use a self-hosted sync server", bool(auth.get("endpoint")), id="custom")
            yield Input(auth.get("endpoint") or "", placeholder="https://example.com/", id="endpoint")
            yield Static("", id="login-error")
            yield LoadingIndicator(id="login-busy")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Log In", variant="primary", id="login")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#login-busy").display = False
        self._toggle_endpoint()
        user = self.query_one("#username", Input)
        (self.query_one("#password", Input) if user.value else user).focus()

    @on(Checkbox.Changed, "#custom")
    def _toggle_endpoint(self) -> None:
        self.query_one("#endpoint").display = self.query_one("#custom", Checkbox).value

    @on(Input.Submitted)
    @on(Button.Pressed, "#login")
    def submit(self) -> None:
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        endpoint = None
        if self.query_one("#custom", Checkbox).value:
            endpoint = self.query_one("#endpoint", Input).value.strip() or None
        if not username or not password:
            self.query_one("#login-error", Static).update(Text("Please enter your AnkiWeb ID and password.", style="red"))
            return
        self.query_one("#login-busy").display = True
        self.query_one("#login", Button).disabled = True
        self.query_one("#login-error", Static).update("")
        self._login(username, password, endpoint)

    @work(thread=True, exclusive=True)
    def _login(self, username: str, password: str, endpoint: str | None) -> None:
        try:
            hkey = login(username, password, endpoint)
        except SyncError as err:
            self.app.call_from_thread(self._failed, str(err))
            return
        except Exception as err:  # pragma: no cover - unexpected transport problems
            self.app.call_from_thread(self._failed, f"{type(err).__name__}: {err}")
            return
        self.app.call_from_thread(self._succeeded, username, hkey, endpoint)

    def _failed(self, message: str) -> None:
        self.query_one("#login-busy").display = False
        self.query_one("#login", Button).disabled = False
        self.query_one("#login-error", Static).update(Text(message, style="red"))
        self.query_one("#password", Input).focus()

    def _succeeded(self, username: str, hkey: str, endpoint: str | None) -> None:
        store_auth(self.app, username, hkey, endpoint)
        self.notify(f"Logged in as {username}")
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)


class FullSyncChoice(ModalScreen[str | None]):
    """Ask which side wins when a one-way (full) sync is required."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, upload_ok: bool, download_ok: bool):
        super().__init__()
        self.upload_ok = upload_ok
        self.download_ok = download_ok

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(icon("sync") + "One-way sync required", classes="dialog-title")
            yield Static(
                "The collection here and the one on AnkiWeb have changes that can't be merged "
                "(for example after a note type was modified, or on the first sync).\n\n"
                "Choose which copy to keep. The other one will be overwritten, and any changes "
                "made only there since the last sync will be lost.",
                classes="dialog-body",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button(icon("export") + "Upload to AnkiWeb", id="upload", disabled=not self.upload_ok)
                yield Button(icon("import") + "Download from AnkiWeb", id="download", disabled=not self.download_ok)
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    @on(Button.Pressed, "#upload")
    def upload(self) -> None:
        self.dismiss("upload")

    @on(Button.Pressed, "#download")
    def download(self) -> None:
        self.dismiss("download")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class SyncScreen(ModalScreen[bool]):
    """Runs a sync in a worker thread and reports progress."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, on_done=None):
        super().__init__()
        self.on_done = on_done
        self.running = False
        self.collection_replaced = False

    def compose(self) -> ComposeResult:
        auth = sync_auth(self.app) or {}
        with Vertical(classes="dialog"):
            yield Label(icon("sync") + f"Syncing as {auth.get('username', '?')}", classes="dialog-title")
            yield LoadingIndicator(id="sync-busy")
            with VerticalScroll(id="sync-log"):
                yield Static("", id="sync-messages")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="close", disabled=True)

    def on_mount(self) -> None:
        self.lines: list[Text] = []
        self.running = True
        self._run_collection_sync()

    def log_line(self, message: str, style: str = "") -> None:
        self.lines.append(Text(message, style=style))
        self.query_one("#sync-messages", Static).update(Text("\n").join(self.lines))
        self.query_one("#sync-log", VerticalScroll).scroll_end(animate=False)

    def _client(self) -> HttpSyncClient:
        auth = sync_auth(self.app)
        return HttpSyncClient(auth["hkey"], auth.get("endpoint") or DEFAULT_ENDPOINT)

    def _progress(self, message: str) -> None:
        self.app.call_from_thread(self.log_line, message, "dim")

    @work(thread=True, exclusive=True, group="sync")
    def _run_collection_sync(self) -> None:
        try:
            client = self._client()
            result = CollectionSyncer(self.app.col, client, self._progress).sync()
        except SyncError as err:
            self.app.call_from_thread(self._error, err)
            return
        except Exception as err:
            self.app.call_from_thread(self._error, SyncError(f"{type(err).__name__}: {err}"))
            return
        self.app.call_from_thread(self._after_collection_sync, result, client.endpoint)

    def _after_collection_sync(self, result, endpoint: str) -> None:
        if result.new_endpoint:
            auth = sync_auth(self.app)
            store_auth(self.app, auth["username"], auth["hkey"], result.new_endpoint)
        if result.server_message:
            self.log_line(result.server_message, "yellow")
        if result.action == "full_required":
            if result.local_empty and not result.remote_empty:
                self._run_full("download", result.media_usn)
            elif result.remote_empty and not result.local_empty:
                self._run_full("upload", result.media_usn)
            else:
                self.app.push_screen(
                    FullSyncChoice(result.upload_ok, result.download_ok),
                    lambda choice: self._run_full(choice, result.media_usn) if choice else self._finish(False),
                )
            return
        if result.action == "no_changes":
            self.log_line(icon("check") + "Collection is up to date.", "green")
        else:
            stats = result.stats
            self.log_line(
                icon("check") + f"Collection synced (sent {stats.get('sent', 0)}, received {stats.get('received', 0)}).",
                "green",
            )
        self._run_media(result.media_usn)

    def _run_full(self, direction: str, media_usn: int | None) -> None:
        self.log_line(f"Full {'upload' if direction == 'upload' else 'download'}...", "bold")
        self._full_worker(direction, media_usn)

    @work(thread=True, exclusive=True, group="sync")
    def _full_worker(self, direction: str, media_usn: int | None) -> None:
        try:
            syncer = CollectionSyncer(self.app.col, self._client(), self._progress)
            if direction == "upload":
                syncer.full_upload()
            else:
                syncer.full_download()
        except SyncError as err:
            self.app.call_from_thread(self._error, err)
            if direction == "download" and self.app.col.db is None:
                self.app.call_from_thread(self.app.open_collection)
            return
        except Exception as err:
            self.app.call_from_thread(self._error, SyncError(f"{type(err).__name__}: {err}"))
            return
        self.app.call_from_thread(self._after_full, direction, media_usn)

    def _after_full(self, direction: str, media_usn: int | None) -> None:
        if direction == "download":
            self.app.open_collection()
            self.collection_replaced = True
            self.log_line(icon("check") + "Collection downloaded from AnkiWeb.", "green")
        else:
            self.log_line(icon("check") + "Collection uploaded to AnkiWeb.", "green")
        self._run_media(media_usn)

    def _run_media(self, media_usn: int | None) -> None:
        if not self.app.settings.get("sync_media", True):
            self._finish(True)
            return
        self._media_worker(media_usn)

    @work(thread=True, exclusive=True, group="sync")
    def _media_worker(self, media_usn: int | None) -> None:
        col = self.app.col
        try:
            stats = MediaSyncer(col.media_dir, media_db_path(col), self._client(), self._progress).sync(media_usn)
        except SyncError as err:
            self.app.call_from_thread(self.log_line, f"Media sync failed: {err}", "red")
            self.app.call_from_thread(self._finish, True)
            return
        except Exception as err:
            self.app.call_from_thread(self.log_line, f"Media sync failed: {type(err).__name__}: {err}", "red")
            self.app.call_from_thread(self._finish, True)
            return
        changed = stats["downloaded"] + stats["uploaded"] + stats["deleted"]
        message = (
            f"Media synced ({stats['uploaded']} uploaded, {stats['downloaded']} downloaded, "
            f"{stats['deleted']} deleted)."
            if changed
            else "Media is up to date."
        )
        self.app.call_from_thread(self.log_line, icon("check") + message, "green")
        self.app.call_from_thread(self._finish, True)

    def _error(self, err: SyncError) -> None:
        self.log_line(icon("times") + str(err), "bold red")
        if err.kind == "auth":
            logout(self.app)
            self.log_line("You have been logged out. Press y to log in again.", "yellow")
        elif err.kind == "sanity":
            self.log_line("The next sync will ask you to upload or download the whole collection.", "yellow")
        self._finish(False)

    def _finish(self, ok: bool) -> None:
        self.running = False
        self.query_one("#sync-busy").display = False
        close = self.query_one("#close", Button)
        close.disabled = False
        close.focus()
        self.app.col.sched.clear_queues()
        if ok:
            self.notify("Sync complete")
        if self.on_done:
            self.on_done(ok)

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        if self.running:
            return
        self.dismiss(True)


def start_sync(app, on_done=None) -> None:
    """Sync now, asking for credentials first if needed."""

    def run() -> None:
        app.push_screen(SyncScreen(on_done))

    if sync_auth(app) is None:
        app.push_screen(LoginScreen(), lambda ok: run() if ok else None)
    else:
        run()

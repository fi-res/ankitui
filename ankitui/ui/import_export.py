"""Import and export screens."""

from __future__ import annotations

import os
from datetime import datetime

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from ..core.exporting import export_apkg, export_colpkg, export_notes_text
from ..core.importing import ImportError_, TextImportOptions, import_apkg, import_colpkg, import_text
from .dialogs import ConfirmDialog, FilePickerDialog, MessageDialog

IMPORT_EXTENSIONS = (".apkg", ".colpkg", ".txt", ".csv", ".tsv")


def start_import(app, on_done) -> None:
    def picked(path: str | None) -> None:
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext == ".apkg":
            _import_deck_package(app, path, on_done)
        elif ext == ".colpkg":
            _import_collection_package(app, path, on_done)
        elif ext in (".txt", ".csv", ".tsv"):
            app.push_screen(TextImportScreen(path), lambda _: on_done())
        else:
            app.notify("Supported files: .apkg, .colpkg, .txt, .csv, .tsv", severity="error")

    app.push_screen(
        FilePickerDialog("Import", start=os.path.expanduser("~"), extensions=IMPORT_EXTENSIONS), picked
    )


def _import_deck_package(app, path: str, on_done) -> None:
    try:
        result = import_apkg(app.col, path)
    except (ImportError_, Exception) as err:
        app.push_screen(MessageDialog("Import failed", f"{type(err).__name__}: {err}"))
        return
    on_done()
    app.push_screen(MessageDialog("Import complete", result.summary()))


def _import_collection_package(app, path: str, on_done) -> None:
    def confirmed(ok: bool) -> None:
        if not ok:
            return
        col_path = app.collection_path
        app.col.close()
        app.col = None
        try:
            import_colpkg(path, col_path)
        except Exception as err:
            app.open_collection()
            app.push_screen(MessageDialog("Import failed", f"{type(err).__name__}: {err}"))
            return
        app.open_collection()
        on_done()
        app.push_screen(
            MessageDialog(
                "Import complete",
                f"Collection replaced with {os.path.basename(path)}.\n"
                f"{app.col.card_count()} cards, {app.col.note_count()} notes.\n"
                f"The previous collection was saved as {col_path}.bak",
            )
        )

    app.push_screen(
        ConfirmDialog(
            "Replace Collection",
            "Importing a collection package (.colpkg) REPLACES your whole collection with the "
            "package contents. A backup of the current collection will be kept. Continue?",
            yes="Replace",
            danger=True,
        ),
        confirmed,
    )


class TextImportScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog dialog-wide"):
            yield Label(f"Import text: {os.path.basename(self.path)}", classes="dialog-title")
            with Horizontal(classes="form-row"):
                yield Label("Note type")
                yield Select(
                    [(nt["name"], int(nt["id"])) for nt in self.col.all_notetypes()],
                    allow_blank=False,
                    value=int(self.col.current_notetype()["id"]),
                    id="ti-notetype",
                )
            with Horizontal(classes="form-row"):
                yield Label("Deck")
                decks = [(d["name"], int(d["id"])) for d in self.col.all_decks() if not d["dyn"]]
                cur = self.col.current_deck_id()
                yield Select(decks, allow_blank=False, value=cur if any(v == cur for _, v in decks) else decks[0][1], id="ti-deck")
            with Horizontal(classes="form-row"):
                yield Label("Field separator")
                yield Select(
                    [("Auto-detect", "auto"), ("Tab", "\t"), ("Comma", ","), ("Semicolon", ";"), ("Pipe", "|")],
                    allow_blank=False,
                    value="auto",
                    id="ti-delim",
                )
            with Horizontal(classes="form-row"):
                yield Label("Duplicates")
                yield Select(
                    [("Skip", "skip"), ("Update existing", "update"), ("Import anyway", "keep")],
                    allow_blank=False,
                    value="skip",
                    id="ti-dupes",
                )
            with Horizontal(classes="form-row"):
                yield Label("Extra tags")
                yield Input("", id="ti-tags", placeholder="space separated")
            yield Checkbox("Allow HTML in fields", True, id="ti-html")
            yield Static("Columns map to fields in order. Lines like '#separator:tab', '#html:true', "
                         "'#tags column:3', '#deck:Name' and '#notetype:Name' are honoured.", classes="opt-help")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Import", variant="primary", id="go")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed, "#go")
    def go(self) -> None:
        delim = self.query_one("#ti-delim", Select).value
        opts = TextImportOptions(
            notetype_id=int(self.query_one("#ti-notetype", Select).value),
            deck_id=int(self.query_one("#ti-deck", Select).value),
            delimiter=None if delim == "auto" else delim,
            allow_html=self.query_one("#ti-html", Checkbox).value,
            extra_tags=self.query_one("#ti-tags", Input).value.split(),
            duplicates=self.query_one("#ti-dupes", Select).value,
        )
        try:
            result = import_text(self.col, self.path, opts)
        except Exception as err:
            self.app.push_screen(MessageDialog("Import failed", f"{type(err).__name__}: {err}"))
            return
        self.dismiss(None)
        self.app.push_screen(MessageDialog("Import complete", result.summary()))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


FORMATS = [
    ("Anki Deck Package (*.apkg)", "apkg"),
    ("Anki Collection Package (*.colpkg)", "colpkg"),
    ("Notes in Plain Text (*.txt)", "txt"),
]


class ExportScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, did: int | None = None):
        super().__init__()
        self.did = did

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        decks = [("All decks", 0)] + [(d["name"], int(d["id"])) for d in self.col.all_decks() if not d["dyn"]]
        initial = self.did if self.did and any(v == self.did for _, v in decks) else 0
        with Vertical(classes="dialog dialog-wide"):
            yield Label("Export", classes="dialog-title")
            with Horizontal(classes="form-row"):
                yield Label("Format")
                yield Select(FORMATS, allow_blank=False, value="apkg", id="ex-format")
            with Horizontal(classes="form-row", id="ex-deck-row"):
                yield Label("Include")
                yield Select(decks, allow_blank=False, value=initial, id="ex-deck")
            yield Checkbox("Include scheduling information", True, id="ex-sched")
            yield Checkbox("Include media", True, id="ex-media")
            yield Checkbox("Include HTML and media references", False, id="ex-html")
            yield Checkbox("Include tags", True, id="ex-tags")
            with Horizontal(classes="form-row"):
                yield Label("Save to")
                yield Input(self._default_path("apkg"), id="ex-path")
                yield Button("Browse", id="ex-browse")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Export", variant="primary", id="go")
                yield Button("Cancel", id="cancel")

    def _default_path(self, fmt: str) -> str:
        stamp = datetime.now().strftime("%Y-%m-%d@%H-%M-%S")
        if fmt == "colpkg":
            name = f"collection-{stamp}.colpkg"
        else:
            did = self.did or 0
            deck = self.col.get_deck(did) if did else None
            base = deck["name"].replace("::", "-") if deck else "All Decks"
            base = "".join(c if c.isalnum() or c in " -_" else "_" for c in base).strip() or "deck"
            name = f"{base}.{fmt}"
        return os.path.join(os.path.expanduser("~"), name)

    def on_mount(self) -> None:
        self._update()

    @on(Select.Changed, "#ex-format")
    def format_changed(self, event: Select.Changed) -> None:
        fmt = event.value
        path_input = self.query_one("#ex-path", Input)
        root, _ext = os.path.splitext(path_input.value)
        if fmt == "colpkg":
            path_input.value = self._default_path("colpkg")
        else:
            path_input.value = root + "." + fmt if root else self._default_path(fmt)
        self._update()

    @on(Select.Changed, "#ex-deck")
    def deck_changed(self, event: Select.Changed) -> None:
        self.did = int(event.value) or None
        fmt = self.query_one("#ex-format", Select).value
        if fmt != "colpkg":
            self.query_one("#ex-path", Input).value = self._default_path(fmt)

    def _update(self) -> None:
        fmt = self.query_one("#ex-format", Select).value
        self.query_one("#ex-deck-row").display = fmt != "colpkg"
        self.query_one("#ex-sched", Checkbox).display = fmt == "apkg"
        self.query_one("#ex-media", Checkbox).display = fmt in ("apkg", "colpkg")
        self.query_one("#ex-html", Checkbox).display = fmt == "txt"
        self.query_one("#ex-tags", Checkbox).display = fmt == "txt"

    @on(Button.Pressed, "#ex-browse")
    def browse(self) -> None:
        current = self.query_one("#ex-path", Input).value

        def picked(path: str | None) -> None:
            if path:
                self.query_one("#ex-path", Input).value = path

        self.app.push_screen(
            FilePickerDialog(
                "Export to",
                start=os.path.dirname(current) or os.path.expanduser("~"),
                save=True,
                default_name=os.path.basename(current),
            ),
            picked,
        )

    @on(Button.Pressed, "#go")
    def go(self) -> None:
        fmt = self.query_one("#ex-format", Select).value
        path = os.path.expanduser(self.query_one("#ex-path", Input).value.strip())
        if not path:
            self.notify("Please choose where to save the file.", severity="error")
            return
        if not path.lower().endswith("." + fmt):
            path += "." + fmt
        folder = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(folder):
            self.notify(f"Folder does not exist: {folder}", severity="error")
            return
        did = int(self.query_one("#ex-deck", Select).value) or None
        try:
            if fmt == "apkg":
                notes, cards = export_apkg(
                    self.col,
                    path,
                    did,
                    include_scheduling=self.query_one("#ex-sched", Checkbox).value,
                    include_media=self.query_one("#ex-media", Checkbox).value,
                )
                msg = f"Exported {notes} notes / {cards} cards to\n{path}"
            elif fmt == "colpkg":
                export_colpkg(self.col, path, include_media=self.query_one("#ex-media", Checkbox).value)
                msg = f"Collection exported to\n{path}"
            else:
                n = export_notes_text(
                    self.col,
                    path,
                    did,
                    include_html=self.query_one("#ex-html", Checkbox).value,
                    include_tags=self.query_one("#ex-tags", Checkbox).value,
                )
                msg = f"Exported {n} notes to\n{path}"
        except Exception as err:
            self.app.push_screen(MessageDialog("Export failed", f"{type(err).__name__}: {err}"))
            return
        self.dismiss(None)
        self.app.push_screen(MessageDialog("Export complete", msg))

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

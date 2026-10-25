"""Adding and editing notes (AnkiDroid's NoteEditor)."""

from __future__ import annotations

import re

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, TextArea

from ..core import consts
from ..core.models import Note, field_names
from ..core.template import CLOZE_RE
from ..core.utils import canonify_tags, parse_tags

_BR_RE = re.compile(r"(?i)<br\s*/?>")


def field_to_editor(value: str) -> str:
    return _BR_RE.sub("\n", value)


def editor_to_field(value: str) -> str:
    return value.rstrip("\n").replace("\n", "<br>")


class _NoteForm(Screen):
    """Shared layout: note type / deck pickers, one TextArea per field, tags."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+l", "cloze", "Cloze"),
        Binding("ctrl+p", "preview", "Preview"),
    ]

    note: Note
    allow_type_change = False

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="editor"):
            with Horizontal(classes="editor-row"):
                yield Label("Type:")
                yield Select(
                    [(nt["name"], int(nt["id"])) for nt in self.col.all_notetypes()],
                    allow_blank=False,
                    value=self.note.mid,
                    id="notetype",
                    disabled=not self.allow_type_change,
                )
                yield Label("Deck:", id="deck-label")
                yield Select(self.deck_options(), allow_blank=False, value=self.initial_deck(), id="deck")
            yield Static("", id="fields-status")
            yield VerticalScroll(id="fields")
            yield Label("Tags (space separated):", classes="field-label")
            yield Input(" ".join(self.note.tags), id="tags")
            with Horizontal(id="editor-buttons"):
                yield Button(self.save_label(), variant="primary", id="save")
                yield Button("Cloze [...]", id="cloze")
                yield Button("Preview", id="preview")
                yield Button("Close", id="close")
        yield Footer()

    def deck_options(self) -> list[tuple[str, int]]:
        return [(d["name"], int(d["id"])) for d in self.col.all_decks() if not d["dyn"]]

    def initial_deck(self) -> int:
        return self.col.current_deck_id()

    def save_label(self) -> str:
        return "Save"

    async def on_mount(self) -> None:
        self.setup()
        await self.build_fields()

    def setup(self) -> None:
        pass

    async def build_fields(self) -> None:
        container = self.query_one("#fields", VerticalScroll)
        await container.remove_children()
        nt = self.col.get_notetype(self.note.mid)
        widgets = []
        for i, name in enumerate(field_names(nt)):
            value = self.note.fields[i] if i < len(self.note.fields) else ""
            widgets.append(Label(name, classes="field-label"))
            widgets.append(TextArea(field_to_editor(value), id=f"field-{i}", classes="field-input"))
        await container.mount_all(widgets)
        self.call_after_refresh(self._focus_first)
        self.update_status()

    def _focus_first(self) -> None:
        areas = list(self.query(TextArea))
        if areas:
            areas[0].focus()

    def collect(self) -> None:
        nt = self.col.get_notetype(self.note.mid)
        self.note.fields = [
            editor_to_field(self.query_one(f"#field-{i}", TextArea).text) for i in range(len(nt["flds"]))
        ]
        self.note.tags = canonify_tags(parse_tags(self.query_one("#tags", Input).value))

    def update_status(self) -> None:
        try:
            self.collect()
        except Exception:
            return
        state = self.col.note_is_duplicate_or_empty(self.note)
        status = self.query_one("#fields-status", Static)
        nt = self.col.get_notetype(self.note.mid)
        if state == "duplicate":
            status.update("[b red]Duplicate:[/] a note with this first field already exists.")
        elif nt.get("type") == consts.MODEL_CLOZE and not any(CLOZE_RE.search(f) for f in self.note.fields):
            status.update("[yellow]Cloze notes need a cloze deletion, e.g. {{c1::text}} (Ctrl+L).[/]")
        else:
            status.update("")

    @on(TextArea.Changed)
    def field_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "field-0" or self.col.get_notetype(self.note.mid).get("type") == consts.MODEL_CLOZE:
            self.update_status()

    @on(Button.Pressed, "#save")
    def save_pressed(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#close")
    def close_pressed(self) -> None:
        self.action_close()

    @on(Button.Pressed, "#cloze")
    def cloze_pressed(self) -> None:
        self.action_cloze()

    @on(Button.Pressed, "#preview")
    def preview_pressed(self) -> None:
        self.action_preview()

    def action_close(self) -> None:
        self.dismiss(False)

    def action_save(self) -> None:
        raise NotImplementedError

    def action_cloze(self) -> None:
        area = self.focused if isinstance(self.focused, TextArea) else None
        if area is None:
            areas = list(self.query(TextArea))
            if not areas:
                return
            area = areas[0]
        highest = 0
        for ta in self.query(TextArea):
            for m in CLOZE_RE.finditer(ta.text):
                highest = max(highest, int(m.group(1)))
        num = highest + 1
        selection = area.selection
        selected = area.selected_text
        start, end = sorted((selection.start, selection.end))
        area.replace(f"{{{{c{num}::{selected}}}}}", start, end)
        if not selected:
            row, col = start
            area.move_cursor((row, col + len(f"{{{{c{num}::")))
        area.focus()

    def action_preview(self) -> None:
        from ..core.models import Card
        from .dialogs import MessageDialog
        from .render import html_to_text

        self.collect()
        ords = self.col.card_ords_for_note(self.note)
        if not ords:
            self.notify("This note would not produce any cards.", severity="warning")
            return
        card = Card(id=0, nid=self.note.id, did=self.query_one("#deck", Select).value or 1, ord=ords[0])
        rendered = self.col.render_card(card, self.note)
        question = html_to_text(rendered.question.replace("\x00TYPEANS\x00", "[type answer]"))
        answer = html_to_text(rendered.answer.replace("\x00TYPEANS\x00", ""))
        from rich.console import Group
        from rich.rule import Rule

        self.app.push_screen(
            MessageDialog(
                f"Preview ({len(ords)} card{'s' if len(ords) != 1 else ''})",
                Group(question, Rule("answer"), answer),
            )
        )


class AddNoteScreen(_NoteForm):
    allow_type_change = True

    def __init__(self, deck_id: int | None = None):
        self.deck_id = deck_id
        super().__init__()

    def setup(self) -> None:
        self.title = "Add"

    def compose(self) -> ComposeResult:
        nt = self.col.current_notetype()
        self.note = self.col.new_note(nt)
        yield from super().compose()

    def initial_deck(self) -> int:
        did = self.deck_id or self.col.current_deck_id()
        deck = self.col.get_deck(did)
        if deck is None or deck["dyn"]:
            options = self.deck_options()
            return options[0][1] if options else 1
        return did

    def save_label(self) -> str:
        return "Add"

    @on(Select.Changed, "#notetype")
    async def notetype_changed(self, event: Select.Changed) -> None:
        if event.value is Select.NULL or int(event.value) == self.note.mid:
            return
        self.collect()
        old_fields = self.note.fields
        nt = self.col.get_notetype(int(event.value))
        self.note = self.col.new_note(nt)
        self.note.fields = (old_fields + [""] * len(nt["flds"]))[: len(nt["flds"])]
        self.note.tags = canonify_tags(parse_tags(self.query_one("#tags", Input).value))
        await self.build_fields()

    def action_save(self) -> None:
        self.collect()
        state = self.col.note_is_duplicate_or_empty(self.note)
        if state == "empty":
            self.notify("The first field is empty.", severity="error")
            return
        did = self.query_one("#deck", Select).value
        try:
            count = self.col.add_note(self.note, int(did))
        except ValueError as err:
            self.notify(str(err), severity="error")
            return
        self.notify(f"Added {count} card{'s' if count != 1 else ''}.")
        nt = self.col.get_notetype(self.note.mid)
        kept = [
            (self.note.fields[i] if nt["flds"][i].get("sticky") else "") for i in range(len(nt["flds"]))
        ]
        tags = self.note.tags
        self.note = self.col.new_note(nt)
        self.note.fields = kept
        self.note.tags = tags
        for i in range(len(nt["flds"])):
            self.query_one(f"#field-{i}", TextArea).text = field_to_editor(kept[i])
        self._focus_first()
        self.update_status()


class EditNoteScreen(_NoteForm):
    def __init__(self, nid: int):
        self.nid = nid
        super().__init__()

    def compose(self) -> ComposeResult:
        self.note = self.col.get_note(self.nid)
        yield from super().compose()

    def setup(self) -> None:
        self.title = "Edit Note"
        self.query_one("#deck", Select).display = False
        self.query_one("#deck-label", Label).display = False

    def action_save(self) -> None:
        self.collect()
        if self.col.note_is_duplicate_or_empty(self.note) == "empty":
            self.notify("The first field is empty.", severity="error")
            return
        created = self.col.update_note(self.note)
        if created:
            self.notify(f"Saved; {created} new card(s) generated.")
        else:
            self.notify("Saved.")
        self.dismiss(True)

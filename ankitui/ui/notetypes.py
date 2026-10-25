"""Managing note types: fields and card templates."""

from __future__ import annotations

import copy
import re

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Label, Select, TextArea

from ..core import consts
from ..core.models import new_field, new_template, stock_notetypes
from ..core.utils import int_time
from .dialogs import ChoiceDialog, ConfirmDialog, PromptDialog


def _ref_pattern(name: str) -> re.Pattern:
    return re.compile(r"\{\{([#^/]?)((?:[^}:]+:)*)" + re.escape(name) + r"\}\}")


def rename_field(col, nt: dict, ord_: int, new_name: str) -> None:
    old = nt["flds"][ord_]["name"]
    if any(f["name"].lower() == new_name.lower() for f in nt["flds"] if f["ord"] != ord_):
        raise ValueError("That field name is already used.")
    nt["flds"][ord_]["name"] = new_name
    pattern = _ref_pattern(old)
    for t in nt["tmpls"]:
        for key in ("qfmt", "afmt"):
            t[key] = pattern.sub(lambda m: "{{" + m.group(1) + m.group(2) + new_name + "}}", t[key])
    col.mark_schema_modified()
    col.save_notetype(nt)


def add_field(col, nt: dict, name: str) -> None:
    if any(f["name"].lower() == name.lower() for f in nt["flds"]):
        raise ValueError("That field name is already used.")
    nt["flds"].append(new_field(name, len(nt["flds"])))
    for nid, flds in col.db.execute("SELECT id, flds FROM notes WHERE mid=?", (nt["id"],)).fetchall():
        col.db.execute(
            "UPDATE notes SET flds=?, mod=?, usn=? WHERE id=?", (flds + "\x1f", int_time(), col.usn(), nid)
        )
    col.mark_schema_modified()
    col.save_notetype(nt)


def remove_field(col, nt: dict, ord_: int) -> None:
    if len(nt["flds"]) <= 1:
        raise ValueError("A note type needs at least one field.")
    name = nt["flds"][ord_]["name"]
    del nt["flds"][ord_]
    for i, f in enumerate(nt["flds"]):
        f["ord"] = i
    pattern = _ref_pattern(name)
    for t in nt["tmpls"]:
        for key in ("qfmt", "afmt"):
            t[key] = pattern.sub("", t[key])
    if nt.get("sortf", 0) >= len(nt["flds"]):
        nt["sortf"] = 0
    for nid, flds in col.db.execute("SELECT id, flds FROM notes WHERE mid=?", (nt["id"],)).fetchall():
        parts = flds.split("\x1f")
        if ord_ < len(parts):
            del parts[ord_]
        col.db.execute(
            "UPDATE notes SET flds=?, mod=?, usn=? WHERE id=?", ("\x1f".join(parts), int_time(), col.usn(), nid)
        )
    col.mark_schema_modified()
    col.save_notetype(nt)


def add_template(col, nt: dict) -> int:
    if nt.get("type") == consts.MODEL_CLOZE:
        raise ValueError("Cloze note types have a single template.")
    n = len(nt["tmpls"]) + 1
    names = {t["name"] for t in nt["tmpls"]}
    while f"Card {n}" in names:
        n += 1
    base = nt["tmpls"][0]
    nt["tmpls"].append(new_template(f"Card {n}", len(nt["tmpls"]), base["qfmt"], base["afmt"]))
    col.mark_schema_modified()
    col.save_notetype(nt)
    return regenerate_cards(col, nt)


def remove_template(col, nt: dict, ord_: int) -> None:
    if len(nt["tmpls"]) <= 1:
        raise ValueError("A note type needs at least one card type.")
    orphan = col.db.execute(
        "SELECT count() FROM notes n WHERE n.mid=? AND NOT EXISTS "
        "(SELECT 1 FROM cards c WHERE c.nid=n.id AND c.ord!=?)",
        (nt["id"], ord_),
    ).fetchone()[0]
    if orphan:
        raise ValueError(f"{orphan} note(s) would be left without cards. Delete them first.")
    cids = [
        r[0]
        for r in col.db.execute(
            "SELECT c.id FROM cards c JOIN notes n ON c.nid=n.id WHERE n.mid=? AND c.ord=?", (nt["id"], ord_)
        )
    ]
    col.remove_cards_and_orphaned_notes(cids)
    col.db.execute(
        "UPDATE cards SET ord=ord-1, mod=?, usn=? WHERE ord>? AND nid IN (SELECT id FROM notes WHERE mid=?)",
        (int_time(), col.usn(), ord_, nt["id"]),
    )
    del nt["tmpls"][ord_]
    for i, t in enumerate(nt["tmpls"]):
        t["ord"] = i
    col.mark_schema_modified()
    col.save_notetype(nt)
    col.sched.clear_queues()


def regenerate_cards(col, nt: dict) -> int:
    created = 0
    for (nid,) in col.db.execute("SELECT id FROM notes WHERE mid=?", (nt["id"],)).fetchall():
        created += col.generate_missing_cards(col.get_note(nid))
    col.save()
    col.sched.clear_queues()
    return created


class NotetypesScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add", "Add"),
        Binding("r", "rename", "Rename"),
        Binding("f", "fields", "Fields"),
        Binding("c", "cards", "Cards"),
        Binding("delete", "delete", "Delete"),
    ]

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="nt-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Note Types"
        table = self.query_one(DataTable)
        table.add_columns("Name", "Notes", "Fields", "Card types")
        self.refresh_table()
        table.focus()

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for nt in self.col.all_notetypes():
            kind = " (cloze)" if nt.get("type") == consts.MODEL_CLOZE else ""
            table.add_row(
                nt["name"] + kind,
                str(self.col.notetype_use_count(int(nt["id"]))),
                str(len(nt["flds"])),
                str(len(nt["tmpls"])),
                key=str(nt["id"]),
            )

    def selected(self) -> dict | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return self.col.get_notetype(int(key))

    def action_add(self) -> None:
        choices = [(f"stock:{i}", f"Add: {nt['name']}") for i, nt in enumerate(stock_notetypes())]
        choices += [(f"clone:{nt['id']}", f"Clone: {nt['name']}") for nt in self.col.all_notetypes()]

        def chosen(key: str | None) -> None:
            if not key:
                return
            kind, ident = key.split(":", 1)
            base = stock_notetypes()[int(ident)] if kind == "stock" else copy.deepcopy(self.col.get_notetype(int(ident)))

            def named(name: str | None) -> None:
                if not name:
                    return
                nt = copy.deepcopy(base)
                nt["id"] = 0
                nt["name"] = name
                self.col.add_notetype(nt)
                self.refresh_table()

            self.app.push_screen(PromptDialog("Add Note Type", "Name:", base["name"]), named)

        self.app.push_screen(ChoiceDialog("Add Note Type", choices), chosen)

    def action_rename(self) -> None:
        nt = self.selected()
        if not nt:
            return

        def done(name: str | None) -> None:
            if name:
                nt["name"] = name
                self.col.save_notetype(nt)
                self.refresh_table()

        self.app.push_screen(PromptDialog("Rename Note Type", "Name:", nt["name"]), done)

    def action_delete(self) -> None:
        nt = self.selected()
        if not nt:
            return
        if len(self.col.models) <= 1:
            self.notify("The last note type can't be deleted.", severity="error")
            return
        n = self.col.notetype_use_count(int(nt["id"]))

        def done(ok: bool) -> None:
            if ok:
                self.col.remove_notetype(int(nt["id"]))
                self.refresh_table()

        self.app.push_screen(
            ConfirmDialog("Delete Note Type", f"Delete '{nt['name']}' and its {n} note(s)?", yes="Delete", danger=True),
            done,
        )

    def action_fields(self) -> None:
        nt = self.selected()
        if nt:
            self.app.push_screen(FieldsScreen(int(nt["id"])), lambda _: self.refresh_table())

    def action_cards(self) -> None:
        nt = self.selected()
        if nt:
            self.app.push_screen(TemplatesScreen(int(nt["id"])), lambda _: self.refresh_table())


class FieldsScreen(Screen):
    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("a", "add", "Add field"),
        Binding("r", "rename", "Rename"),
        Binding("s", "sort_field", "Sort by this"),
        Binding("delete", "delete", "Delete"),
    ]

    def __init__(self, mid: int):
        super().__init__()
        self.mid = mid

    @property
    def col(self):
        return self.app.col

    @property
    def nt(self) -> dict:
        return self.col.get_notetype(self.mid)

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="fields-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Fields: {self.nt['name']}"
        self.query_one(DataTable).add_columns("#", "Field", "Sort field")
        self.refresh_table()
        self.query_one(DataTable).focus()

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for f in self.nt["flds"]:
            table.add_row(str(f["ord"] + 1), f["name"], "✓" if self.nt.get("sortf", 0) == f["ord"] else "", key=str(f["ord"]))

    def selected_ord(self) -> int | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_add(self) -> None:
        def done(name: str | None) -> None:
            if name:
                try:
                    add_field(self.col, self.nt, name.strip())
                except ValueError as err:
                    self.notify(str(err), severity="error")
                self.refresh_table()

        self.app.push_screen(PromptDialog("Add Field", "Field name:"), done)

    def action_rename(self) -> None:
        ord_ = self.selected_ord()
        if ord_ is None:
            return

        def done(name: str | None) -> None:
            if name:
                try:
                    rename_field(self.col, self.nt, ord_, name.strip())
                except ValueError as err:
                    self.notify(str(err), severity="error")
                self.refresh_table()

        self.app.push_screen(PromptDialog("Rename Field", "New name:", self.nt["flds"][ord_]["name"]), done)

    def action_sort_field(self) -> None:
        ord_ = self.selected_ord()
        if ord_ is None:
            return
        nt = self.nt
        nt["sortf"] = ord_
        self.col.save_notetype(nt)
        for (nid,) in self.col.db.execute("SELECT id FROM notes WHERE mid=?", (self.mid,)).fetchall():
            self.col._write_note(self.col.get_note(nid))
        self.col.save()
        self.refresh_table()

    def action_delete(self) -> None:
        ord_ = self.selected_ord()
        if ord_ is None:
            return

        def done(ok: bool) -> None:
            if ok:
                try:
                    remove_field(self.col, self.nt, ord_)
                except ValueError as err:
                    self.notify(str(err), severity="error")
                self.refresh_table()

        self.app.push_screen(
            ConfirmDialog("Delete Field", f"Delete field '{self.nt['flds'][ord_]['name']}' from all notes?", yes="Delete", danger=True),
            done,
        )


class TemplatesScreen(Screen):
    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, mid: int):
        super().__init__()
        self.mid = mid
        self.ord = 0

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        nt = self.col.get_notetype(self.mid)
        yield Header()
        with Vertical(id="editor"):
            with Horizontal(classes="editor-row"):
                yield Label("Card type:")
                yield Select(self._options(nt), allow_blank=False, value=0, id="tmpl")
                yield Button("Add", id="add")
                yield Button("Rename", id="rename")
                yield Button("Delete", id="delete")
            yield Label("Front template", classes="field-label")
            yield TextArea(id="qfmt", classes="field-input")
            yield Label("Back template", classes="field-label")
            yield TextArea(id="afmt", classes="field-input")
            yield Label("Styling (shared)", classes="field-label")
            yield TextArea(nt.get("css", ""), id="css", classes="field-input")
            with Horizontal(id="editor-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Close", id="close")
        yield Footer()

    def _options(self, nt: dict) -> list[tuple[str, int]]:
        return [(t["name"], t["ord"]) for t in nt["tmpls"]]

    def on_mount(self) -> None:
        self.title = f"Card Types: {self.col.get_notetype(self.mid)['name']}"
        self.load(0)

    def load(self, ord_: int) -> None:
        nt = self.col.get_notetype(self.mid)
        self.ord = min(ord_, len(nt["tmpls"]) - 1)
        tmpl = nt["tmpls"][self.ord]
        self.query_one("#qfmt", TextArea).text = tmpl["qfmt"]
        self.query_one("#afmt", TextArea).text = tmpl["afmt"]

    def _store(self) -> dict:
        nt = self.col.get_notetype(self.mid)
        tmpl = nt["tmpls"][self.ord]
        tmpl["qfmt"] = self.query_one("#qfmt", TextArea).text
        tmpl["afmt"] = self.query_one("#afmt", TextArea).text
        nt["css"] = self.query_one("#css", TextArea).text
        return nt

    @on(Select.Changed, "#tmpl")
    def tmpl_changed(self, event: Select.Changed) -> None:
        if event.value is Select.NULL or int(event.value) == self.ord:
            return
        self.col.save_notetype(self._store())
        self.load(int(event.value))

    def _reload_select(self, value: int) -> None:
        sel = self.query_one("#tmpl", Select)
        sel.set_options(self._options(self.col.get_notetype(self.mid)))
        sel.value = value
        self.load(value)

    @on(Button.Pressed, "#add")
    def add(self) -> None:
        try:
            self.col.save_notetype(self._store())
            created = add_template(self.col, self.col.get_notetype(self.mid))
        except ValueError as err:
            self.notify(str(err), severity="error")
            return
        self.notify(f"Card type added; {created} card(s) generated.")
        self._reload_select(len(self.col.get_notetype(self.mid)["tmpls"]) - 1)

    @on(Button.Pressed, "#rename")
    def rename(self) -> None:
        nt = self.col.get_notetype(self.mid)

        def done(name: str | None) -> None:
            if name:
                nt2 = self._store()
                nt2["tmpls"][self.ord]["name"] = name
                self.col.save_notetype(nt2)
                self._reload_select(self.ord)

        self.app.push_screen(PromptDialog("Rename Card Type", "Name:", nt["tmpls"][self.ord]["name"]), done)

    @on(Button.Pressed, "#delete")
    def delete(self) -> None:
        def done(ok: bool) -> None:
            if not ok:
                return
            try:
                remove_template(self.col, self.col.get_notetype(self.mid), self.ord)
            except ValueError as err:
                self.notify(str(err), severity="error")
                return
            self._reload_select(0)

        self.app.push_screen(
            ConfirmDialog("Delete Card Type", "Delete this card type and all of its cards?", yes="Delete", danger=True), done
        )

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        nt = self._store()
        self.col.save_notetype(nt)
        created = regenerate_cards(self.col, nt)
        self.notify("Saved" + (f"; {created} new card(s) generated." if created else "."))

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)

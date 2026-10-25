"""The card browser."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from rich.console import Group
from rich.rule import Rule
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from ..core import consts
from ..core.search import ORDER_CLAUSES, SearchError
from ..core.utils import strip_html
from .dialogs import ChoiceDialog, ConfirmDialog, PromptDialog
from .icons import icon
from .render import html_to_text

MAX_ROWS = 5000


class BrowserScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+f,slash", "focus_search", "Search"),
        Binding("e", "edit", "Edit"),
        Binding("s", "toggle_suspend", "Suspend"),
        Binding("d", "change_deck", "Deck"),
        Binding("t", "add_tag", "Tag"),
        Binding("T", "remove_tag", "Untag", show=False),
        Binding("m", "toggle_mark", "Mark", show=False),
        Binding("r", "forget", "Reset", show=False),
        Binding("u", "set_due", "Set due", show=False),
        Binding("o", "sort", "Sort"),
        Binding("delete", "delete", "Delete"),
        Binding("a", "add", "Add", show=False),
    ]

    def __init__(self, query: str = ""):
        super().__init__()
        self.query_text = query
        self.cids: list[int] = []
        self.sort_key = "noteCrt"

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="browser-search"):
            yield Input(self.query_text, placeholder='Search, e.g. deck:French is:due tag:verb "some text"', id="search")
            yield Button(icon("browse") + "Search", id="go")
        yield DataTable(id="browser-table", cursor_type="row", zebra_stripes=True)
        with VerticalScroll(id="browser-preview"):
            yield Static("", id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Browse"
        table = self.query_one(DataTable)
        table.add_column("Sort Field", key="sort", width=36)
        table.add_column("Card", key="card", width=14)
        table.add_column("Deck", key="deck", width=20)
        table.add_column("Due", key="due", width=12)
        table.add_column("Ivl", key="ivl", width=6)
        table.add_column("Ease", key="ease", width=5)
        table.add_column("Reps", key="reps", width=5)
        table.add_column("Lapses", key="lapses", width=6)
        self.search()
        table.focus()

    def search(self) -> None:
        text = self.query_one("#search", Input).value
        try:
            cids = self.col.find_cards(text, ORDER_CLAUSES[self.sort_key])
        except SearchError as err:
            self.notify(str(err), severity="error")
            return
        except Exception as err:  # malformed SQL from odd input
            self.notify(f"Invalid search: {err}", severity="error")
            return
        self.cids = cids
        self.populate()

    def populate(self, keep_cid: int | None = None) -> None:
        table = self.query_one(DataTable)
        table.clear()
        shown = self.cids[:MAX_ROWS]
        today = self.col.today
        for cid in shown:
            row = self._row(cid, today)
            if row:
                table.add_row(*row, key=str(cid))
        total = len(self.cids)
        self.sub_title = f"{total} card{'s' if total != 1 else ''} shown" + (
            f" (first {MAX_ROWS})" if total > MAX_ROWS else ""
        )
        if keep_cid is not None and keep_cid in shown:
            table.move_cursor(row=shown.index(keep_cid))
        self.update_preview()

    def _row(self, cid: int, today: int):
        r = self.col.db.execute(
            "SELECT c.ord, c.did, c.odid, c.queue, c.type, c.due, c.odue, c.ivl, c.factor, c.reps, c.lapses, "
            "n.sfld, n.mid, c.flags, n.tags FROM cards c JOIN notes n ON c.nid = n.id WHERE c.id = ?",
            (cid,),
        ).fetchone()
        if r is None:
            return None
        ord_, did, odid, queue, ctype, due, odue, ivl, factor, reps, lapses, sfld, mid, flags, tags = r
        nt = self.col.get_notetype(mid)
        if nt and nt.get("type") == consts.MODEL_CLOZE:
            card_name = f"Cloze {ord_ + 1}"
        elif nt and ord_ < len(nt["tmpls"]):
            card_name = nt["tmpls"][ord_]["name"]
        else:
            card_name = str(ord_ + 1)
        deck = self.col.get_deck(did)
        deck_name = deck["name"] if deck else "?"
        if odid:
            home = self.col.get_deck(odid)
            deck_name += f" ({home['name'] if home else '?'})"
        real_due = odue if odid and odue else due
        if ctype == consts.CARD_TYPE_NEW:
            due_txt = f"New #{real_due}"
        elif queue in (consts.QUEUE_LRN, consts.QUEUE_PREVIEW) or (ctype in (1, 3) and real_due > 1_000_000_000):
            due_txt = datetime.fromtimestamp(real_due).strftime("%H:%M %m-%d")
        else:
            due_txt = (date.today() + timedelta(days=real_due - today)).isoformat()
        if queue == consts.QUEUE_SUSPENDED:
            due_txt = f"({due_txt})"
        elif queue in (consts.QUEUE_SCHED_BURIED, consts.QUEUE_USER_BURIED):
            due_txt = f"[{due_txt}]"
        style = ""
        if queue == consts.QUEUE_SUSPENDED:
            style = "on dark_goldenrod"
        elif flags & 7:
            style = {1: "red", 2: "dark_orange", 3: "green", 4: "blue", 5: "hot_pink", 6: "turquoise2", 7: "purple"}[flags & 7]
        elif " marked " in f" {tags.lower()} ":
            style = "magenta"
        sort_txt = Text(strip_html(str(sfld)).replace("\n", " ")[:80], style=style)
        return (
            sort_txt,
            card_name,
            deck_name,
            due_txt,
            f"{ivl}d" if ctype == consts.CARD_TYPE_REV else "",
            f"{factor // 10}%" if ctype == consts.CARD_TYPE_REV else "",
            str(reps),
            str(lapses),
        )

    def selected_cid(self) -> int | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def update_preview(self) -> None:
        cid = self.selected_cid()
        preview = self.query_one("#preview", Static)
        if cid is None:
            preview.update("")
            return
        card = self.col.get_card(cid)
        if card is None:
            preview.update("")
            return
        rendered = self.col.render_card(card)
        q = html_to_text(rendered.question.replace("\x00TYPEANS\x00", ""))
        a = html_to_text(rendered.answer.replace("\x00TYPEANS\x00", ""))
        note = self.col.get_note(card.nid)
        tags = Text("Tags: " + (" ".join(note.tags) or "-"), style="dim")
        preview.update(Group(q, Rule("answer", style="dim"), a, tags))

    @on(DataTable.RowHighlighted)
    def highlighted(self) -> None:
        self.update_preview()

    @on(DataTable.RowSelected)
    def selected(self) -> None:
        self.action_edit()

    @on(Input.Submitted, "#search")
    @on(Button.Pressed, "#go")
    def do_search(self) -> None:
        self.search()
        self.query_one(DataTable).focus()

    def _refresh_row(self, cid: int) -> None:
        self.populate(keep_cid=cid)

    # ------------------------------------------------------------ actions

    def action_back(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one(DataTable).focus()
            return
        self.app.pop_screen()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_edit(self) -> None:
        cid = self.selected_cid()
        if cid is None:
            return
        from .note_editor import EditNoteScreen

        card = self.col.get_card(cid)
        self.app.push_screen(EditNoteScreen(card.nid), lambda _: self._refresh_row(cid))

    def action_add(self) -> None:
        from .note_editor import AddNoteScreen

        self.app.push_screen(AddNoteScreen(), lambda _: self.search())

    def action_toggle_suspend(self) -> None:
        cid = self.selected_cid()
        if cid is None:
            return
        card = self.col.get_card(cid)
        if card.queue == consts.QUEUE_SUSPENDED:
            self.col.sched.unsuspend_or_unbury([cid])
            self.notify("Card unsuspended")
        else:
            self.col.sched.suspend_cards([cid])
            self.notify("Card suspended")
        self._refresh_row(cid)

    def action_change_deck(self) -> None:
        cid = self.selected_cid()
        if cid is None:
            return
        choices = [(str(d["id"]), d["name"]) for d in self.col.all_decks() if not d["dyn"]]

        def done(key: str | None) -> None:
            if key is None:
                return
            self.col.set_deck([cid], int(key))
            self._refresh_row(cid)

        self.app.push_screen(ChoiceDialog("Change deck", choices), done)

    def _selected_nid(self) -> int | None:
        cid = self.selected_cid()
        if cid is None:
            return None
        return self.col.get_card(cid).nid

    def action_add_tag(self) -> None:
        nid = self._selected_nid()
        if nid is None:
            return
        cid = self.selected_cid()

        def done(value: str | None) -> None:
            if value:
                for tag in value.split():
                    self.col.add_tags_to_notes([nid], tag)
                self._refresh_row(cid)

        self.app.push_screen(PromptDialog("Add Tags", "Tags to add (space separated):"), done)

    def action_remove_tag(self) -> None:
        nid = self._selected_nid()
        if nid is None:
            return
        cid = self.selected_cid()

        def done(value: str | None) -> None:
            if value:
                for tag in value.split():
                    self.col.remove_tags_from_notes([nid], tag)
                self._refresh_row(cid)

        self.app.push_screen(PromptDialog("Remove Tags", "Tags to remove (space separated):"), done)

    def action_toggle_mark(self) -> None:
        nid = self._selected_nid()
        if nid is None:
            return
        note = self.col.get_note(nid)
        if note.has_tag("marked"):
            self.col.remove_tags_from_notes([nid], "marked")
        else:
            self.col.add_tags_to_notes([nid], "marked")
        self._refresh_row(self.selected_cid())

    def action_forget(self) -> None:
        cid = self.selected_cid()
        if cid is None:
            return

        def done(ok: bool) -> None:
            if ok:
                self.col.sched.forget_cards([cid])
                self._refresh_row(cid)

        self.app.push_screen(ConfirmDialog("Reset Card", "Reset this card to a new card?"), done)

    def action_set_due(self) -> None:
        cid = self.selected_cid()
        if cid is None:
            return

        def done(value: str | None) -> None:
            if value is None or not value.strip().lstrip("-").isdigit():
                return
            self.col.sched.set_due_date([cid], int(value))
            self._refresh_row(cid)

        self.app.push_screen(
            PromptDialog("Set Due Date", "Days from today (0 = today, 1 = tomorrow):", "0", restrict=r"-?\d*"), done
        )

    def action_sort(self) -> None:
        labels = {
            "noteCrt": "Created",
            "noteFld": "Sort field",
            "cardDue": "Due",
            "cardIvl": "Interval",
            "cardEase": "Ease",
            "cardReps": "Reviews",
            "cardLapses": "Lapses",
            "cardMod": "Card modified",
            "noteMod": "Note modified",
        }

        def done(key: str | None) -> None:
            if key:
                self.sort_key = key
                self.search()

        self.app.push_screen(ChoiceDialog("Sort by", list(labels.items())), done)

    def action_delete(self) -> None:
        nid = self._selected_nid()
        if nid is None:
            return

        def done(ok: bool) -> None:
            if ok:
                self.col.remove_notes([nid])
                self.search()

        self.app.push_screen(
            ConfirmDialog("Delete Note", "Delete the selected note and all of its cards?", yes="Delete", danger=True),
            done,
        )

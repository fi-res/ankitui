"""The main screen: the deck list (AnkiDroid's DeckPicker)."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..core.sched.scheduler import DeckNode
from .dialogs import ChoiceDialog, ConfirmDialog, MessageDialog, PromptDialog

NEW_COLOR = "dodger_blue1"
LEARN_COLOR = "red1"
REVIEW_COLOR = "green3"


def count_cell(value: int, color: str) -> Text:
    if value == 0:
        return Text("0", style="dim", justify="right")
    return Text(str(value), style=f"bold {color}", justify="right")


class DeckPickerScreen(Screen):
    BINDINGS = [
        Binding("enter", "study", "Study", show=True),
        Binding("v", "overview", "Overview", show=False),
        Binding("a", "add", "Add"),
        Binding("b", "browse", "Browse"),
        Binding("n", "new_deck", "New deck"),
        Binding("f", "new_filtered", "Filtered deck", show=False),
        Binding("c", "custom_study", "Custom study", show=False),
        Binding("o", "options", "Options"),
        Binding("r", "rename", "Rename", show=False),
        Binding("delete,x", "delete", "Delete", show=False),
        Binding("space", "toggle_collapse", "Collapse", show=False),
        Binding("i", "import", "Import"),
        Binding("e", "export", "Export"),
        Binding("u,ctrl+z", "undo", "Undo", show=False),
        Binding("m", "manage", "More..."),
        Binding("f5", "refresh", "Refresh", show=False),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        table = DataTable(id="deck-table", cursor_type="row", zebra_stripes=False)
        yield table
        yield Static("", id="deck-summary")
        yield Footer()

    @property
    def col(self):
        return self.app.col

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Deck", key="deck")
        table.add_column(Text("New", justify="right"), key="new", width=6)
        table.add_column(Text("Learn", justify="right"), key="learn", width=6)
        table.add_column(Text("Due", justify="right"), key="due", width=6)
        self.refresh_decks()
        table.focus()

    def on_screen_resume(self) -> None:
        self.refresh_decks()

    def refresh_decks(self, select_did: int | None = None) -> None:
        table = self.query_one(DataTable)
        if select_did is None and table.row_count:
            try:
                select_did = int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            except Exception:
                select_did = None
        if select_did is None:
            select_did = self.col.current_deck_id()
        table.clear()
        self._rows: list[int] = []

        def add(node: DeckNode) -> None:
            if node.children:
                marker = "▸ " if node.collapsed else "▾ "
            else:
                marker = "  "
            label = Text("  " * node.depth + marker)
            label.append(node.name, style="italic cyan" if node.filtered else "")
            table.add_row(
                label,
                count_cell(node.new, NEW_COLOR),
                count_cell(node.learn, LEARN_COLOR),
                count_cell(node.review, REVIEW_COLOR),
                key=str(node.did),
            )
            self._rows.append(node.did)
            if not node.collapsed:
                for child in node.children:
                    add(child)

        for node in self.col.sched.deck_tree():
            add(node)
        if select_did in self._rows:
            table.move_cursor(row=self._rows.index(select_did))
        self._update_summary()

    def _update_summary(self) -> None:
        timing = self.col.timing_today()
        start = (timing.next_day_at - 86_400) * 1000
        cards, ms = self.col.db.execute(
            "SELECT count(), coalesce(sum(time), 0) FROM revlog WHERE id > ? AND type != 4", (start,)
        ).fetchone()
        minutes = ms / 60000
        undo = self.col.sched.undo_name()
        undo_text = f"   Undo available: {undo} (u)" if undo else ""
        self.query_one("#deck-summary", Static).update(
            f"Studied {cards} card{'s' if cards != 1 else ''} in {minutes:.1f} minutes today.  "
            f"{self.col.card_count()} cards, {self.col.note_count()} notes.{undo_text}"
        )

    def selected_did(self) -> int | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return int(row_key.value)

    # ------------------------------------------------------------ actions

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_study()

    def action_study(self) -> None:
        did = self.selected_did()
        if did is None:
            return
        self.col.set_current_deck(did)
        counts = self.col.sched.counts()
        if counts.new + counts.learning + counts.review:
            from .reviewer import ReviewerScreen

            self.app.push_screen(ReviewerScreen())
        else:
            self.action_overview()

    def action_overview(self) -> None:
        did = self.selected_did()
        if did is None:
            return
        self.col.set_current_deck(did)
        from .overview import OverviewScreen

        self.app.push_screen(OverviewScreen())

    def action_add(self) -> None:
        from .note_editor import AddNoteScreen

        did = self.selected_did()
        self.app.push_screen(AddNoteScreen(deck_id=did))

    def action_browse(self) -> None:
        from .browser import BrowserScreen

        did = self.selected_did()
        deck = self.col.get_deck(did) if did else None
        query = f'deck:"{deck["name"]}"' if deck else ""
        self.app.push_screen(BrowserScreen(query))

    def action_new_deck(self) -> None:
        def done(name: str | None) -> None:
            if not name:
                return
            try:
                did = self.col.add_normal_deck(name)
            except ValueError as err:
                self.notify(str(err), severity="error")
                return
            self.refresh_decks(did)

        self.app.push_screen(PromptDialog("Create Deck", "Deck name (use :: for subdecks):"), done)

    def action_new_filtered(self) -> None:
        from .filtered_deck import FilteredDeckScreen

        self.app.push_screen(FilteredDeckScreen(), lambda _: self.refresh_decks())

    def action_custom_study(self) -> None:
        did = self.selected_did()
        if did is None:
            return
        if self.col.get_deck(did)["dyn"]:
            self.notify("Custom study is not available for filtered decks.", severity="warning")
            return
        from .custom_study import CustomStudyScreen

        self.col.set_current_deck(did)

        def done(result) -> None:
            self.refresh_decks()
            if result == "study":
                from .overview import OverviewScreen

                self.app.push_screen(OverviewScreen())

        self.app.push_screen(CustomStudyScreen(did), done)

    def action_options(self) -> None:
        did = self.selected_did()
        if did is None:
            return
        deck = self.col.get_deck(did)
        if deck["dyn"]:
            from .filtered_deck import FilteredDeckScreen

            self.app.push_screen(FilteredDeckScreen(did), lambda _: self.refresh_decks())
            return
        from .deck_options import DeckOptionsScreen

        self.app.push_screen(DeckOptionsScreen(did), lambda _: self.refresh_decks())

    def action_rename(self) -> None:
        did = self.selected_did()
        if did is None:
            return
        deck = self.col.get_deck(did)

        def done(name: str | None) -> None:
            if not name or name == deck["name"]:
                return
            try:
                self.col.rename_deck(did, name)
            except ValueError as err:
                self.notify(str(err), severity="error")
                return
            self.refresh_decks(did)

        self.app.push_screen(PromptDialog("Rename Deck", "New name:", deck["name"]), done)

    def action_delete(self) -> None:
        did = self.selected_did()
        if did is None:
            return
        deck = self.col.get_deck(did)
        ids = ",".join(str(d) for d in self.col.child_ids(did, include_self=True))
        if deck["dyn"]:
            msg = f"Delete filtered deck '{deck['name']}'? Its cards will return to their original decks."
        else:
            n = self.col.db.execute(f"SELECT count() FROM cards WHERE did IN ({ids}) OR odid IN ({ids})").fetchone()[0]
            msg = f"Delete deck '{deck['name']}' including its subdecks and {n} card(s)? This cannot be undone."

        def done(ok: bool) -> None:
            if ok:
                self.col.remove_deck(did)
                self.notify(f"Deleted {deck['name']}")
                self.refresh_decks()

        self.app.push_screen(ConfirmDialog("Delete Deck", msg, yes="Delete", danger=True), done)

    def action_toggle_collapse(self) -> None:
        did = self.selected_did()
        if did is None or not self.col.child_ids(did):
            return
        deck = self.col.get_deck(did)
        deck["collapsed"] = not deck.get("collapsed", False)
        self.col.save_deck(deck)
        self.refresh_decks(did)

    def action_import(self) -> None:
        from .import_export import start_import

        start_import(self.app, self.refresh_decks)

    def action_export(self) -> None:
        from .import_export import ExportScreen

        self.app.push_screen(ExportScreen(self.selected_did()))

    def action_undo(self) -> None:
        entry = self.col.sched.undo()
        if entry is None:
            self.notify("Nothing to undo")
        else:
            self.notify(f"Undone: {entry.name}")
        self.refresh_decks()

    def action_refresh(self) -> None:
        self.col.sched.clear_queues()
        self.refresh_decks()

    def action_manage(self) -> None:
        did = self.selected_did()
        choices = [
            ("rename", "Rename deck"),
            ("delete", "Delete deck"),
            ("unbury", "Unbury cards in deck"),
            ("notetypes", "Manage note types"),
            ("stats", "Collection statistics"),
            ("about", "About AnkiTUI"),
        ]
        if did is not None and self.col.get_deck(did)["dyn"]:
            choices = [("rebuild", "Rebuild filtered deck"), ("empty", "Empty filtered deck")] + choices

        def done(key: str | None) -> None:
            if key == "rename":
                self.action_rename()
            elif key == "delete":
                self.action_delete()
            elif key == "unbury" and did is not None:
                self.col.sched.unbury_deck(did)
                self.refresh_decks()
            elif key == "rebuild" and did is not None:
                n = self.col.sched.rebuild_filtered_deck(did)
                self.notify(f"Rebuilt: {n} cards")
                self.refresh_decks()
            elif key == "empty" and did is not None:
                self.col.sched.empty_filtered_deck(did)
                self.refresh_decks()
            elif key == "notetypes":
                from .notetypes import NotetypesScreen

                self.app.push_screen(NotetypesScreen())
            elif key == "stats":
                self.app.push_screen(MessageDialog("Statistics", collection_stats(self.col)))
            elif key == "about":
                from .. import __version__

                self.app.push_screen(
                    MessageDialog(
                        "About",
                        f"AnkiTUI {__version__}\n\nA terminal client for Anki collections.\n"
                        f"Collection: {self.col.path}\n\nThe scheduler is a port of the Anki/AnkiDroid "
                        "v3 (SM-2) scheduler. Collections are stored in Anki's schema 11 format.",
                    )
                )

        self.app.push_screen(ChoiceDialog("Deck actions", choices), done)


def collection_stats(col) -> str:
    from ..core import consts

    q = col.db.execute
    total = col.card_count()
    new = q("SELECT count() FROM cards WHERE type = 0").fetchone()[0]
    learning = q("SELECT count() FROM cards WHERE type IN (1, 3)").fetchone()[0]
    review = q("SELECT count() FROM cards WHERE type = 2").fetchone()[0]
    mature = q("SELECT count() FROM cards WHERE type = 2 AND ivl >= 21").fetchone()[0]
    suspended = q(f"SELECT count() FROM cards WHERE queue = {consts.QUEUE_SUSPENDED}").fetchone()[0]
    buried = q("SELECT count() FROM cards WHERE queue IN (-2, -3)").fetchone()[0]
    reviews, ms = q("SELECT count(), coalesce(sum(time), 0) FROM revlog").fetchone()
    timing = col.timing_today()
    tomorrow = q(
        "SELECT count() FROM cards WHERE queue IN (2, 3) AND due <= ?", (timing.days_elapsed + 1,)
    ).fetchone()[0]
    avg_ease = q("SELECT avg(factor) FROM cards WHERE type = 2").fetchone()[0]
    week_start = (timing.next_day_at - 7 * 86_400) * 1000
    week = q(
        "SELECT count(), coalesce(sum(CASE WHEN ease = 1 THEN 1 ELSE 0 END), 0) FROM revlog "
        "WHERE id > ? AND type IN (1, 2)",
        (week_start,),
    ).fetchone()
    retention = f"{100 * (1 - week[1] / week[0]):.1f}%" if week[0] else "n/a"
    lines = [
        f"Cards: {total}",
        f"  New: {new}",
        f"  Learning: {learning}",
        f"  Review: {review} (mature: {mature})",
        f"  Suspended: {suspended}",
        f"  Buried: {buried}",
        "",
        f"Notes: {col.note_count()}",
        f"Reviews due by tomorrow: {tomorrow}",
    ]
    if avg_ease:
        lines.append(f"Average ease: {avg_ease / 10:.0f}%")
    lines += [
        "",
        f"Total reviews: {reviews} ({ms / 3_600_000:.1f} hours)",
        f"Last 7 days: {week[0]} reviews, retention {retention}",
    ]
    return "\n".join(lines)

"""The main screen: the deck list (AnkiDroid's DeckPicker)."""

from __future__ import annotations

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..core.sched.scheduler import DeckNode
from ..core.search import escape_search_value
from ..core.stats import Stats
from .charts import proportion_bar
from .dialogs import ChoiceDialog, ConfirmDialog, MessageDialog, PromptDialog
from .icons import icon

NEW_COLOR = "dodger_blue1"
LEARN_COLOR = "red1"
REVIEW_COLOR = "green3"


def count_cell(value: int, color: str) -> Text:
    if value == 0:
        return Text("·", style="dim", justify="right")
    return Text(str(value), style=f"bold {color}", justify="right")


def counts_text(new: int, learn: int, review: int, sep: str = "   ") -> Text:
    text = Text()
    text.append(icon("new") + str(new), style=f"bold {NEW_COLOR}")
    text.append(sep)
    text.append(icon("learn") + str(learn), style=f"bold {LEARN_COLOR}")
    text.append(sep)
    text.append(icon("review") + str(review), style=f"bold {REVIEW_COLOR}")
    return text


def deck_query(col, did: int | None) -> str:
    deck = col.get_deck(did) if did else None
    return f'deck:"{escape_search_value(deck["name"])}"' if deck else ""


class DeckPickerScreen(Screen):
    BINDINGS = [
        Binding("enter", "study", "Study", show=True),
        Binding("v", "overview", "Overview", show=False),
        Binding("a", "add", "Add"),
        Binding("b", "browse", "Browse"),
        Binding("s", "stats", "Stats"),
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
        Binding("m", "manage", "More"),
        Binding("f5", "refresh", "Refresh", show=False),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="today-banner")
        with Horizontal(id="deck-main"):
            yield DataTable(id="deck-table", cursor_type="row", zebra_stripes=False)
            yield Static("", id="deck-info")
        yield Static("", id="deck-summary")
        yield Footer()

    @property
    def col(self):
        return self.app.col

    def on_mount(self) -> None:
        self.title = "Decks"
        self._setup_columns()
        self.refresh_decks()
        self.query_one(DataTable).focus()

    def _setup_columns(self) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        table.add_column(icon("deck") + "Deck", key="deck")
        table.add_column(Text(icon("new") + "New", justify="right", style=NEW_COLOR), key="new", width=7)
        table.add_column(Text(icon("learn") + "Learn", justify="right", style=LEARN_COLOR), key="learn", width=8)
        table.add_column(Text(icon("review") + "Due", justify="right", style=REVIEW_COLOR), key="due", width=7)

    def on_screen_resume(self) -> None:
        self.refresh_decks()

    def on_resize(self) -> None:
        self.query_one("#deck-info").display = self.size.width >= 100

    def refresh_view(self) -> None:
        """Called after a global appearance change (e.g. icons toggled)."""
        self._setup_columns()
        self.refresh_decks()

    def refresh_decks(self, select_did: int | None = None) -> None:
        table = self.query_one(DataTable)
        if select_did is None and table.row_count:
            select_did = self.selected_did()
        if select_did is None:
            select_did = self.col.current_deck_id()
        table.clear()
        self._rows: list[int] = []

        def add(node: DeckNode, depth: int) -> None:
            label = Text("  " * depth)
            if node.children:
                label.append("▸ " if node.collapsed else "▾ ", style="dim")
                ico = "folder" if node.collapsed else "folder_open"
            else:
                label.append("  ")
                ico = "deck"
            if node.filtered:
                ico = "filtered"
            label.append(icon(ico), style="cyan" if node.filtered else "gold3" if node.children else "grey70")
            label.append(node.name, style="italic cyan" if node.filtered else ("bold" if depth == 0 else ""))
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
                    add(child, depth + 1)

        tree = self.col.sched.deck_tree()
        for node in tree:
            add(node, 0)
        if select_did in self._rows:
            table.move_cursor(row=self._rows.index(select_did))
        self._update_banner(tree)
        self._update_summary()
        self.update_info()

    def _update_banner(self, tree: list[DeckNode]) -> None:
        stats = Stats(self.col)
        today = stats.today_stats()
        cal = stats.calendar(30)
        new = sum(n.new for n in tree)
        learn = sum(n.learn for n in tree)
        review = sum(n.review for n in tree)
        text = Text()
        text.append(icon("sun") + "Today: ", style="bold")
        if today.cards:
            text.append(f"{today.cards} cards in {today.time_ms / 60000:.0f} min")
            if today.correct_pct is not None:
                text.append(f" · {today.correct_pct:.0f}% correct", style="dim")
        else:
            text.append("nothing studied yet", style="dim")
        text.append("    ")
        if cal.current_streak:
            text.append(icon("fire") + f"{cal.current_streak}-day streak", style="bold orange1")
            text.append("    ")
        text.append("Due now: ", style="bold")
        text.append_text(counts_text(new, learn, review, sep="  "))
        self.query_one("#today-banner", Static).update(text)

    def _update_summary(self) -> None:
        undo = self.col.sched.undo_name()
        text = Text()
        text.append(icon("cards") + f"{self.col.card_count()} cards   ", style="dim")
        text.append(icon("notes") + f"{self.col.note_count()} notes", style="dim")
        if undo:
            text.append(f"   {icon('undo')}u: undo {undo}", style="yellow")
        if self.col.card_count() == 0:
            text.append("   Press ", style="dim")
            text.append("a", style="bold")
            text.append(" to add cards or ", style="dim")
            text.append("i", style="bold")
            text.append(" to import a deck (.apkg).", style="dim")
        self.query_one("#deck-summary", Static).update(text)

    def update_info(self) -> None:
        panel = self.query_one("#deck-info", Static)
        did = self.selected_did()
        deck = self.col.get_deck(did) if did is not None else None
        if deck is None:
            panel.update("")
            return
        panel.border_title = icon("filtered" if deck["dyn"] else "deck") + deck["name"].split("::")[-1]
        stats = Stats(self.col, did)
        counts = stats.card_counts()
        due = stats.future_due(2)
        today = stats.today_stats()
        width = max(panel.content_size.width or 30, 10)
        parts = [
            ("New", counts.new, NEW_COLOR),
            ("Learning", counts.learning + counts.relearning, "dark_orange"),
            ("Young", counts.young, "#a3e635"),
            ("Mature", counts.mature, "#16a34a"),
            ("Suspended", counts.suspended + counts.buried, "yellow3"),
        ]
        grid = Table.grid(padding=(0, 1))
        grid.add_column()
        grid.add_column(justify="right")
        for name, n, color in parts:
            grid.add_row(Text("■ ", style=color) + Text(name, style="dim"), str(n))
        lines = [Text(deck["name"], style="bold")]
        desc = deck.get("desc", "")
        if desc:
            from ..core.utils import strip_html

            lines.append(Text(strip_html(desc)[:200], style="italic dim"))
        lines.append(Text(""))
        lines.append(proportion_bar([(n, c) for _, n, c in parts], width))
        lines.append(grid)
        lines.append(Text(""))
        info = Text()
        info.append(icon("cards") + f"Total: {counts.total} cards\n")
        info.append(icon("calendar") + f"Due tomorrow: {due.tomorrow}\n")
        info.append(icon("sun") + f"Studied today: {today.cards}\n")
        if deck["dyn"]:
            info.append(icon("filtered") + "Filtered deck\n", style="cyan")
            for term in deck.get("terms", []):
                info.append(f"  {term[0] or '(all)'}\n", style="dim")
        else:
            conf = self.col.config_for_deck(did)
            info.append(icon("options") + f"Preset: {conf['name']}\n", style="dim")
        lines.append(info)
        panel.update(Group(*lines))

    def selected_did(self) -> int | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        try:
            return int(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        except Exception:
            return None

    # ------------------------------------------------------------ actions

    @on(DataTable.RowHighlighted)
    def row_highlighted(self) -> None:
        self.update_info()

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

        self.app.push_screen(AddNoteScreen(deck_id=self.selected_did()))

    def action_browse(self) -> None:
        from .browser import BrowserScreen

        self.app.push_screen(BrowserScreen(deck_query(self.col, self.selected_did())))

    def action_stats(self) -> None:
        from .stats import StatsScreen

        self.app.push_screen(StatsScreen(self.selected_did()))

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
            ("rename", icon("edit") + "Rename deck"),
            ("delete", icon("trash") + "Delete deck"),
            ("filtered", icon("filtered") + "Create filtered deck"),
            ("custom", icon("custom") + "Custom study"),
            ("unbury", icon("unbury") + "Unbury cards in deck"),
            ("notetypes", icon("notetype") + "Manage note types"),
            ("icons", icon("theme") + "Toggle Nerd Font icons"),
            ("help", icon("help") + "Keyboard shortcuts"),
            ("about", icon("info") + "About AnkiTUI"),
        ]
        if did is not None and self.col.get_deck(did)["dyn"]:
            choices = [
                ("rebuild", icon("rebuild") + "Rebuild filtered deck"),
                ("empty", icon("empty") + "Empty filtered deck"),
            ] + choices

        def done(key: str | None) -> None:
            if key == "rename":
                self.action_rename()
            elif key == "delete":
                self.action_delete()
            elif key == "filtered":
                self.action_new_filtered()
            elif key == "custom":
                self.action_custom_study()
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
            elif key == "icons":
                self.app.action_toggle_icons()
            elif key == "help":
                self.app.action_help()
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

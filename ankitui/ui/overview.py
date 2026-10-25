"""Deck overview / study options (AnkiDroid's StudyOptionsFragment)."""

from __future__ import annotations

from rich.align import Align
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from ..core.sched.scheduler import time_span
from ..core.stats import Stats
from ..core.utils import strip_html
from .charts import proportion_bar
from .deck_picker import LEARN_COLOR, NEW_COLOR, REVIEW_COLOR, deck_query
from .dialogs import ConfirmDialog, PromptDialog
from .icons import icon


def _tile(value: int, label: str, color: str, ico: str) -> Text:
    text = Text(justify="center")
    text.append(f"{value}\n", style=f"bold {color}" if value else "dim")
    text.append(icon(ico) + label, style=color if value else "dim")
    return text


class OverviewScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("s,enter", "study", "Study now"),
        Binding("c", "custom_study", "Custom study"),
        Binding("o", "options", "Options"),
        Binding("t", "stats", "Stats"),
        Binding("a", "add", "Add", show=False),
        Binding("b", "browse", "Browse", show=False),
        Binding("n", "rename", "Rename"),
        Binding("e", "export", "Export", show=False),
        Binding("delete,x", "delete", "Delete"),
        Binding("r", "rebuild", "Rebuild", show=False),
        Binding("E", "empty", "Empty", show=False),
        Binding("u", "unbury", "Unbury", show=False),
    ]

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="overview"):
            with Vertical(id="overview-card"):
                yield Static("", id="overview-title")
                yield Static("", id="overview-desc")
                with Horizontal(id="overview-tiles"):
                    yield Static("", id="tile-new", classes="tile")
                    yield Static("", id="tile-learn", classes="tile")
                    yield Static("", id="tile-review", classes="tile")
                yield Static("", id="overview-message")
                yield Static("", id="overview-info")
                with Horizontal(id="overview-buttons"):
                    yield Button("Study Now", variant="primary", id="study")
                    yield Button("Custom Study", id="custom")
                    yield Button("Rebuild", id="rebuild")
                    yield Button("Empty", id="empty")
                    yield Button("Unbury", id="unbury")
            with Vertical(id="deck-actions"):
                yield Static("", id="deck-actions-title")
                with Horizontal(classes="deck-actions-row"):
                    yield Button("Options", id="options")
                    yield Button("Add", id="add")
                    yield Button("Browse", id="browse")
                    yield Button("Stats", id="stats")
                with Horizontal(classes="deck-actions-row"):
                    yield Button("Rename", id="rename")
                    yield Button("Export", id="export")
                    yield Button("Delete", variant="error", id="delete")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_view()

    def on_screen_resume(self) -> None:
        self.refresh_view()

    def refresh_view(self) -> None:
        did = self.col.current_deck_id()
        deck = self.col.get_deck(did)
        self.title = "Overview"
        is_filtered = bool(deck["dyn"])
        title = Text(justify="center")
        title.append(icon("filtered" if is_filtered else "deck"), style="cyan" if is_filtered else "gold3")
        title.append(deck["name"], style="bold")
        self.query_one("#overview-title", Static).update(title)
        desc = strip_html(deck.get("desc", "") or "")
        self.query_one("#overview-desc", Static).update(Text(desc, style="italic dim", justify="center") if desc else "")
        self.query_one("#overview-desc").display = bool(desc)

        counts = self.col.sched.counts()
        self.query_one("#tile-new", Static).update(_tile(counts.new, "New", NEW_COLOR, "new"))
        self.query_one("#tile-learn", Static).update(_tile(counts.learning, "Learning", LEARN_COLOR, "learn"))
        self.query_one("#tile-review", Static).update(_tile(counts.review, "To Review", REVIEW_COLOR, "review"))

        stats = Stats(self.col, did)
        card_counts = stats.card_counts()
        today = stats.today_stats()
        info = self.col.sched.congrats_info()
        nothing_due = counts.new + counts.learning + counts.review == 0

        message = Text(justify="center")
        if nothing_due:
            if card_counts.total == 0:
                message.append(icon("info") + "This deck is empty. Press ", style="dim")
                message.append("a", style="bold")
                message.append(" to add cards.", style="dim")
            else:
                message.append(icon("trophy") + "Congratulations! You have finished this deck for now.\n", style="bold green3")
                extra = []
                if info.learn_remaining and info.secs_until_next_learn is not None:
                    extra.append(f"The next learning card will be ready in {time_span(info.secs_until_next_learn)}.")
                if info.have_sched_buried or info.have_user_buried:
                    extra.append("Some related or buried cards were delayed until tomorrow (u to unbury).")
                if not info.is_filtered and (info.new_remaining or info.review_remaining):
                    extra.append("Daily limits reached. Use Custom Study (c) to study more today.")
                message.append("\n".join(extra), style="dim")
        else:
            total = counts.new + counts.learning + counts.review
            message.append(f"{total} card{'s' if total != 1 else ''} to study", style="dim")
        self.query_one("#overview-message", Static).update(message)

        parts = [
            (card_counts.new, NEW_COLOR),
            (card_counts.learning + card_counts.relearning, "dark_orange"),
            (card_counts.young, "#a3e635"),
            (card_counts.mature, "#16a34a"),
            (card_counts.suspended + card_counts.buried, "yellow3"),
        ]
        info_text = Text()
        info_text.append_text(proportion_bar(parts, 60))
        info_text.append("\n")
        info_text.append(
            f"{icon('cards')}{card_counts.total} cards   {icon('new')}{card_counts.new} new   "
            f"{icon('review')}{card_counts.young + card_counts.mature} review   "
            f"{icon('suspend')}{card_counts.suspended} suspended\n",
            style="dim",
        )
        if today.cards:
            info_text.append(
                f"{icon('sun')}Studied {today.cards} cards in {today.time_ms / 60000:.1f} min today", style="dim"
            )
            if today.correct_pct is not None:
                info_text.append(f" ({today.correct_pct:.0f}% correct)", style="dim")
            info_text.append("\n")
        info_text.append(f"{icon('calendar')}Due tomorrow: {stats.future_due(2).tomorrow}", style="dim")
        self.query_one("#overview-info", Static).update(Align.center(info_text))

        study = self.query_one("#study", Button)
        study.label = icon("study") + "Study Now"
        study.disabled = nothing_due
        self.query_one("#custom", Button).label = icon("custom") + "Custom Study"
        for ident, ico, label in (
            ("options", "options", "Options"),
            ("add", "add", "Add Cards"),
            ("browse", "browse", "Browse"),
            ("stats", "stats", "Statistics"),
            ("rename", "edit", "Rename"),
            ("export", "export", "Export"),
            ("delete", "trash", "Delete"),
        ):
            self.query_one(f"#{ident}", Button).label = icon(ico) + label
        self.query_one("#deck-actions-title", Static).update(
            Text(icon("deck") + ("Filtered deck" if is_filtered else "Deck"), style="bold")
        )
        self.query_one("#rebuild", Button).label = icon("rebuild") + "Rebuild"
        self.query_one("#empty", Button).label = icon("empty") + "Empty"
        self.query_one("#unbury", Button).label = icon("unbury") + "Unbury"
        self.query_one("#custom", Button).display = not is_filtered
        self.query_one("#rebuild", Button).display = is_filtered
        self.query_one("#empty", Button).display = is_filtered
        self.query_one("#unbury", Button).display = info.have_sched_buried or info.have_user_buried
        if not nothing_due:
            study.focus()

    @on(Button.Pressed, "#study")
    def action_study(self) -> None:
        counts = self.col.sched.counts()
        if counts.new + counts.learning + counts.review == 0:
            self.notify("No cards are due right now.")
            return
        from .reviewer import ReviewerScreen

        self.app.push_screen(ReviewerScreen())

    @on(Button.Pressed, "#custom")
    def action_custom_study(self) -> None:
        did = self.col.current_deck_id()
        if self.col.get_deck(did)["dyn"]:
            return
        from .custom_study import CustomStudyScreen

        self.app.push_screen(CustomStudyScreen(did), lambda _: self.refresh_view())

    @on(Button.Pressed, "#options")
    def action_options(self) -> None:
        did = self.col.current_deck_id()
        if self.col.get_deck(did)["dyn"]:
            from .filtered_deck import FilteredDeckScreen

            self.app.push_screen(FilteredDeckScreen(did), lambda _: self.refresh_view())
        else:
            from .deck_options import DeckOptionsScreen

            self.app.push_screen(DeckOptionsScreen(did), lambda _: self.refresh_view())

    @on(Button.Pressed, "#rebuild")
    def action_rebuild(self) -> None:
        did = self.col.current_deck_id()
        if self.col.get_deck(did)["dyn"]:
            n = self.col.sched.rebuild_filtered_deck(did)
            self.notify(f"Rebuilt filtered deck: {n} cards")
            self.refresh_view()

    @on(Button.Pressed, "#empty")
    def action_empty(self) -> None:
        did = self.col.current_deck_id()
        if self.col.get_deck(did)["dyn"]:
            self.col.sched.empty_filtered_deck(did)
            self.notify("Filtered deck emptied")
            self.refresh_view()

    @on(Button.Pressed, "#unbury")
    def action_unbury(self) -> None:
        self.col.sched.unbury_deck(self.col.current_deck_id())
        self.refresh_view()

    @on(Button.Pressed, "#stats")
    def action_stats(self) -> None:
        from .stats import StatsScreen

        self.app.push_screen(StatsScreen(self.col.current_deck_id()))

    @on(Button.Pressed, "#add")
    def action_add(self) -> None:
        from .note_editor import AddNoteScreen

        self.app.push_screen(AddNoteScreen(deck_id=self.col.current_deck_id()))

    @on(Button.Pressed, "#browse")
    def action_browse(self) -> None:
        from .browser import BrowserScreen

        self.app.push_screen(BrowserScreen(deck_query(self.col, self.col.current_deck_id())))

    @on(Button.Pressed, "#rename")
    def action_rename(self) -> None:
        did = self.col.current_deck_id()
        deck = self.col.get_deck(did)

        def done(name: str | None) -> None:
            if not name or name == deck["name"]:
                return
            try:
                self.col.rename_deck(did, name)
            except ValueError as err:
                self.notify(str(err), severity="error")
                return
            self.refresh_view()

        self.app.push_screen(PromptDialog("Rename Deck", "New name (use :: for subdecks):", deck["name"]), done)

    @on(Button.Pressed, "#export")
    def action_export(self) -> None:
        from .import_export import ExportScreen

        self.app.push_screen(ExportScreen(self.col.current_deck_id()))

    @on(Button.Pressed, "#delete")
    def action_delete(self) -> None:
        did = self.col.current_deck_id()
        deck = self.col.get_deck(did)
        if deck["dyn"]:
            msg = f"Delete filtered deck '{deck['name']}'? Its cards will return to their original decks."
        else:
            ids = ",".join(str(d) for d in self.col.child_ids(did, include_self=True))
            n = self.col.db.execute(f"SELECT count() FROM cards WHERE did IN ({ids}) OR odid IN ({ids})").fetchone()[0]
            msg = f"Delete deck '{deck['name']}' including its subdecks and {n} card(s)? This cannot be undone."

        def done(ok: bool) -> None:
            if ok:
                self.col.remove_deck(did)
                self.notify(f"Deleted {deck['name']}")
                self.app.pop_screen()

        self.app.push_screen(ConfirmDialog("Delete Deck", msg, yes="Delete", danger=True), done)

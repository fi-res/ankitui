"""Deck overview / study options (AnkiDroid's StudyOptionsFragment)."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from ..core.sched.scheduler import time_span
from ..core.utils import strip_html
from .deck_picker import LEARN_COLOR, NEW_COLOR, REVIEW_COLOR


class OverviewScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("s,enter", "study", "Study now"),
        Binding("c", "custom_study", "Custom study"),
        Binding("o", "options", "Options"),
        Binding("r", "rebuild", "Rebuild", show=False),
        Binding("e", "empty", "Empty", show=False),
        Binding("u", "unbury", "Unbury", show=False),
        Binding("a", "add", "Add"),
        Binding("b", "browse", "Browse"),
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
                yield Static("", id="overview-counts")
                yield Static("", id="overview-info")
                with Horizontal(id="overview-buttons"):
                    yield Button("Study Now", variant="primary", id="study")
                    yield Button("Custom Study", id="custom")
                    yield Button("Options", id="options")
                    yield Button("Rebuild", id="rebuild")
                    yield Button("Empty", id="empty")
                    yield Button("Unbury", id="unbury")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_view()

    def on_screen_resume(self) -> None:
        self.refresh_view()

    def refresh_view(self) -> None:
        did = self.col.current_deck_id()
        deck = self.col.get_deck(did)
        self.query_one("#overview-title", Static).update(deck["name"])
        desc = strip_html(deck.get("desc", "") or "")
        self.query_one("#overview-desc", Static).update(Text(desc, style="italic") if desc else "")
        counts = self.col.sched.counts()
        text = Text(justify="center")
        text.append("New: ")
        text.append(str(counts.new), style=f"bold {NEW_COLOR}")
        text.append("    Learning: ")
        text.append(str(counts.learning), style=f"bold {LEARN_COLOR}")
        text.append("    To Review: ")
        text.append(str(counts.review), style=f"bold {REVIEW_COLOR}")
        self.query_one("#overview-counts", Static).update(text)

        ids = ",".join(str(d) for d in self.col.child_ids(did, include_self=True))
        total = self.col.db.execute(f"SELECT count() FROM cards WHERE did IN ({ids})").fetchone()[0]
        total_new = self.col.db.execute(f"SELECT count() FROM cards WHERE did IN ({ids}) AND queue = 0").fetchone()[0]
        info = self.col.sched.congrats_info()
        lines = [f"Total new cards: {total_new}", f"Total cards: {total}"]
        nothing_due = counts.new + counts.learning + counts.review == 0
        if nothing_due:
            lines.insert(0, "")
            if total == 0:
                lines.insert(0, "This deck is empty. Press 'a' to add cards.")
            else:
                lines.insert(0, "Congratulations! You have finished this deck for now.")
                if info.learn_remaining and info.secs_until_next_learn is not None:
                    lines.insert(
                        1,
                        f"The next learning card will be ready in {time_span(info.secs_until_next_learn)}.",
                    )
                if info.have_sched_buried or info.have_user_buried:
                    lines.insert(1, "Some related or buried cards were delayed until tomorrow (u to unbury).")
                if not info.is_filtered and (info.new_remaining or info.review_remaining):
                    lines.insert(1, "Daily limits reached. Use Custom Study (c) to study more today.")
        self.query_one("#overview-info", Static).update("\n".join(lines))
        is_filtered = bool(deck["dyn"])
        self.query_one("#study", Button).disabled = nothing_due
        self.query_one("#custom", Button).display = not is_filtered
        self.query_one("#rebuild", Button).display = is_filtered
        self.query_one("#empty", Button).display = is_filtered
        self.query_one("#unbury", Button).display = info.have_sched_buried or info.have_user_buried

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

    def action_add(self) -> None:
        from .note_editor import AddNoteScreen

        self.app.push_screen(AddNoteScreen(deck_id=self.col.current_deck_id()))

    def action_browse(self) -> None:
        from .browser import BrowserScreen

        deck = self.col.get_deck(self.col.current_deck_id())
        self.app.push_screen(BrowserScreen(f'deck:"{deck["name"]}"'))

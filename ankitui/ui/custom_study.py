"""Custom study (AnkiDroid's CustomStudyDialog)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Select, SelectionList, Static

from ..core.sched.scheduler import CRAM_ALL, CRAM_DUE, CRAM_NEW, CRAM_REVIEW, CustomStudyError

OPTIONS = [
    ("new_limit", "Increase today's new card limit"),
    ("review_limit", "Increase today's review card limit"),
    ("forgot", "Review forgotten cards"),
    ("ahead", "Review ahead"),
    ("preview", "Preview new cards"),
    ("cram", "Study by card state or tag"),
]

PROMPTS = {
    "new_limit": "Increase/decrease (e.g. -3) today's new card limit by:",
    "review_limit": "Increase/decrease (e.g. -3) today's review limit by:",
    "forgot": "Review cards forgotten in the last x days:",
    "ahead": "Review cards due in the next x days:",
    "preview": "Preview new cards added in the last x days:",
    "cram": "Select x cards from the deck:",
}

CRAM_KINDS = [
    ("New cards only", CRAM_NEW),
    ("Due cards only", CRAM_DUE),
    ("All review cards in random order", CRAM_REVIEW),
    ("All cards in random order (don't reschedule)", CRAM_ALL),
]


class CustomStudyScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, did: int):
        super().__init__()
        self.did = did

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        deck = self.col.get_deck(self.did)
        self.defaults = self.col.sched.custom_study_defaults(self.did)
        with Vertical(classes="dialog dialog-wide"):
            yield Label(f"Custom Study: {deck['name']}", classes="dialog-title")
            with RadioSet(id="cs-option"):
                for i, (key, label) in enumerate(OPTIONS):
                    yield RadioButton(label, value=(i == 0), id=f"cs-{key}")
            yield Static("", id="cs-available")
            yield Label("", id="cs-prompt")
            yield Input("", id="cs-value", restrict=r"-?\d*")
            yield Select(CRAM_KINDS, allow_blank=False, value=CRAM_NEW, id="cs-cram-kind")
            with Horizontal(id="cs-tags"):
                with Vertical():
                    yield Label("Require one or more of these tags:")
                    yield SelectionList(id="cs-include")
                with Vertical():
                    yield Label("Exclude cards with these tags:")
                    yield SelectionList(id="cs-exclude")
            with Horizontal(classes="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        tags = self.col.tags_in_decks(self.col.child_ids(self.did, include_self=True))
        inc_saved = set(self.col.conf.get(f"customStudyIncludeTags_{self.did}", []))
        exc_saved = set(self.col.conf.get(f"customStudyExcludeTags_{self.did}", []))
        self.query_one("#cs-include", SelectionList).add_options([(t, t, t in inc_saved) for t in tags])
        self.query_one("#cs-exclude", SelectionList).add_options([(t, t, t in exc_saved) for t in tags])
        self.select_option("new_limit")
        self.query_one(RadioSet).focus()

    def current_option(self) -> str:
        pressed = self.query_one(RadioSet).pressed_button
        if pressed is None or not pressed.id:
            return "new_limit"
        return pressed.id[3:]

    def select_option(self, key: str) -> None:
        self.query_one("#cs-prompt", Label).update(PROMPTS[key])
        value = {
            "new_limit": self.defaults["extend_new"],
            "review_limit": self.defaults["extend_review"],
            "forgot": self.col.conf.get("customStudyForgotDays", 1),
            "ahead": self.col.conf.get("customStudyAheadDays", 1),
            "preview": self.col.conf.get("customStudyPreviewDays", 1),
            "cram": self.col.conf.get("customStudyCramCards", 100),
        }[key]
        self.query_one("#cs-value", Input).value = str(value)
        available = ""
        if key == "new_limit":
            available = f"Available new cards: {self.defaults['available_new']}"
        elif key == "review_limit":
            available = f"Available review cards: {self.defaults['available_review']}"
        self.query_one("#cs-available", Static).update(available)
        is_cram = key == "cram"
        self.query_one("#cs-cram-kind", Select).display = is_cram
        self.query_one("#cs-tags").display = is_cram

    @on(RadioSet.Changed)
    def option_changed(self) -> None:
        self.select_option(self.current_option())

    @on(Input.Submitted, "#cs-value")
    @on(Button.Pressed, "#ok")
    def submit(self) -> None:
        key = self.current_option()
        raw = self.query_one("#cs-value", Input).value.strip()
        try:
            value = int(raw)
        except ValueError:
            self.notify("Please enter a number.", severity="error")
            return
        if key not in ("new_limit", "review_limit") and value < 1:
            self.notify("The minimum value is 1.", severity="error")
            return
        include = list(self.query_one("#cs-include", SelectionList).selected)
        exclude = list(self.query_one("#cs-exclude", SelectionList).selected)
        try:
            did = self.col.sched.custom_study(
                self.did,
                key,
                value,
                cram_kind=self.query_one("#cs-cram-kind", Select).value,
                tags_include=include,
                tags_exclude=exclude,
            )
        except CustomStudyError as err:
            self.notify(str(err), severity="error")
            return
        conf_key = {
            "forgot": "customStudyForgotDays",
            "ahead": "customStudyAheadDays",
            "preview": "customStudyPreviewDays",
            "cram": "customStudyCramCards",
        }.get(key)
        if conf_key:
            self.col.conf[conf_key] = value
            self.col.save()
        if did is None:
            self.notify("Limits extended for today.")
            self.dismiss("limits")
        else:
            self.notify("Custom study session created.")
            self.dismiss("study")

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

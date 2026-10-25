"""Deck options (presets) editor."""

from __future__ import annotations

import copy
import re

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, Select, Static

from ..core import consts
from ..core.deckconf import LIMITS
from .dialogs import ConfirmDialog, PromptDialog

_STEP_RE = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")
_UNIT_MINUTES = {"s": 1 / 60, "m": 1, "": 1, "h": 60, "d": 1440}


def parse_steps(text: str) -> list[float]:
    steps = []
    for part in text.replace(",", " ").split():
        m = _STEP_RE.match(part.strip().lower())
        if not m:
            raise ValueError(f"Invalid step '{part}'. Use e.g. 1m 10m 1h 1d")
        value = float(m.group(1)) * _UNIT_MINUTES[m.group(2)]
        if value <= 0:
            raise ValueError("Steps must be greater than zero")
        steps.append(round(value, 4))
    return steps


def format_steps(steps: list[float]) -> str:
    out = []
    for s in steps:
        if s >= 1440 and s % 1440 == 0:
            out.append(f"{int(s // 1440)}d")
        elif s >= 60 and s % 60 == 0:
            out.append(f"{int(s // 60)}h")
        elif s < 1:
            out.append(f"{round(s * 60)}s")
        else:
            out.append(f"{s:g}m")
    return " ".join(out)


GATHER = [
    ("Deck", consts.NEW_GATHER_DECK),
    ("Deck, then random notes", consts.NEW_GATHER_DECK_THEN_RANDOM_NOTES),
    ("Ascending position", consts.NEW_GATHER_LOWEST_POSITION),
    ("Descending position", consts.NEW_GATHER_HIGHEST_POSITION),
    ("Random notes", consts.NEW_GATHER_RANDOM_NOTES),
    ("Random cards", consts.NEW_GATHER_RANDOM_CARDS),
]
SORT = [
    ("Card type, then order gathered", consts.NEW_SORT_TEMPLATE),
    ("Order gathered", consts.NEW_SORT_NO_SORT),
    ("Card type, then random", consts.NEW_SORT_TEMPLATE_THEN_RANDOM),
    ("Random note, then card type", consts.NEW_SORT_RANDOM_NOTE_THEN_TEMPLATE),
    ("Random", consts.NEW_SORT_RANDOM_CARD),
]
MIX = [
    ("Mix with reviews", consts.MIX_WITH_REVIEWS),
    ("Show after reviews", consts.MIX_AFTER_REVIEWS),
    ("Show before reviews", consts.MIX_BEFORE_REVIEWS),
]
REVIEW_ORDER = [
    ("Due date, then random", consts.REVIEW_ORDER_DAY),
    ("Due date, then deck", consts.REVIEW_ORDER_DAY_THEN_DECK),
    ("Deck, then due date", consts.REVIEW_ORDER_DECK_THEN_DAY),
    ("Ascending intervals", consts.REVIEW_ORDER_INTERVALS_ASCENDING),
    ("Descending intervals", consts.REVIEW_ORDER_INTERVALS_DESCENDING),
    ("Ascending ease", consts.REVIEW_ORDER_EASE_ASCENDING),
    ("Descending ease", consts.REVIEW_ORDER_EASE_DESCENDING),
    ("Relative overdueness", consts.REVIEW_ORDER_RELATIVE_OVERDUENESS),
    ("Random", consts.REVIEW_ORDER_RANDOM),
    ("Order added", consts.REVIEW_ORDER_ADDED),
    ("Reverse order added", consts.REVIEW_ORDER_REVERSE_ADDED),
]

# (option id, label, getter, kind)
NUMBER_FIELDS = [
    ("new_per_day", "New cards/day", lambda c: c["new"]["perDay"], "int"),
    ("reviews_per_day", "Maximum reviews/day", lambda c: c["rev"]["perDay"], "int"),
    ("graduating_interval_good", "Graduating interval (days)", lambda c: c["new"]["ints"][0], "int"),
    ("graduating_interval_easy", "Easy interval (days)", lambda c: c["new"]["ints"][1], "int"),
    ("minimum_lapse_interval", "Minimum interval (days)", lambda c: c["lapse"]["minInt"], "int"),
    ("leech_threshold", "Leech threshold (lapses)", lambda c: c["lapse"]["leechFails"], "int"),
    ("maximum_review_interval", "Maximum interval (days)", lambda c: c["rev"]["maxIvl"], "int"),
    ("initial_ease", "Starting ease", lambda c: c["new"]["initialFactor"] / 1000, "float"),
    ("easy_multiplier", "Easy bonus", lambda c: c["rev"]["ease4"], "float"),
    ("interval_multiplier", "Interval modifier", lambda c: c["rev"]["ivlFct"], "float"),
    ("hard_multiplier", "Hard interval", lambda c: c["rev"].get("hardFactor", 1.2), "float"),
    ("lapse_multiplier", "New interval (after lapse)", lambda c: c["lapse"]["mult"], "float"),
    ("cap_answer_time_to_secs", "Maximum answer seconds", lambda c: c.get("maxTaken", 60), "int"),
]


def _apply_number(conf: dict, key: str, value) -> None:
    if key == "new_per_day":
        conf["new"]["perDay"] = value
    elif key == "reviews_per_day":
        conf["rev"]["perDay"] = value
    elif key == "graduating_interval_good":
        conf["new"]["ints"] = [value, conf["new"]["ints"][1], 0]
    elif key == "graduating_interval_easy":
        conf["new"]["ints"] = [conf["new"]["ints"][0], value, 0]
    elif key == "minimum_lapse_interval":
        conf["lapse"]["minInt"] = value
    elif key == "leech_threshold":
        conf["lapse"]["leechFails"] = value
    elif key == "maximum_review_interval":
        conf["rev"]["maxIvl"] = value
    elif key == "initial_ease":
        conf["new"]["initialFactor"] = int(round(value * 1000))
    elif key == "easy_multiplier":
        conf["rev"]["ease4"] = value
    elif key == "interval_multiplier":
        conf["rev"]["ivlFct"] = value
    elif key == "hard_multiplier":
        conf["rev"]["hardFactor"] = value
    elif key == "lapse_multiplier":
        conf["lapse"]["mult"] = value
    elif key == "cap_answer_time_to_secs":
        conf["maxTaken"] = value


class DeckOptionsScreen(Screen):
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, did: int):
        super().__init__()
        self.did = did

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        deck = self.col.get_deck(self.did)
        self.conf = copy.deepcopy(self.col.config_for_deck(self.did))
        yield Header()
        with VerticalScroll(id="options"):
            yield Static(f"Options for deck: [b]{deck['name']}[/b]")
            with Horizontal(classes="opt-row"):
                yield Label("Preset")
                yield Select(self._preset_options(), allow_blank=False, value=int(self.conf["id"]), id="preset")
            with Horizontal(classes="opt-row"):
                yield Button("Add preset", id="add-preset")
                yield Button("Clone preset", id="clone-preset")
                yield Button("Rename preset", id="rename-preset")
                yield Button("Delete preset", id="delete-preset")
            yield Static("", id="preset-usage", classes="opt-help")

            yield Label("Daily Limits", classes="opt-section")
            yield from self._number_row("new_per_day")
            yield from self._number_row("reviews_per_day")
            with Horizontal(classes="opt-row"):
                yield Label("New cards ignore review limit")
                yield Checkbox("(applies to all decks)", bool(self.col.conf.get("newCardsIgnoreReviewLimit")), id="ignore-review-limit")
            with Horizontal(classes="opt-row"):
                yield Label("Apply limits of all parent decks")
                yield Checkbox("(applies to all decks)", bool(self.col.conf.get("applyAllParentLimits")), id="parent-limits")

            yield Label("New Cards", classes="opt-section")
            with Horizontal(classes="opt-row"):
                yield Label("Learning steps")
                yield Input(id="learn-steps")
            yield from self._number_row("graduating_interval_good")
            yield from self._number_row("graduating_interval_easy")
            with Horizontal(classes="opt-row"):
                yield Label("Insertion order")
                yield Select([("Sequential", 1), ("Random", 0)], allow_blank=False, id="insert-order")

            yield Label("Lapses", classes="opt-section")
            with Horizontal(classes="opt-row"):
                yield Label("Relearning steps")
                yield Input(id="relearn-steps")
            yield from self._number_row("minimum_lapse_interval")
            yield from self._number_row("leech_threshold")
            with Horizontal(classes="opt-row"):
                yield Label("Leech action")
                yield Select([("Tag only", consts.LEECH_TAG_ONLY), ("Suspend card", consts.LEECH_SUSPEND)], allow_blank=False, id="leech-action")

            yield Label("Display Order", classes="opt-section")
            for ident, label, options in (
                ("gather", "New card gather order", GATHER),
                ("sort", "New card sort order", SORT),
                ("new-mix", "New/review order", MIX),
                ("day-mix", "Interday learning/review order", MIX),
                ("review-order", "Review sort order", REVIEW_ORDER),
            ):
                with Horizontal(classes="opt-row"):
                    yield Label(label)
                    yield Select(options, allow_blank=False, id=ident)

            yield Label("Burying", classes="opt-section")
            yield Checkbox("Bury new siblings", id="bury-new")
            yield Checkbox("Bury review siblings", id="bury-review")
            yield Checkbox("Bury interday learning siblings", id="bury-interday")

            yield Label("Advanced", classes="opt-section")
            for key in (
                "maximum_review_interval",
                "initial_ease",
                "easy_multiplier",
                "interval_multiplier",
                "hard_multiplier",
                "lapse_multiplier",
                "cap_answer_time_to_secs",
            ):
                yield from self._number_row(key)

            yield Label("Deck", classes="opt-section")
            with Horizontal(classes="opt-row"):
                yield Label("Description")
                yield Input(deck.get("desc", ""), id="deck-desc")

            with Horizontal(classes="opt-row"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")
        yield Footer()

    def _preset_options(self) -> list[tuple[str, int]]:
        out = []
        for c in self.col.all_configs():
            n = len(self.col.decks_using_config(int(c["id"])))
            out.append((f"{c['name']} (used by {n} deck{'s' if n != 1 else ''})", int(c["id"])))
        return out

    def _number_row(self, key: str):
        label = next(f[1] for f in NUMBER_FIELDS if f[0] == key)
        lo, hi, _ = LIMITS[key]
        with Horizontal(classes="opt-row"):
            yield Label(f"{label} [{lo}-{hi}]")
            yield Input(id=f"num-{key}", restrict=r"[0-9.]*")

    def on_mount(self) -> None:
        self.title = "Deck Options"
        self.load_conf(self.conf)

    def load_conf(self, conf: dict) -> None:
        self.conf = copy.deepcopy(conf)
        for key, _label, getter, kind in NUMBER_FIELDS:
            value = getter(conf)
            self.query_one(f"#num-{key}", Input).value = (
                str(int(value)) if kind == "int" else f"{float(value):g}"
            )
        self.query_one("#learn-steps", Input).value = format_steps(conf["new"]["delays"])
        self.query_one("#relearn-steps", Input).value = format_steps(conf["lapse"]["delays"])
        self.query_one("#insert-order", Select).value = 0 if conf["new"].get("order", 1) == 0 else 1
        self.query_one("#leech-action", Select).value = conf["lapse"].get("leechAction", 1)
        self.query_one("#gather", Select).value = conf.get("newGatherPriority", 0)
        self.query_one("#sort", Select).value = conf.get("newSortOrder", 0)
        self.query_one("#new-mix", Select).value = conf.get("newMix", 0)
        self.query_one("#day-mix", Select).value = conf.get("interdayLearningMix", 0)
        self.query_one("#review-order", Select).value = conf.get("reviewOrder", 0)
        self.query_one("#bury-new", Checkbox).value = bool(conf["new"].get("bury"))
        self.query_one("#bury-review", Checkbox).value = bool(conf["rev"].get("bury"))
        self.query_one("#bury-interday", Checkbox).value = bool(conf.get("buryInterdayLearning"))
        n = len(self.col.decks_using_config(int(conf["id"])))
        self.query_one("#preset-usage", Static).update(
            f"Changes to this preset affect {n} deck{'s' if n != 1 else ''}."
        )

    def gather_conf(self) -> dict:
        conf = copy.deepcopy(self.conf)
        for key, label, _getter, kind in NUMBER_FIELDS:
            raw = self.query_one(f"#num-{key}", Input).value.strip()
            try:
                value = int(raw) if kind == "int" else float(raw)
            except ValueError as err:
                raise ValueError(f"{label}: '{raw}' is not a valid number") from err
            lo, hi, _ = LIMITS[key]
            if value < lo or value > hi:
                raise ValueError(f"{label} must be between {lo} and {hi}")
            _apply_number(conf, key, value)
        conf["new"]["delays"] = parse_steps(self.query_one("#learn-steps", Input).value)
        conf["lapse"]["delays"] = parse_steps(self.query_one("#relearn-steps", Input).value)
        conf["new"]["order"] = self.query_one("#insert-order", Select).value
        conf["lapse"]["leechAction"] = self.query_one("#leech-action", Select).value
        conf["newGatherPriority"] = self.query_one("#gather", Select).value
        conf["newSortOrder"] = self.query_one("#sort", Select).value
        conf["newMix"] = self.query_one("#new-mix", Select).value
        conf["interdayLearningMix"] = self.query_one("#day-mix", Select).value
        conf["reviewOrder"] = self.query_one("#review-order", Select).value
        conf["new"]["bury"] = self.query_one("#bury-new", Checkbox).value
        conf["rev"]["bury"] = self.query_one("#bury-review", Checkbox).value
        conf["buryInterdayLearning"] = self.query_one("#bury-interday", Checkbox).value
        return conf

    def _refresh_presets(self, select_id: int) -> None:
        sel = self.query_one("#preset", Select)
        sel.set_options(self._preset_options())
        sel.value = select_id

    @on(Select.Changed, "#preset")
    def preset_changed(self, event: Select.Changed) -> None:
        if event.value is Select.NULL or int(event.value) == int(self.conf["id"]):
            return
        conf = self.col.dconf.get(str(event.value))
        if conf:
            self.load_conf(conf)

    @on(Button.Pressed, "#add-preset")
    def add_preset(self) -> None:
        def done(name: str | None) -> None:
            if name:
                conf = self.col.add_config(name)
                self._refresh_presets(int(conf["id"]))
                self.load_conf(conf)

        self.app.push_screen(PromptDialog("Add Preset", "Name:"), done)

    @on(Button.Pressed, "#clone-preset")
    def clone_preset(self) -> None:
        def done(name: str | None) -> None:
            if name:
                try:
                    base = self.gather_conf()
                except ValueError as err:
                    self.notify(str(err), severity="error")
                    return
                conf = self.col.add_config(name, base)
                self._refresh_presets(int(conf["id"]))
                self.load_conf(conf)

        self.app.push_screen(PromptDialog("Clone Preset", "Name:", self.conf["name"] + " copy"), done)

    @on(Button.Pressed, "#rename-preset")
    def rename_preset(self) -> None:
        def done(name: str | None) -> None:
            if name:
                self.conf["name"] = name
                stored = self.col.dconf[str(self.conf["id"])]
                stored["name"] = name
                self.col.save_config(stored)
                self._refresh_presets(int(self.conf["id"]))

        self.app.push_screen(PromptDialog("Rename Preset", "Name:", self.conf["name"]), done)

    @on(Button.Pressed, "#delete-preset")
    def delete_preset(self) -> None:
        cid = int(self.conf["id"])
        if cid == consts.DEFAULT_CONF_ID:
            self.notify("The default preset can't be removed.", severity="error")
            return

        def done(ok: bool) -> None:
            if ok:
                self.col.remove_config(cid)
                self._refresh_presets(1)
                self.load_conf(self.col.dconf["1"])

        n = len(self.col.decks_using_config(cid))
        self.app.push_screen(
            ConfirmDialog("Delete Preset", f"Delete preset '{self.conf['name']}'? {n} deck(s) will revert to Default.", danger=True, yes="Delete"),
            done,
        )

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        try:
            conf = self.gather_conf()
        except ValueError as err:
            self.notify(str(err), severity="error")
            return
        self.col.save_config(conf)
        deck = self.col.get_deck(self.did)
        deck["conf"] = int(conf["id"])
        deck["desc"] = self.query_one("#deck-desc", Input).value
        self.col.conf["newCardsIgnoreReviewLimit"] = self.query_one("#ignore-review-limit", Checkbox).value
        self.col.conf["applyAllParentLimits"] = self.query_one("#parent-limits", Checkbox).value
        self.col.save_deck(deck)
        self.col.sched.clear_queues()
        self.notify("Options saved")
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_close(self) -> None:
        self.dismiss(False)

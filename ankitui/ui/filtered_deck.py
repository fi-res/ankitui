"""Creating and editing filtered decks."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select

from ..core import consts
from ..core.search import SearchError, escape_search_value

ORDERS = [(label, key) for key, label in consts.DYN_ORDER_LABELS.items()]


class FilteredDeckScreen(ModalScreen[int | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "build", "Build")]

    def __init__(self, did: int | None = None):
        super().__init__()
        self.did = did

    @property
    def col(self):
        return self.app.col

    def _defaults(self) -> dict:
        if self.did is not None:
            deck = self.col.get_deck(self.did)
            terms = deck.get("terms") or [["", 100, consts.DYN_RANDOM]]
            return {
                "name": deck["name"],
                "terms": terms,
                "resched": deck.get("resched", True),
                "preview": (deck.get("previewAgainSecs", 60), deck.get("previewHardSecs", 600), deck.get("previewGoodSecs", 0)),
            }
        current = self.col.get_deck(self.col.current_deck_id())
        search = ""
        if current and not current["dyn"]:
            search = f'deck:"{escape_search_value(current["name"])}"'
        n = 1
        while self.col.deck_id_by_name(f"Filtered Deck {n}") is not None:
            n += 1
        return {
            "name": f"Filtered Deck {n}",
            "terms": [[f"{search} is:due".strip(), 100, consts.DYN_RANDOM], [f"{search} is:new".strip(), 20, consts.DYN_DUE]],
            "resched": True,
            "preview": (60, 600, 0),
        }

    def compose(self) -> ComposeResult:
        d = self._defaults()
        t1 = d["terms"][0]
        t2 = d["terms"][1] if len(d["terms"]) > 1 else ["", 20, consts.DYN_DUE]
        with Vertical(classes="dialog dialog-wide"):
            yield Label("Edit Filtered Deck" if self.did else "Create Filtered Deck", classes="dialog-title")
            with VerticalScroll():
                with Horizontal(classes="form-row"):
                    yield Label("Name")
                    yield Input(d["name"], id="fd-name")
                yield Label("Filter", classes="opt-section")
                with Horizontal(classes="form-row"):
                    yield Label("Search")
                    yield Input(t1[0], id="fd-search")
                with Horizontal(classes="form-row"):
                    yield Label("Limit to")
                    yield Input(str(t1[1]), id="fd-limit", restrict=r"\d*")
                with Horizontal(classes="form-row"):
                    yield Label("Cards selected by")
                    yield Select(ORDERS, allow_blank=False, value=int(t1[2]), id="fd-order")
                yield Checkbox("Enable second filter", len(d["terms"]) > 1, id="fd-second")
                with Vertical(id="fd-second-box"):
                    with Horizontal(classes="form-row"):
                        yield Label("Search")
                        yield Input(t2[0], id="fd-search2")
                    with Horizontal(classes="form-row"):
                        yield Label("Limit to")
                        yield Input(str(t2[1]), id="fd-limit2", restrict=r"\d*")
                    with Horizontal(classes="form-row"):
                        yield Label("Cards selected by")
                        yield Select(ORDERS, allow_blank=False, value=int(t2[2]), id="fd-order2")
                yield Label("Options", classes="opt-section")
                yield Checkbox("Reschedule cards based on my answers in this deck", bool(d["resched"]), id="fd-resched")
                with Vertical(id="fd-preview-box"):
                    yield Label("Preview delays when not rescheduling (seconds, 0 = return to deck):")
                    with Horizontal(classes="form-row"):
                        yield Label("Again")
                        yield Input(str(d["preview"][0]), id="fd-again", restrict=r"\d*")
                    with Horizontal(classes="form-row"):
                        yield Label("Hard")
                        yield Input(str(d["preview"][1]), id="fd-hard", restrict=r"\d*")
                    with Horizontal(classes="form-row"):
                        yield Label("Good")
                        yield Input(str(d["preview"][2]), id="fd-good", restrict=r"\d*")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Rebuild" if self.did else "Build", variant="primary", id="build")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._update_visibility()
        self.query_one("#fd-search", Input).focus()

    @on(Checkbox.Changed)
    def _update_visibility(self) -> None:
        self.query_one("#fd-second-box").display = self.query_one("#fd-second", Checkbox).value
        self.query_one("#fd-preview-box").display = not self.query_one("#fd-resched", Checkbox).value

    def _int(self, ident: str, default: int) -> int:
        raw = self.query_one(ident, Input).value.strip()
        return int(raw) if raw.isdigit() else default

    @on(Button.Pressed, "#build")
    def action_build(self) -> None:
        name = self.query_one("#fd-name", Input).value.strip()
        if not name:
            self.notify("Please enter a name.", severity="error")
            return
        if self.did is None and self.col.deck_id_by_name(name) is not None:
            self.notify("A deck with that name already exists.", severity="error")
            return
        second = None
        if self.query_one("#fd-second", Checkbox).value:
            second = (
                self.query_one("#fd-search2", Input).value,
                self._int("#fd-limit2", 20),
                self.query_one("#fd-order2", Select).value,
            )
        try:
            did, count = self.col.sched.create_filtered_deck(
                name,
                self.query_one("#fd-search", Input).value,
                self._int("#fd-limit", 100),
                self.query_one("#fd-order", Select).value,
                self.query_one("#fd-resched", Checkbox).value,
                search2=second,
                preview_secs=(self._int("#fd-again", 60), self._int("#fd-hard", 600), self._int("#fd-good", 0)),
                existing_id=self.did,
            )
        except (SearchError, ValueError) as err:
            self.notify(str(err), severity="error")
            return
        if count == 0:
            self.notify("No cards matched the criteria you provided.", severity="warning")
            if self.did is None:
                return
        else:
            self.notify(f"Filtered deck built with {count} cards.")
        self.col.set_current_deck(did)
        self.dismiss(did)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

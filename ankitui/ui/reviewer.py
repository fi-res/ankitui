"""The study screen (AnkiDroid's Reviewer)."""

from __future__ import annotations

import html
import time
from datetime import datetime

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Static

from ..core import consts
from ..core.sched.queue import KIND_DAY_LEARN, KIND_LEARN, KIND_NEW, KIND_REVIEW
from ..core.sched.scheduler import QueuedCard, time_span
from ..core.sched.states import is_leeched
from ..core.template import CLOZE_RE, TYPE_ANSWER_MARKER, cloze_answer_text, compare_typed_answer
from ..core.utils import strip_html
from .deck_picker import LEARN_COLOR, NEW_COLOR, REVIEW_COLOR
from .dialogs import ChoiceDialog, ConfirmDialog, MessageDialog
from .render import html_to_text

FLAG_NAMES = {0: "No flag", 1: "Red", 2: "Orange", 3: "Green", 4: "Blue", 5: "Pink", 6: "Turquoise", 7: "Purple"}
FLAG_COLORS = {1: "red", 2: "dark_orange", 3: "green", 4: "blue", 5: "hot_pink", 6: "turquoise2", 7: "purple"}


class ReviewerScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("space,enter", "primary", "Show/Good", show=False),
        Binding("1", "answer(1)", "Again", show=False),
        Binding("2", "answer(2)", "Hard", show=False),
        Binding("3", "answer(3)", "Good", show=False),
        Binding("4", "answer(4)", "Easy", show=False),
        Binding("u,ctrl+z", "undo", "Undo"),
        Binding("e", "edit", "Edit"),
        Binding("asterisk", "mark", "Mark"),
        Binding("minus", "bury_card", "Bury"),
        Binding("equals_sign", "bury_note", "Bury note", show=False),
        Binding("at", "suspend_card", "Suspend"),
        Binding("exclamation_mark", "suspend_note", "Suspend note", show=False),
        Binding("f", "flag", "Flag"),
        Binding("i", "card_info", "Info"),
        Binding("ctrl+delete,ctrl+d", "delete_note", "Delete", show=False),
        Binding("m", "more", "More..."),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.current: QueuedCard | None = None
        self.showing_answer = False
        self.shown_at = time.time()
        self.typed = ""
        self.type_correct: str | None = None

    @property
    def col(self):
        return self.app.col

    def compose(self) -> ComposeResult:
        with Horizontal(id="review-top"):
            yield Static("", id="review-counts")
            yield Static("", id="review-timer")
        with VerticalScroll(id="card-scroll"):
            yield Static("", id="card-content")
            yield Input(placeholder="Type the answer and press Enter", id="type-answer")
        with Horizontal(id="answer-bar"):
            yield Button("Show Answer", variant="primary", id="btn-show")
            for ident in ("again", "hard", "good", "easy"):
                yield Button(ident, id=f"btn-{ident}")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1, self.update_timer)
        self.next_card()

    # ----------------------------------------------------------- flow

    def next_card(self) -> None:
        self.current = self.col.sched.get_next_card()
        if self.current is None:
            self.finish()
            return
        self.show_question()

    def finish(self) -> None:
        from .overview import OverviewScreen

        self.notify("Congratulations! You have finished this deck for now.")
        stack = self.app.screen_stack
        if len(stack) >= 2 and isinstance(stack[-2], OverviewScreen):
            self.app.pop_screen()
        else:
            self.app.switch_screen(OverviewScreen())

    def update_counts(self) -> None:
        counts = self.col.sched.counts()
        kind = self.current.kind if self.current else None
        text = Text()
        for value, color, kinds in (
            (counts.new, NEW_COLOR, (KIND_NEW,)),
            (counts.learning, LEARN_COLOR, (KIND_LEARN, KIND_DAY_LEARN)),
            (counts.review, REVIEW_COLOR, (KIND_REVIEW,)),
        ):
            style = f"bold {color}" + (" underline" if kind in kinds else "")
            text.append(str(value), style=style)
            text.append("  ")
        if self.current:
            card = self.current.card
            deck = self.col.get_deck(card.odid or card.did)
            text.append(f"  {deck['name'] if deck else ''}", style="dim")
            if card.user_flag:
                text.append(f"  ⚑ {FLAG_NAMES[card.user_flag]}", style=FLAG_COLORS[card.user_flag])
            note = self.col.get_note(card.nid)
            if note and note.has_tag("marked"):
                text.append("  ★ marked", style="yellow")
        self.query_one("#review-counts", Static).update(text)

    def update_timer(self) -> None:
        secs = int(time.time() - self.shown_at)
        self.query_one("#review-timer", Static).update(f"{secs // 60}:{secs % 60:02d}")

    def render_card_content(self, answer: bool) -> None:
        card = self.current.card
        rendered = self.col.render_card(card)
        content = rendered.answer if answer else rendered.question
        type_input = self.query_one("#type-answer", Input)
        if rendered.type_field and not answer:
            content = content.replace(TYPE_ANSWER_MARKER, "")
            type_input.add_class("visible")
            type_input.value = ""
            type_input.focus()
            note = self.col.get_note(card.nid)
            value = self.col.note_fields_map(note).get(rendered.type_field, "")
            self.type_correct = cloze_answer_text(value, card.ord) if rendered.type_cloze else strip_html(value)
        elif rendered.type_field and answer:
            type_input.remove_class("visible")
            content = content.replace(TYPE_ANSWER_MARKER, self._typed_answer_html(), 1).replace(TYPE_ANSWER_MARKER, "")
        else:
            type_input.remove_class("visible")
            self.type_correct = None
        self.query_one("#card-content", Static).update(html_to_text(content))
        scroll = self.query_one("#card-scroll", VerticalScroll)
        if answer and "id=answer" in content:
            scroll.scroll_end(animate=False)
        else:
            scroll.scroll_home(animate=False)

    def _typed_answer_html(self) -> str:
        correct = self.type_correct or ""
        given = self.typed
        if given.strip() == correct.strip():
            return f'<br><span style="color: green; font-weight: bold">{html.escape(correct)} ✓</span><br>'
        parts = []
        for kind, seg in compare_typed_answer(given, correct):
            color = {"good": "green", "bad": "red", "missing": "grey"}[kind]
            parts.append(f'<span style="color: {color}">{html.escape(seg)}</span>')
        return (
            "<br>You typed: " + "".join(parts) + "<br>" + f'Correct: <span style="font-weight: bold">{html.escape(correct)}</span><br>'
        )

    def show_question(self) -> None:
        self.showing_answer = False
        self.shown_at = time.time()
        self.typed = ""
        self.render_card_content(answer=False)
        btn = self.query_one("#btn-show", Button)
        btn.display = True
        for ident in ("again", "hard", "good", "easy"):
            self.query_one(f"#btn-{ident}", Button).display = False
        if not self.query_one("#type-answer", Input).has_class("visible"):
            btn.focus()
        self.update_counts()
        self.update_timer()

    def show_answer(self) -> None:
        if self.current is None:
            return
        type_input = self.query_one("#type-answer", Input)
        if type_input.has_class("visible"):
            self.typed = type_input.value
        self.showing_answer = True
        self.render_card_content(answer=True)
        labels = self.col.sched.describe_next_states(self.current.states)
        self.query_one("#btn-show", Button).display = False
        for i, (name, ident) in enumerate((("Again", "again"), ("Hard", "hard"), ("Good", "good"), ("Easy", "easy"))):
            button = self.query_one(f"#btn-{ident}", Button)
            button.label = f"{i + 1} {name}  {labels[i]}"
            button.display = True
        self.query_one("#btn-good", Button).focus()

    def answer(self, ease: int) -> None:
        if self.current is None or not self.showing_answer:
            return
        qc = self.current
        taken = int((time.time() - self.shown_at) * 1000)
        nxt = qc.states.for_ease(ease)
        self.col.sched.answer_card(qc.card, qc.states, ease, taken)
        if is_leeched(nxt):
            card = self.col.get_card(qc.card.id)
            msg = "Card was a leech and has been suspended." if card.queue == consts.QUEUE_SUSPENDED else "Card was a leech."
            self.notify(msg, severity="warning")
        self.next_card()

    # -------------------------------------------------------- handlers

    @on(Input.Submitted, "#type-answer")
    def typed_submitted(self) -> None:
        self.show_answer()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        ident = event.button.id or ""
        if ident == "btn-show":
            self.show_answer()
        elif ident.startswith("btn-"):
            ease = {"btn-again": 1, "btn-hard": 2, "btn-good": 3, "btn-easy": 4}.get(ident)
            if ease:
                self.answer(ease)

    def action_primary(self) -> None:
        if self.showing_answer:
            self.answer(3)
        else:
            self.show_answer()

    def action_answer(self, ease: int) -> None:
        if isinstance(self.focused, Input):
            return
        if not self.showing_answer:
            return
        self.answer(ease)

    def check_action(self, action: str, parameters) -> bool | None:
        # let digits and symbols reach the type-in-answer box
        if isinstance(self.focused, Input) and action in (
            "answer", "mark", "bury_card", "bury_note", "suspend_card", "suspend_note", "flag",
            "edit", "undo", "card_info", "more", "primary",
        ):
            return False
        return True

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_undo(self) -> None:
        entry = self.col.sched.undo()
        if entry is None:
            self.notify("Nothing to undo")
            return
        self.notify(f"Undone: {entry.name}")
        self.next_card()

    def action_edit(self) -> None:
        if self.current is None:
            return
        from .note_editor import EditNoteScreen

        def done(_saved) -> None:
            if self.current is None:
                return
            card = self.col.get_card(self.current.card.id)
            if card is None:
                self.col.sched.clear_queues()
                self.next_card()
                return
            self.current.card = card
            self.render_card_content(answer=self.showing_answer)
            self.update_counts()

        self.app.push_screen(EditNoteScreen(self.current.card.nid), done)

    def action_mark(self) -> None:
        if self.current is None:
            return
        nid = self.current.card.nid
        note = self.col.get_note(nid)
        if note.has_tag("marked"):
            self.col.remove_tags_from_notes([nid], "marked")
            self.notify("Note unmarked")
        else:
            self.col.add_tags_to_notes([nid], "marked")
            self.notify("Note marked")
        self.update_counts()

    def _after_removal(self, message: str) -> None:
        self.notify(message)
        self.next_card()

    def action_bury_card(self) -> None:
        if self.current:
            self.col.sched.bury_cards([self.current.card.id])
            self._after_removal("Card buried")

    def action_bury_note(self) -> None:
        if self.current:
            self.col.sched.bury_note(self.current.card.nid)
            self._after_removal("Note buried")

    def action_suspend_card(self) -> None:
        if self.current:
            self.col.sched.suspend_cards([self.current.card.id])
            self._after_removal("Card suspended")

    def action_suspend_note(self) -> None:
        if self.current:
            self.col.sched.suspend_note(self.current.card.nid)
            self._after_removal("Note suspended")

    def action_delete_note(self) -> None:
        if self.current is None:
            return
        nid = self.current.card.nid

        def done(ok: bool) -> None:
            if ok:
                self.col.remove_notes([nid])
                self.col.sched.clear_queues()
                self._after_removal("Note deleted")

        self.app.push_screen(
            ConfirmDialog("Delete Note", "Delete this note and all its cards?", yes="Delete", danger=True), done
        )

    def action_flag(self) -> None:
        if self.current is None:
            return
        choices = [(str(k), f"{k}: {v}") for k, v in FLAG_NAMES.items()]

        def done(key: str | None) -> None:
            if key is None or self.current is None:
                return
            card = self.col.get_card(self.current.card.id)
            card.set_user_flag(int(key))
            self.col.update_card(card)
            self.col.save()
            self.current.card = card
            self.update_counts()

        self.app.push_screen(ChoiceDialog("Flag card", choices), done)

    def action_card_info(self) -> None:
        if self.current:
            self.app.push_screen(MessageDialog("Card Info", card_info_text(self.col, self.current.card.id)))

    def action_more(self) -> None:
        choices = [
            ("bury_note", "Bury note (=)"),
            ("suspend_note", "Suspend note (!)"),
            ("delete_note", "Delete note (Ctrl+D)"),
            ("forget", "Reset card to new"),
            ("options", "Deck options"),
        ]

        def done(key: str | None) -> None:
            if key == "bury_note":
                self.action_bury_note()
            elif key == "suspend_note":
                self.action_suspend_note()
            elif key == "delete_note":
                self.action_delete_note()
            elif key == "forget" and self.current:
                self.col.sched.forget_cards([self.current.card.id])
                self._after_removal("Card reset to new")
            elif key == "options" and self.current:
                from .deck_options import DeckOptionsScreen

                card = self.current.card

                def reload(_):
                    self.col.sched.clear_queues()
                    self.next_card()

                self.app.push_screen(DeckOptionsScreen(card.odid or card.did), reload)

        self.app.push_screen(ChoiceDialog("More actions", choices), done)


def card_info_text(col, cid: int) -> str:
    card = col.get_card(cid)
    note = col.get_note(card.nid)
    nt = col.get_notetype(note.mid)
    deck = col.get_deck(card.did)
    home = col.get_deck(card.odid) if card.odid else None
    rows = col.db.execute(
        "SELECT id, ease, ivl, lastIvl, factor, time, type FROM revlog WHERE cid = ? ORDER BY id", (cid,)
    ).fetchall()
    today = col.today
    type_names = {0: "New", 1: "Learning", 2: "Review", 3: "Relearning"}

    def fmt_ts(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"Added: {fmt_ts(card.nid)}",
        f"First review: {fmt_ts(rows[0][0]) if rows else '-'}",
        f"Latest review: {fmt_ts(rows[-1][0]) if rows else '-'}",
    ]
    if card.queue in (consts.QUEUE_REV, consts.QUEUE_DAY_LRN):
        lines.append(f"Due: in {card.due - today} day(s)")
    elif card.queue == consts.QUEUE_LRN:
        lines.append(f"Due: in {time_span(max(card.due - time.time(), 0))}")
    elif card.queue == consts.QUEUE_NEW:
        lines.append(f"Position: {card.due}")
    if card.type == consts.CARD_TYPE_REV:
        lines.append(f"Interval: {time_span(card.ivl * 86400)}")
        lines.append(f"Ease: {card.factor / 10:.0f}%")
    lines += [
        f"Reviews: {card.reps}",
        f"Lapses: {card.lapses}",
    ]
    if rows:
        total = sum(r[5] for r in rows)
        lines.append(f"Average time: {total / len(rows) / 1000:.1f}s   Total time: {total / 1000:.0f}s")
    tmpl = "Cloze " + str(card.ord + 1) if nt.get("type") == consts.MODEL_CLOZE else nt["tmpls"][card.ord]["name"]
    lines += [
        f"Card type: {type_names.get(card.type, card.type)} ({tmpl})",
        f"Note type: {nt['name']}",
        f"Deck: {deck['name']}" + (f" (home: {home['name']})" if home else ""),
        f"Card ID: {card.id}   Note ID: {note.id}",
    ]
    if rows:
        lines += ["", "Date              Type        Rating  Interval  Ease   Time"]
        kinds = {0: "Learn", 1: "Review", 2: "Relearn", 3: "Filtered", 4: "Manual", 5: "Resched"}
        ratings = {0: "-", 1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}
        for rid, ease, ivl, _last, factor, taken, kind in reversed(rows):
            ivl_txt = time_span(ivl * 86400) if ivl > 0 else time_span(-ivl)
            lines.append(
                f"{fmt_ts(rid)}  {kinds.get(kind, kind):<10}  {ratings.get(ease, ease):<6}  "
                f"{ivl_txt:<9} {factor / 10 if factor else 0:>4.0f}%  {taken / 1000:.1f}s"
            )
    return "\n".join(lines)

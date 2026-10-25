"""The scheduler service: answering, queues, burying, filtered decks, custom study.

Ported from rslib/src/scheduler (answering/, queue/, filtered/, bury_and_suspend.rs).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .. import consts
from ..deckconf import sched_params
from ..models import CARD_COLUMNS, Card, Note
from ..search import escape_search_value, filtered_order_sql
from ..utils import int_time
from .fuzz import fuzz_factor_for_seed, fuzz_seed, learning_ivl_with_fuzz
from .queue import BuryMode, CardQueues, Counts, QueueBuilder, RemainingLimits, remaining_limits
from .states import (
    CardState,
    IntervalKind,
    LearnState,
    NewState,
    PreviewState,
    RelearnState,
    ReschedulingFilterState,
    ReviewState,
    SchedulingStates,
    StateContext,
    f32,
    is_leeched,
    new_position_of,
)
from .steps import LearningSteps

if TYPE_CHECKING:
    from ..collection import Collection

UNDO_LIMIT = 30


@dataclass
class QueuedCard:
    card: Card
    kind: str
    states: SchedulingStates


@dataclass
class DeckNode:
    did: int
    name: str
    full_name: str
    depth: int
    new: int = 0
    learn: int = 0
    review: int = 0
    filtered: bool = False
    collapsed: bool = False
    children: list["DeckNode"] = field(default_factory=list)


@dataclass
class UndoEntry:
    name: str
    cards: list[Card] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    revlog_ids: list[int] = field(default_factory=list)
    decks: dict[str, dict] = field(default_factory=dict)
    card_to_show: int | None = None


@dataclass
class CongratsInfo:
    learn_remaining: int
    secs_until_next_learn: int | None
    have_sched_buried: bool
    have_user_buried: bool
    is_filtered: bool
    new_remaining: bool
    review_remaining: bool


class CustomStudyError(Exception):
    pass


CRAM_NEW = "new"
CRAM_DUE = "due"
CRAM_REVIEW = "review"
CRAM_ALL = "all"


def _gather_ord(queue: int) -> int:
    if queue in (consts.QUEUE_LRN, consts.QUEUE_PREVIEW):
        return 0
    if queue == consts.QUEUE_DAY_LRN:
        return 1
    if queue == consts.QUEUE_REV:
        return 2
    if queue == consts.QUEUE_NEW:
        return 3
    return 4


class Scheduler:
    def __init__(self, col: "Collection"):
        self.col = col
        self.fuzz_enabled = True
        self._queues: CardQueues | None = None
        self.undo_stack: list[UndoEntry] = []
        self._show_first: int | None = None

    # ---------------------------------------------------------------- timing

    def learn_ahead_secs(self) -> int:
        return int(self.col.conf.get("collapseTime", 1200))

    def _unbury_if_day_rolled_over(self, today: int) -> None:
        last = self.col.conf.get("lastUnburied", 0)
        if last < today:
            self.col.db.execute(
                f"UPDATE cards SET queue = (CASE WHEN type IN ({consts.CARD_TYPE_LRN},{consts.CARD_TYPE_RELEARNING}) "
                f"THEN (CASE WHEN due > 1000000000 THEN {consts.QUEUE_LRN} ELSE {consts.QUEUE_DAY_LRN} END) "
                f"ELSE type END), mod = ?, usn = ? WHERE queue IN ({consts.QUEUE_SCHED_BURIED},{consts.QUEUE_USER_BURIED})",
                (int_time(), self.col.usn()),
            )
            self.col.conf["lastUnburied"] = today
            self.col.save()

    # ---------------------------------------------------------------- queues

    def clear_queues(self) -> None:
        self._queues = None

    def _get_queues(self) -> CardQueues:
        timing = self.col.timing_today()
        did = self.col.current_deck_id()
        if self._queues is not None and (
            self._queues.current_day != timing.days_elapsed or self._queues.deck_id != did
        ):
            if self._queues.current_day != timing.days_elapsed:
                self.undo_stack.clear()
            self._queues = None
        if self._queues is None:
            self._unbury_if_day_rolled_over(timing.days_elapsed)
            builder = QueueBuilder(self.col, did)
            builder.gather()
            self._queues = builder.build(self.learn_ahead_secs())
        return self._queues

    def counts(self) -> Counts:
        c = self._get_queues().get_counts()
        return Counts(c.new, c.learning, c.review)

    def get_next_card(self) -> QueuedCard | None:
        queues = self._get_queues()
        queues.get_counts()
        if self._show_first is not None:
            queues.move_to_front(self._show_first)
            self._show_first = None
        for cid, kind in queues.iter():
            card = self.col.get_card(cid)
            if card is None:
                queues.remove_card(cid)
                return self.get_next_card()
            return QueuedCard(card=card, kind=kind, states=self.scheduling_states(card))
        return None

    def deck_counts(self, did: int) -> Counts:
        """Counts for a deck as if it were selected (respecting limits and burying)."""
        timing = self.col.timing_today()
        self._unbury_if_day_rolled_over(timing.days_elapsed)
        builder = QueueBuilder(self.col, did)
        builder.gather()
        q = builder.build(self.learn_ahead_secs())
        return q.counts

    def deck_tree(self) -> list[DeckNode]:
        """Deck list with due counts (port of deck_tree() / sum_counts_and_apply_limits_v3)."""
        col = self.col
        timing = col.timing_today()
        self._unbury_if_day_rolled_over(timing.days_elapsed)
        today = timing.days_elapsed
        learn_cutoff = int(time.time()) + self.learn_ahead_secs()
        counts: dict[int, tuple[int, int, int, int]] = {}
        for did, new, rev, interday, intraday in col.db.execute(
            f"SELECT did, sum(queue = {consts.QUEUE_NEW}), "
            f"sum(queue = {consts.QUEUE_REV} AND due <= :day), "
            f"sum(queue = {consts.QUEUE_DAY_LRN} AND due <= :day), "
            f"sum((queue = {consts.QUEUE_LRN} AND due < :learn) OR (queue = {consts.QUEUE_PREVIEW} AND due <= :learn)) "
            "FROM cards GROUP BY did",
            {"day": today, "learn": learn_cutoff},
        ):
            counts[did] = (new or 0, rev or 0, interday or 0, intraday or 0)
        ignore = bool(col.conf.get("newCardsIgnoreReviewLimit", False))
        roots: list[DeckNode] = []
        by_name: dict[str, DeckNode] = {}
        for deck in col.all_decks():
            name = deck["name"]
            parts = name.split("::")
            node = DeckNode(
                did=int(deck["id"]),
                name=parts[-1],
                full_name=name,
                depth=len(parts) - 1,
                filtered=bool(deck["dyn"]),
                collapsed=bool(deck.get("collapsed", False)),
            )
            by_name[name] = node
            parent = by_name.get("::".join(parts[:-1])) if len(parts) > 1 else None
            (parent.children if parent is not None else roots).append(node)

        def limits_for(node: DeckNode) -> RemainingLimits:
            deck = col.get_deck(node.did)
            conf = None if deck["dyn"] else col.config_for_deck(node.did)
            return remaining_limits(deck, conf, today, ignore)

        def apply(node: DeckNode, parent_limits: RemainingLimits | None) -> list[int]:
            remaining = limits_for(node)
            if parent_limits is not None:
                remaining.cap_to(parent_limits)
                parent_limits = remaining
            new, rev, interday, intraday = counts.get(node.did, (0, 0, 0, 0))
            total = [new, rev, interday, intraday]
            for child in node.children:
                sub = apply(child, parent_limits)
                total = [a + b for a, b in zip(total, sub)]
            new, rev, interday, intraday = total
            interday = min(interday, remaining.review)
            remaining_reviews = max(remaining.review - interday, 0)
            rev = min(rev, remaining_reviews)
            new = min(new, remaining.new)
            if remaining.cap_new_to_review:
                remaining_reviews = max(remaining_reviews - rev, 0)
                new = min(new, remaining_reviews)
            node.new, node.review, node.learn = new, rev, intraday + interday
            return [new, rev, interday, intraday]

        start_limits = RemainingLimits() if col.conf.get("applyAllParentLimits") else None
        for root in roots:
            apply(root, start_limits)
        # Like Anki, hide an empty Default deck when other decks exist.
        if len(roots) > 1:
            roots = [
                r
                for r in roots
                if not (r.did == consts.DEFAULT_DECK_ID and not r.children and not self.col.deck_has_cards(r.did))
            ]
        return roots

    # ---------------------------------------------------------- card states

    def _home_deck(self, card: Card) -> dict:
        return self.col.get_deck(card.odid or card.did) or self.col.get_deck(consts.DEFAULT_DECK_ID)

    def _state_context(self, card: Card, deck: dict, conf: dict) -> StateContext:
        seed = fuzz_seed(card.id, card.reps) if self.fuzz_enabled else None
        p = sched_params(conf)
        return StateContext(
            fuzz_factor=fuzz_factor_for_seed(seed),
            steps=LearningSteps(p["learn_steps"]),
            graduating_interval_good=p["graduating_interval_good"],
            graduating_interval_easy=p["graduating_interval_easy"],
            initial_ease_factor=f32(p["initial_ease"]),
            hard_multiplier=f32(p["hard_multiplier"]),
            easy_multiplier=f32(p["easy_multiplier"]),
            interval_multiplier=f32(p["interval_multiplier"]),
            maximum_review_interval=p["maximum_review_interval"],
            leech_threshold=p["leech_threshold"],
            relearn_steps=LearningSteps(p["relearn_steps"]),
            lapse_multiplier=f32(p["lapse_multiplier"]),
            minimum_lapse_interval=p["minimum_lapse_interval"],
            in_filtered_deck=bool(deck["dyn"]),
            preview_again=int(deck.get("previewAgainSecs", 60)) if deck["dyn"] else 0,
            preview_hard=int(deck.get("previewHardSecs", 600)) if deck["dyn"] else 0,
            preview_good=int(deck.get("previewGoodSecs", 0)) if deck["dyn"] else 0,
        )

    def current_card_state(self, card: Card, deck: dict, conf: dict, today: int) -> CardState:
        if deck["dyn"]:
            due = card.odue if card.odue != 0 else card.due
        elif card.type == consts.CARD_TYPE_REV:
            due = min(card.due, today)
        else:
            due = card.due
        ease = f32(card.factor / 1000.0) if card.factor else f32(sched_params(conf)["initial_ease"])
        normal: CardState
        if card.type == consts.CARD_TYPE_NEW:
            normal = NewState(position=max(due, 0))
        elif card.type == consts.CARD_TYPE_LRN:
            steps = LearningSteps(sched_params(conf)["learn_steps"])
            normal = LearnState(remaining_steps=card.left, scheduled_secs=steps.current_delay_secs(card.left))
        elif card.type == consts.CARD_TYPE_REV:
            normal = ReviewState(
                scheduled_days=card.ivl,
                elapsed_days=max(card.ivl - (due - today), 0),
                ease_factor=ease,
                lapses=card.lapses,
            )
        else:
            steps = LearningSteps(sched_params(conf)["relearn_steps"])
            normal = RelearnState(
                learning=LearnState(remaining_steps=card.left, scheduled_secs=steps.current_delay_secs(card.left)),
                review=ReviewState(
                    scheduled_days=card.ivl, elapsed_days=card.ivl, ease_factor=ease, lapses=card.lapses
                ),
            )
        if deck["dyn"]:
            if deck.get("resched", True):
                return ReschedulingFilterState(original_state=normal)
            return PreviewState(scheduled_secs=int(deck.get("previewAgainSecs", 60)), finished=False)
        return normal

    def scheduling_states(self, card: Card) -> SchedulingStates:
        deck = self.col.get_deck(card.did)
        conf = self.col.config_for_deck(self._home_deck(card)["id"])
        today = self.col.timing_today().days_elapsed
        current = self.current_card_state(card, deck, conf, today)
        return current.next_states(self._state_context(card, deck, conf))

    def describe_next_states(self, states: SchedulingStates) -> list[str]:
        timing = self.col.timing_today()
        secs_until_rollover = max(timing.next_day_at - int(time.time()), 0)
        collapse = self.learn_ahead_secs()
        out = []
        for st in (states.again, states.hard, states.good, states.easy):
            secs = st.interval_kind().maybe_as_days(secs_until_rollover).as_seconds()
            out.append(answer_button_time_collapsible(secs, collapse))
        return out

    # ------------------------------------------------------------- answering

    def answer_card(self, card: Card, states: SchedulingStates, ease: int, taken_ms: int = 0) -> None:
        col = self.col
        card = col.get_card(card.id)
        original = card.copy()
        timing = col.timing_today()
        now = int(time.time())
        deck = col.get_deck(card.did)
        home = self._home_deck(card)
        conf = col.config_for_deck(home["id"])
        current = self.current_card_state(card, deck, conf, timing.days_elapsed)
        nxt = states.for_ease(ease)
        secs_until_rollover = max(timing.next_day_at - now, 0)
        seed = fuzz_seed(card.id, card.reps) if self.fuzz_enabled else None

        undo = UndoEntry(name="Answer Card", card_to_show=card.id)
        undo.cards.append(original)
        affected_decks = [card.did] + col.parent_ids(card.did)
        undo.decks = {str(d): json.loads(json.dumps(col.decks[str(d)])) for d in affected_decks}

        revlog_ease_factor = self._apply_study_state(card, current, nxt, timing, now, seed, secs_until_rollover)
        if is_leeched(nxt) and sched_params(conf)["leech_action"] == consts.LEECH_SUSPEND:
            card.queue = consts.QUEUE_SUSPENDED

        taken_ms = min(int(taken_ms), sched_params(conf)["cap_answer_time_to_secs"] * 1000)
        revlog_id = col._next_id("revlog", int(time.time() * 1000))
        col.db.execute(
            "INSERT INTO revlog VALUES (?,?,?,?,?,?,?,?,?)",
            (
                revlog_id,
                card.id,
                col.usn(),
                ease,
                nxt.interval_kind().maybe_as_days(secs_until_rollover).as_revlog_interval(),
                current.interval_kind().maybe_as_days(secs_until_rollover).as_revlog_interval(),
                int(round(revlog_ease_factor * 1000)),
                taken_ms,
                current.revlog_kind(),
            ),
        )
        undo.revlog_ids.append(revlog_id)

        self._update_deck_stats(original.did, original.queue, taken_ms, timing.days_elapsed)
        buried = self._maybe_bury_siblings(original, conf, undo)
        col.update_card(card)
        if is_leeched(nxt):
            note = col.get_note(card.nid)
            undo.notes.append(note.copy())
            col.add_tags_to_notes([card.nid], "leech")
        col.save()
        self._push_undo(undo)

        if self._queues is not None:
            try:
                self._queues.pop_entry(card.id)
            except ValueError:
                self._queues.remove_card(card.id)
            for cid in buried:
                self._queues.remove_card(cid)
            finished_preview = isinstance(nxt, PreviewState) and nxt.finished
            if not finished_preview and card.queue != consts.QUEUE_SUSPENDED:
                self._queues.maybe_requeue_learning_card(card, timing.next_day_at)
            self._queues.update_learning_cutoff_and_count()

    def _apply_study_state(self, card, current, nxt, timing, now, seed, secs_until_rollover) -> float:
        if isinstance(nxt, (PreviewState, ReschedulingFilterState)):
            if not card.odid:
                raise ValueError("card answering can't transition into filtered state")
            if isinstance(nxt, PreviewState):
                if nxt.finished:
                    _remove_from_filtered_deck_restoring_queue(card)
                    return 0.0
                card.queue = consts.QUEUE_PREVIEW
                card.due = now + learning_ivl_with_fuzz(seed, nxt.scheduled_secs)
                return 0.0
            factor = self._apply_normal_state(card, current, nxt.original_state, timing, now, seed, secs_until_rollover)
            card.odue = card.due
            return factor
        if isinstance(current, PreviewState):
            raise ValueError("should set finished=true, not return different state")
        if isinstance(current, ReschedulingFilterState) and card.odid:
            card.did = card.odid
            card.odid = 0
            card.odue = 0
        return self._apply_normal_state(card, current, nxt, timing, now, seed, secs_until_rollover)

    def _apply_normal_state(self, card, current, nxt, timing, now, seed, secs_until_rollover) -> float:
        card.reps += 1
        position = new_position_of(current)
        if isinstance(nxt, NewState):
            card.type = consts.CARD_TYPE_NEW
            card.queue = consts.QUEUE_NEW
            card.due = nxt.position
            return 0.0
        if position is not None:
            _set_original_position(card, position)
        if isinstance(nxt, LearnState):
            card.left = nxt.remaining_steps
            card.type = consts.CARD_TYPE_LRN
            self._set_learning_due(card, nxt.interval_kind(), timing, now, seed, secs_until_rollover)
            return 0.0
        if isinstance(nxt, ReviewState):
            card.queue = consts.QUEUE_REV
            card.type = consts.CARD_TYPE_REV
            card.ivl = nxt.scheduled_days
            card.due = timing.days_elapsed + nxt.scheduled_days
            card.factor = int(round(nxt.ease_factor * 1000))
            card.lapses = nxt.lapses
            card.left = 0
            return nxt.ease_factor
        assert isinstance(nxt, RelearnState)
        card.ivl = nxt.review.scheduled_days
        card.left = nxt.learning.remaining_steps
        card.type = consts.CARD_TYPE_RELEARNING
        card.lapses = nxt.review.lapses
        card.factor = int(round(nxt.review.ease_factor * 1000))
        self._set_learning_due(card, nxt.interval_kind(), timing, now, seed, secs_until_rollover)
        return nxt.review.ease_factor

    def _set_learning_due(self, card, interval: IntervalKind, timing, now, seed, secs_until_rollover) -> None:
        interval = interval.maybe_as_days(secs_until_rollover)
        if interval.is_days:
            card.queue = consts.QUEUE_DAY_LRN
            card.due = timing.days_elapsed + interval.days
        else:
            card.queue = consts.QUEUE_LRN
            card.due = now + learning_ivl_with_fuzz(seed, interval.secs)

    def _update_deck_stats(self, did: int, from_queue: int, taken_ms: int, today: int) -> None:
        new_delta = 1 if from_queue == consts.QUEUE_NEW else 0
        rev_delta = 1 if from_queue in (consts.QUEUE_REV, consts.QUEUE_DAY_LRN) else 0
        for d in [did] + self.col.parent_ids(did):
            deck = self.col.decks[str(d)]
            for key, delta in (("newToday", new_delta), ("revToday", rev_delta), ("timeToday", taken_ms)):
                day, count = deck.get(key, [0, 0])
                if day != today:
                    count = 0
                deck[key] = [today, count + delta]
            if "lrnToday" in deck and deck["lrnToday"][0] != today:
                deck["lrnToday"] = [today, 0]
            deck["mod"] = int_time()
            deck["usn"] = self.col.usn()

    def _maybe_bury_siblings(self, card: Card, conf: dict, undo: UndoEntry) -> list[int]:
        mode = BuryMode.from_config(conf)
        if not mode.any():
            return []
        mode.bury_interday_learning &= _gather_ord(card.queue) <= _gather_ord(consts.QUEUE_DAY_LRN)
        mode.bury_reviews &= _gather_ord(card.queue) <= _gather_ord(consts.QUEUE_REV)
        conds = []
        if mode.bury_new:
            conds.append(f"queue = {consts.QUEUE_NEW}")
        if mode.bury_reviews:
            conds.append(f"queue = {consts.QUEUE_REV}")
        if mode.bury_interday_learning:
            conds.append(f"queue = {consts.QUEUE_DAY_LRN}")
        if not conds:
            return []
        rows = self.col.db.execute(
            f"SELECT {CARD_COLUMNS} FROM cards WHERE nid = ? AND id != ? AND ({' OR '.join(conds)})",
            (card.nid, card.id),
        ).fetchall()
        ids = []
        for row in rows:
            sib = Card.from_row(row)
            undo.cards.append(sib.copy())
            sib.queue = consts.QUEUE_SCHED_BURIED
            self.col.update_card(sib)
            ids.append(sib.id)
        return ids

    # ------------------------------------------------------------------ undo

    def _push_undo(self, entry: UndoEntry) -> None:
        self.undo_stack.append(entry)
        del self.undo_stack[:-UNDO_LIMIT]

    def undo_name(self) -> str | None:
        return self.undo_stack[-1].name if self.undo_stack else None

    def undo(self) -> UndoEntry | None:
        if not self.undo_stack:
            return None
        entry = self.undo_stack.pop()
        col = self.col
        for card in entry.cards:
            col.update_card(card, touch=False)
        for note in entry.notes:
            col._write_note(note)
        for rid in entry.revlog_ids:
            col.db.execute("DELETE FROM revlog WHERE id = ?", (rid,))
        for did, deck in entry.decks.items():
            if did in col.decks:
                col.decks[did] = deck
        col.save()
        self.clear_queues()
        self._show_first = entry.card_to_show
        return entry

    # ----------------------------------------------------- bury and suspend

    def _snapshot_cards(self, name: str, cids: list[int]) -> UndoEntry:
        entry = UndoEntry(name=name)
        for cid in cids:
            card = self.col.get_card(cid)
            if card:
                entry.cards.append(card.copy())
        return entry

    def bury_cards(self, cids: list[int], manual: bool = True) -> None:
        entry = self._snapshot_cards("Bury", cids)
        queue = consts.QUEUE_USER_BURIED if manual else consts.QUEUE_SCHED_BURIED
        for card in [c.copy() for c in entry.cards]:
            if card.queue == consts.QUEUE_SUSPENDED:
                continue
            card.queue = queue
            self.col.update_card(card)
            if self._queues:
                self._queues.remove_card(card.id)
        self.col.save()
        self._push_undo(entry)

    def bury_note(self, nid: int) -> None:
        cids = [c.id for c in self.col.cards_of_note(nid)]
        self.bury_cards(cids)
        self.undo_stack[-1].name = "Bury Note"

    def suspend_cards(self, cids: list[int]) -> None:
        entry = self._snapshot_cards("Suspend", cids)
        for card in [c.copy() for c in entry.cards]:
            card.queue = consts.QUEUE_SUSPENDED
            self.col.update_card(card)
            if self._queues:
                self._queues.remove_card(card.id)
        self.col.save()
        self._push_undo(entry)

    def suspend_note(self, nid: int) -> None:
        self.suspend_cards([c.id for c in self.col.cards_of_note(nid)])
        self.undo_stack[-1].name = "Suspend Note"

    def unsuspend_or_unbury(self, cids: list[int]) -> None:
        entry = self._snapshot_cards("Unsuspend/Unbury", cids)
        for card in [c.copy() for c in entry.cards]:
            if card.queue < 0:
                _restore_queue_from_type(card)
                self.col.update_card(card)
        self.col.save()
        self.clear_queues()
        self._push_undo(entry)

    def unbury_deck(self, did: int, mode: str = "all") -> None:
        ids = ",".join(str(d) for d in self.col.child_ids(did, include_self=True))
        queues = {
            "all": f"{consts.QUEUE_SCHED_BURIED},{consts.QUEUE_USER_BURIED}",
            "sched": str(consts.QUEUE_SCHED_BURIED),
            "user": str(consts.QUEUE_USER_BURIED),
        }[mode]
        cids = [r[0] for r in self.col.db.execute(f"SELECT id FROM cards WHERE did IN ({ids}) AND queue IN ({queues})")]
        self.unsuspend_or_unbury(cids)

    def forget_cards(self, cids: list[int], reset_counts: bool = False) -> None:
        """Reset cards to new, placing them at the end of the new queue."""
        entry = self._snapshot_cards("Forget", cids)
        for card in [c.copy() for c in entry.cards]:
            if card.odid:
                _remove_from_filtered_deck_restoring_queue(card)
            pos = int(self.col.conf.get("nextPos", 1))
            self.col.conf["nextPos"] = pos + 1
            card.type = consts.CARD_TYPE_NEW
            card.queue = consts.QUEUE_NEW
            card.due = pos
            card.ivl = 0
            card.factor = 0
            card.left = 0
            if reset_counts:
                card.reps = 0
                card.lapses = 0
            self.col.update_card(card)
        self.col.save()
        self.clear_queues()
        self._push_undo(entry)

    def set_due_date(self, cids: list[int], days: int) -> None:
        """Make cards review cards due in `days` days (Anki's 'Set Due Date')."""
        entry = self._snapshot_cards("Set Due Date", cids)
        today = self.col.today
        for card in [c.copy() for c in entry.cards]:
            if card.odid:
                _remove_from_filtered_deck_restoring_queue(card)
            if card.type != consts.CARD_TYPE_REV:
                card.factor = card.factor or int(self.col.config_for_deck(card.did)["new"]["initialFactor"])
            card.type = consts.CARD_TYPE_REV
            card.queue = consts.QUEUE_REV
            card.due = today + days
            card.ivl = max(days, 1)
            card.left = 0
            self.col.update_card(card)
        self.col.save()
        self.clear_queues()
        self._push_undo(entry)

    # --------------------------------------------------------------- congrats

    def congrats_info(self) -> CongratsInfo:
        did = self.col.current_deck_id()
        ids = ",".join(str(d) for d in self.col.child_ids(did, include_self=True))
        timing = self.col.timing_today()
        row = self.col.db.execute(
            f"SELECT count(), min(due) FROM cards WHERE did IN ({ids}) AND queue = ? AND due < ?",
            (consts.QUEUE_LRN, timing.next_day_at),
        ).fetchone()
        learn, next_due = row
        sched = self.col.db.execute(
            f"SELECT 1 FROM cards WHERE did IN ({ids}) AND queue = ? LIMIT 1", (consts.QUEUE_SCHED_BURIED,)
        ).fetchone()
        user = self.col.db.execute(
            f"SELECT 1 FROM cards WHERE did IN ({ids}) AND queue = ? LIMIT 1", (consts.QUEUE_USER_BURIED,)
        ).fetchone()
        new_rem = self.col.db.execute(
            f"SELECT 1 FROM cards WHERE did IN ({ids}) AND queue = ? LIMIT 1", (consts.QUEUE_NEW,)
        ).fetchone()
        rev_rem = self.col.db.execute(
            f"SELECT 1 FROM cards WHERE did IN ({ids}) AND queue IN (2,3) AND due <= ? LIMIT 1",
            (timing.days_elapsed,),
        ).fetchone()
        return CongratsInfo(
            learn_remaining=learn,
            secs_until_next_learn=max(next_due - int(time.time()), 0) if next_due else None,
            have_sched_buried=bool(sched),
            have_user_buried=bool(user),
            is_filtered=bool(self.col.get_deck(did)["dyn"]),
            new_remaining=bool(new_rem),
            review_remaining=bool(rev_rem),
        )

    # ------------------------------------------------------- filtered decks

    def rebuild_filtered_deck(self, did: int) -> int:
        deck = self.col.get_deck(did)
        if not deck or not deck["dyn"]:
            raise ValueError("not a filtered deck")
        self.empty_filtered_deck(did, save=False)
        timing = self.col.timing_today()
        position = -100_000
        count = 0
        for term in deck.get("terms", [])[:2]:
            search, limit, order = term[0], int(term[1]), int(term[2])
            query = f"({search}) -is:suspended -is:buried -deck:filtered" if search.strip() else (
                "-is:suspended -is:buried -deck:filtered"
            )
            order_sql = filtered_order_sql(order, timing.days_elapsed, timing.next_day_at)
            cids = self.col.find_cards(query, order_sql)[:limit]
            for cid in cids:
                card = self.col.get_card(cid)
                if card is None or card.odid or card.odue:
                    continue
                card.odid = card.did
                card.did = did
                card.odue = card.due
                if not deck.get("resched", True):
                    card.queue = consts.QUEUE_REV
                if card.due > 0:
                    card.due = position
                self.col.update_card(card)
                position += 1
                count += 1
        self.col.save()
        self.clear_queues()
        return count

    def empty_filtered_deck(self, did: int, save: bool = True) -> None:
        rows = self.col.db.execute(f"SELECT {CARD_COLUMNS} FROM cards WHERE did = ?", (did,)).fetchall()
        for row in rows:
            card = Card.from_row(row)
            _remove_from_filtered_deck_restoring_queue(card)
            self.col.update_card(card)
        if save:
            self.col.save()
            self.clear_queues()

    def create_filtered_deck(
        self,
        name: str,
        search: str,
        limit: int = 100,
        order: int = consts.DYN_RANDOM,
        resched: bool = True,
        search2: tuple[str, int, int] | None = None,
        preview_secs: tuple[int, int, int] = (60, 600, 0),
        existing_id: int | None = None,
    ) -> tuple[int, int]:
        """Create (or update) and build a filtered deck. Returns (deck id, card count)."""
        if existing_id is None:
            did = self.col.add_filtered_deck(name)
            created = True
        else:
            did = existing_id
            created = False
            if self.col.get_deck(did)["name"] != name:
                self.col.rename_deck(did, name)
        deck = self.col.get_deck(did)
        terms = [[search, int(limit), int(order)]]
        if search2 is not None:
            terms.append([search2[0], int(search2[1]), int(search2[2])])
        deck["terms"] = terms
        deck["resched"] = bool(resched)
        deck["previewAgainSecs"], deck["previewHardSecs"], deck["previewGoodSecs"] = preview_secs
        self.col.save_deck(deck)
        count = self.rebuild_filtered_deck(did)
        if count == 0 and created:
            self.col.decks.pop(str(did), None)
            self.col.save()
        return did, count

    # ------------------------------------------------------------ custom study

    def custom_study_defaults(self, did: int) -> dict:
        deck = self.col.get_deck(did)
        q_today = self.col.today
        own = self.deck_counts_uncapped(did)
        return {
            "extend_new": int(deck.get("extendNew", 10) or 10),
            "extend_review": int(deck.get("extendRev", 50) or 50),
            "available_new": own[0],
            "available_review": own[1],
            "today": q_today,
        }

    def deck_counts_uncapped(self, did: int) -> tuple[int, int]:
        ids = ",".join(str(d) for d in self.col.child_ids(did, include_self=True))
        today = self.col.today
        new = self.col.db.execute(
            f"SELECT count() FROM cards WHERE did IN ({ids}) AND queue = ?", (consts.QUEUE_NEW,)
        ).fetchone()[0]
        rev = self.col.db.execute(
            f"SELECT count() FROM cards WHERE did IN ({ids}) AND queue IN (2,3) AND due <= ?", (today,)
        ).fetchone()[0]
        return new, rev

    def extend_limits(self, did: int, new_delta: int, review_delta: int) -> None:
        today = self.col.today
        targets = [did]
        if self.col.conf.get("applyAllParentLimits"):
            targets += self.col.parent_ids(did)
        for d in targets:
            deck = self.col.decks[str(d)]
            for key, delta in (("newToday", new_delta), ("revToday", review_delta)):
                day, count = deck.get(key, [0, 0])
                if day != today:
                    count = 0
                deck[key] = [today, count - delta]
            deck["mod"] = int_time()
            deck["usn"] = self.col.usn()
        deck = self.col.decks[str(did)]
        if new_delta > 0:
            deck["extendNew"] = new_delta
        if review_delta > 0:
            deck["extendRev"] = review_delta
        self.col.save()
        self.clear_queues()

    def custom_study(
        self,
        did: int,
        option: str,
        value: int,
        cram_kind: str = CRAM_NEW,
        tags_include: list[str] | None = None,
        tags_exclude: list[str] | None = None,
    ) -> int | None:
        """Port of custom_study_inner(). Returns the filtered deck id, if one was built."""
        deck = self.col.get_deck(did)
        if option == "new_limit":
            self.extend_limits(did, value, 0)
            return None
        if option == "review_limit":
            self.extend_limits(did, 0, value)
            return None
        deck_term = f'deck:"{escape_search_value(deck["name"])}"'
        if option == "forgot":
            search, resched, order, limit = f"rated:{value}:1 {deck_term}", False, consts.DYN_RANDOM, 99_999
        elif option == "ahead":
            search, resched, order, limit = f"prop:due<={value} {deck_term}", True, consts.DYN_DUE, 99_999
        elif option == "preview":
            search, resched, order, limit = f"is:new added:{value} {deck_term}", False, consts.DYN_ADDED, 99_999
        elif option == "cram":
            state, resched, order = {
                CRAM_NEW: ("is:new", True, consts.DYN_ADDED),
                CRAM_DUE: ("is:due", True, consts.DYN_DUE),
                CRAM_REVIEW: ("-is:new", True, consts.DYN_RANDOM),
                CRAM_ALL: ("", False, consts.DYN_RANDOM),
            }[cram_kind]
            parts = [p for p in (state, deck_term) if p]
            inc = [f'tag:"{escape_search_value(t)}"' for t in (tags_include or [])]
            if inc:
                parts.append("(" + " OR ".join(inc) + ")")
            parts += [f'-tag:"{escape_search_value(t)}"' for t in (tags_exclude or [])]
            search, limit = " ".join(parts), value
            self.col.conf[f"customStudyIncludeTags_{did}"] = list(tags_include or [])
            self.col.conf[f"customStudyExcludeTags_{did}"] = list(tags_exclude or [])
        else:
            raise ValueError(f"unknown custom study option {option}")

        name = consts.CUSTOM_STUDY_DECK_NAME
        existing = self.col.deck_id_by_name(name)
        if existing is not None and not self.col.get_deck(existing)["dyn"]:
            raise CustomStudyError(
                "Rename the existing 'Custom Study Session' deck first, as it is not a filtered deck."
            )
        if existing is not None and existing == did:
            raise CustomStudyError("Custom study can't be started from the custom study deck itself.")
        new_did, count = self.create_filtered_deck(
            name, search, limit, order, resched, preview_secs=(60, 600, 0), existing_id=existing
        )
        if count == 0:
            raise CustomStudyError("No cards matched the criteria you provided.")
        self.col.set_current_deck(new_did)
        return new_did


def _restore_queue_from_type(card: Card) -> None:
    if card.type in (consts.CARD_TYPE_LRN, consts.CARD_TYPE_RELEARNING):
        card.queue = consts.QUEUE_LRN if card.due > 1_000_000_000 else consts.QUEUE_DAY_LRN
    elif card.type == consts.CARD_TYPE_NEW:
        card.queue = consts.QUEUE_NEW
    else:
        card.queue = consts.QUEUE_REV


def _remove_from_filtered_deck_restoring_queue(card: Card) -> None:
    if not card.odid:
        return
    card.did = card.odid
    card.odid = 0
    if card.odue:
        card.due = card.odue
    if card.queue >= 0:
        _restore_queue_from_type(card)
    card.odue = 0


def _set_original_position(card: Card, position: int) -> None:
    try:
        data = json.loads(card.data) if card.data else {}
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}
    data["pos"] = position
    card.data = json.dumps(data, separators=(",", ":"))


# ---------------------------------------------------------- time formatting

_SECOND = 1.0
_MINUTE = 60.0
_HOUR = 3600.0
_DAY = 86400.0
_YEAR = 365.0 * _DAY
_MONTH = _YEAR / 12.0


def _natural_unit(secs: float) -> tuple[str, float]:
    a = abs(secs)
    if a < _MINUTE:
        return "s", _SECOND
    if a < _HOUR:
        return "m", _MINUTE
    if a < _DAY:
        return "h", _HOUR
    if a < _MONTH:
        return "d", _DAY
    if a < _YEAR:
        return "mo", _MONTH
    return "y", _YEAR


def _fmt_amount(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def answer_button_time(secs: float) -> str:
    unit, size = _natural_unit(secs)
    amount = secs / size
    if unit in ("s", "m", "d"):
        amount = float(round(amount))
    else:
        amount = round(amount * 10) / 10
    return f"{_fmt_amount(amount)}{unit}"


def answer_button_time_collapsible(secs: int, collapse_secs: int) -> str:
    text = answer_button_time(secs)
    if secs == 0:
        return "End"
    if secs < collapse_secs:
        return "<" + text
    return text


def time_span(secs: float) -> str:
    unit, size = _natural_unit(secs)
    names = {"s": "second", "m": "minute", "h": "hour", "d": "day", "mo": "month", "y": "year"}
    amount = secs / size
    amount = float(round(amount)) if unit in ("s", "d") else round(amount * 10) / 10
    label = names[unit] + ("" if amount == 1 else "s")
    return f"{_fmt_amount(amount)} {label}"

"""Study queue building, ported from rslib/src/scheduler/queue."""

from __future__ import annotations

import bisect
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator

from .. import consts
from ..deckconf import sched_params
from ..utils import fnv1a_64

if TYPE_CHECKING:
    from ..collection import Collection

KIND_NEW = "new"
KIND_REVIEW = "review"
KIND_DAY_LEARN = "day_learn"
KIND_LEARN = "learn"


@dataclass
class BuryMode:
    bury_new: bool = False
    bury_reviews: bool = False
    bury_interday_learning: bool = False

    @classmethod
    def from_config(cls, conf: dict) -> "BuryMode":
        return cls(
            bury_new=bool(conf["new"].get("bury", False)),
            bury_reviews=bool(conf["rev"].get("bury", False)),
            bury_interday_learning=bool(conf.get("buryInterdayLearning", False)),
        )

    def any(self) -> bool:
        return self.bury_new or self.bury_reviews or self.bury_interday_learning


@dataclass
class RemainingLimits:
    review: int = 9999
    new: int = 9999
    cap_new_to_review: bool = False

    def get(self, kind: str) -> int:
        return self.review if kind == KIND_REVIEW else self.new

    def cap_to(self, other: "RemainingLimits") -> None:
        self.review = min(self.review, other.review)
        self.new = min(self.new, other.new)

    def decrement(self, kind: str) -> None:
        if kind == KIND_REVIEW:
            self.review = max(self.review - 1, 0)
            if self.cap_new_to_review:
                self.new = min(self.new, self.review)
        else:
            self.new = max(self.new - 1, 0)


def _day_limit(value, today: int) -> int | None:
    if isinstance(value, dict) and value.get("today") == today:
        return int(value.get("limit", 0))
    return None


def deck_today_counts(deck: dict, today: int) -> tuple[int, int]:
    new = deck.get("newToday", [0, 0])
    rev = deck.get("revToday", [0, 0])
    return (new[1] if new[0] == today else 0, rev[1] if rev[0] == today else 0)


def remaining_limits(deck: dict, conf: dict | None, today: int, new_ignores_review_limit: bool) -> RemainingLimits:
    """Port of RemainingLimits::new_for_normal_deck_v3."""
    if deck.get("dyn") or conf is None:
        return RemainingLimits()
    review_limit = _day_limit(deck.get("reviewLimitToday"), today)
    if review_limit is None:
        review_limit = deck.get("reviewLimit")
    if review_limit is None:
        review_limit = sched_params(conf)["reviews_per_day"]
    new_limit = _day_limit(deck.get("newLimitToday"), today)
    if new_limit is None:
        new_limit = deck.get("newLimit")
    if new_limit is None:
        new_limit = sched_params(conf)["new_per_day"]
    new_today, review_today = deck_today_counts(deck, today)
    review_limit -= review_today
    new_limit -= new_today
    if not new_ignores_review_limit:
        review_limit -= new_today
        new_limit = min(new_limit, review_limit)
    return RemainingLimits(max(review_limit, 0), max(new_limit, 0), not new_ignores_review_limit)


class LimitTree:
    """Per-deck limits where each child is capped by its parent."""

    def __init__(self, col: "Collection", decks: list[dict], today: int):
        self.limits: dict[int, RemainingLimits] = {}
        self.parent: dict[int, int | None] = {}
        ignore = bool(col.conf.get("newCardsIgnoreReviewLimit", False))
        by_name = {d["name"]: int(d["id"]) for d in decks}
        self.root = int(decks[0]["id"])
        for deck in decks:
            did = int(deck["id"])
            conf = None if deck["dyn"] else col.config_for_deck(did)
            self.limits[did] = remaining_limits(deck, conf, today, ignore)
            parent_name = deck["name"].rsplit("::", 1)[0] if "::" in deck["name"] else None
            self.parent[did] = by_name.get(parent_name) if parent_name else None
        # decks are sorted by name, so parents are capped before their children
        for deck in decks:
            did = int(deck["id"])
            pid = self.parent[did]
            if pid is not None and pid in self.limits:
                self.limits[did].cap_to(self.limits[pid])

    def root_limit_reached(self, kind: str) -> bool:
        return self.limits[self.root].get(kind) == 0

    def limit_reached(self, did: int, kind: str) -> bool:
        lim = self.limits.get(did)
        return lim is None or lim.get(kind) == 0

    def decrement(self, did: int, kind: str) -> None:
        cur: int | None = did
        while cur is not None and cur in self.limits:
            self.limits[cur].decrement(kind)
            cur = self.parent.get(cur)
        # children can't have more remaining than their parents
        for child, lim in self.limits.items():
            pid = self.parent.get(child)
            if pid is not None and pid in self.limits:
                lim.cap_to(self.limits[pid])


@dataclass
class MainEntry:
    id: int
    mod: int
    kind: str


@dataclass(order=True)
class LearningEntry:
    sort_key: tuple = field(init=False, repr=False)
    due: int
    id: int
    mod: int
    reps: int

    def __post_init__(self) -> None:
        self.sort_key = (self.reps == 0, self.due)


@dataclass
class Counts:
    new: int = 0
    learning: int = 0
    review: int = 0

    def all_zero(self) -> bool:
        return self.new == 0 and self.learning == 0 and self.review == 0


class CardQueues:
    def __init__(
        self,
        counts: Counts,
        main: list[MainEntry],
        intraday: list[LearningEntry],
        current_day: int,
        learn_ahead_secs: int,
        deck_id: int,
    ):
        self.counts = counts
        self.main: deque[MainEntry] = deque(main)
        self.intraday: list[LearningEntry] = intraday
        self.current_day = current_day
        self.learn_ahead_secs = learn_ahead_secs
        self.current_learning_cutoff = int(time.time())
        self.deck_id = deck_id

    def learn_ahead_cutoff(self) -> int:
        return self.current_learning_cutoff + self.learn_ahead_secs

    def intraday_now(self) -> Iterator[LearningEntry]:
        cutoff = self.current_learning_cutoff
        return (e for e in self.intraday if e.due <= cutoff)

    def intraday_ahead(self) -> Iterator[LearningEntry]:
        cutoff = self.current_learning_cutoff
        ahead = self.learn_ahead_cutoff()
        return (e for e in self.intraday if cutoff < e.due <= ahead)

    def iter(self) -> Iterator[tuple[int, str]]:
        for e in self.intraday_now():
            yield e.id, KIND_LEARN
        for m in self.main:
            yield m.id, m.kind
        for e in self.intraday_ahead():
            yield e.id, KIND_LEARN

    def get_counts(self) -> Counts:
        if self.counts.all_zero():
            self.update_learning_cutoff_and_count()
        return self.counts

    def update_learning_cutoff_and_count(self) -> None:
        last_ahead = self.learn_ahead_cutoff()
        self.current_learning_cutoff = int(time.time())
        new_ahead = self.learn_ahead_cutoff()
        self.counts.learning += sum(1 for e in self.intraday if last_ahead < e.due <= new_ahead)

    def _main_count_delta(self, kind: str, delta: int) -> None:
        if kind == KIND_NEW:
            self.counts.new = max(self.counts.new + delta, 0)
        elif kind == KIND_REVIEW:
            self.counts.review = max(self.counts.review + delta, 0)
        else:
            self.counts.learning = max(self.counts.learning + delta, 0)

    def pop_entry(self, cid: int) -> None:
        for i, e in enumerate(self.intraday):
            if e.id == cid:
                del self.intraday[i]
                self.counts.learning = max(self.counts.learning - 1, 0)
                return
        if self.main and self.main[0].id == cid:
            head = self.main.popleft()
            self._main_count_delta(head.kind, -1)
            return
        raise ValueError("card is not at the top of the queue")

    def remove_card(self, cid: int) -> None:
        """Remove a card from anywhere in the queues (bury/suspend/delete)."""
        for i, e in enumerate(self.intraday):
            if e.id == cid:
                del self.intraday[i]
                if e.due <= self.learn_ahead_cutoff():
                    self.counts.learning = max(self.counts.learning - 1, 0)
                return
        for i, m in enumerate(self.main):
            if m.id == cid:
                del self.main[i]
                self._main_count_delta(m.kind, -1)
                return

    def contains(self, cid: int) -> bool:
        return any(e.id == cid for e in self.intraday) or any(m.id == cid for m in self.main)

    def move_to_front(self, cid: int) -> bool:
        for i, e in enumerate(self.intraday):
            if e.id == cid:
                if e.due > self.learn_ahead_cutoff():
                    self.counts.learning += 1
                del self.intraday[i]
                entry = LearningEntry(due=0, id=e.id, mod=e.mod, reps=e.reps)
                self.intraday.insert(0, entry)
                return True
        for i, m in enumerate(self.main):
            if m.id == cid:
                del self.main[i]
                self.main.appendleft(m)
                return True
        return False

    def maybe_requeue_learning_card(self, card, next_day_at: int) -> None:
        if card.queue not in (consts.QUEUE_LRN, consts.QUEUE_PREVIEW) or card.due >= next_day_at:
            return
        entry = LearningEntry(due=card.due, id=card.id, mod=card.mod, reps=card.reps)
        cutoff = self.learn_ahead_cutoff()
        if entry.due <= cutoff and not self.main and self.intraday:
            # learning collapsed: avoid showing the same card twice in a row
            nxt = self.intraday[0]
            if nxt.due >= entry.due and nxt.due + 1 < cutoff:
                entry = LearningEntry(due=nxt.due + 1, id=card.id, mod=card.mod, reps=card.reps)
        self.insert_intraday(entry)

    def insert_intraday(self, entry: LearningEntry) -> None:
        if entry.due <= self.learn_ahead_cutoff():
            self.counts.learning += 1
        bisect.insort(self.intraday, entry)


# --------------------------------------------------------------- the builder


def _intersperse(one: list, two: list) -> list:
    """Port of Intersperser: spread `two` evenly through `one`."""
    out = []
    one_len, two_len = len(one), len(two)
    ratio = (one_len + 1) / (two_len + 1)
    i1 = i2 = 0
    while i1 < one_len or i2 < two_len:
        if i1 < one_len and i2 < two_len:
            if (i2 + 1) * ratio < (i1 + 1):
                out.append(two[i2])
                i2 += 1
            else:
                out.append(one[i1])
                i1 += 1
        elif i1 < one_len:
            out.append(one[i1])
            i1 += 1
        else:
            out.append(two[i2])
            i2 += 1
    return out


def _merge(first: list, second: list, mode: int, second_is_before: int) -> list:
    if mode == consts.MIX_WITH_REVIEWS:
        return _intersperse(first, second)
    if mode == second_is_before:
        return second + first
    return first + second


@dataclass
class _Due:
    id: int
    nid: int
    due: int
    ivl: int
    factor: int
    mod: int
    did: int
    odid: int
    reps: int
    ord: int


@dataclass
class _New:
    id: int
    nid: int
    ord: int
    mod: int
    did: int
    odid: int
    due: int
    hash: int = 0


class QueueBuilder:
    def __init__(self, col: "Collection", deck_id: int):
        self.col = col
        self.timing = col.timing_today()
        root = col.get_deck(deck_id)
        decks = [root] + [col.get_deck(d) for d in col.child_ids(deck_id)]
        if col.conf.get("applyAllParentLimits"):
            decks = [col.get_deck(p) for p in col.parent_ids(deck_id)] + decks
        self.root = root
        self.active_ids = [deck_id] + col.child_ids(deck_id)
        self.limits = LimitTree(col, decks, self.timing.days_elapsed)
        if col.conf.get("applyAllParentLimits"):
            self.limits.root = int(decks[0]["id"])
        if root["dyn"]:
            self.conf = None
        else:
            self.conf = col.config_for_deck(deck_id)
        self.seen_notes: dict[int, BuryMode] = {}
        self._bury_cache: dict[int, BuryMode] = {}
        self.new: list[_New] = []
        self.review: list[_Due] = []
        self.day_learning: list[_Due] = []
        self.learning: list[LearningEntry] = []

    def _bury_mode(self, home_did: int) -> BuryMode:
        mode = self._bury_cache.get(home_did)
        if mode is None:
            deck = self.col.get_deck(home_did)
            if deck is None or deck["dyn"]:
                mode = BuryMode()
            else:
                mode = BuryMode.from_config(self.col.config_for_deck(home_did))
            self._bury_cache[home_did] = mode
        return mode

    def _update_bury_mode(self, nid: int, home_did: int) -> BuryMode | None:
        new_mode = self._bury_mode(home_did)
        previous = self.seen_notes.get(nid)
        if previous is None:
            self.seen_notes[nid] = BuryMode(new_mode.bury_new, new_mode.bury_reviews, new_mode.bury_interday_learning)
            return None
        snapshot = BuryMode(previous.bury_new, previous.bury_reviews, previous.bury_interday_learning)
        previous.bury_new |= new_mode.bury_new
        previous.bury_reviews |= new_mode.bury_reviews
        previous.bury_interday_learning |= new_mode.bury_interday_learning
        return snapshot

    def _in_active(self) -> str:
        return ",".join(str(int(d)) for d in self.active_ids)

    # ------------------------------------------------------------ gathering

    def gather(self) -> None:
        self._gather_intraday_learning()
        self._gather_due(consts.QUEUE_DAY_LRN)
        self._gather_due(consts.QUEUE_REV)
        self._gather_new()

    def _gather_intraday_learning(self) -> None:
        rows = self.col.db.execute(
            f"SELECT id, nid, due, mod, did, odid, reps FROM cards WHERE did IN ({self._in_active()}) "
            f"AND queue IN ({consts.QUEUE_LRN},{consts.QUEUE_PREVIEW}) AND due <= ?",
            (self.timing.next_day_at,),
        ).fetchall()
        for cid, nid, due, mod, did, odid, reps in rows:
            self._update_bury_mode(nid, odid or did)
            self.learning.append(LearningEntry(due=due, id=cid, mod=mod, reps=reps))

    def _review_sort_key(self, order: int):
        today = self.timing.days_elapsed
        names = {int(d["id"]): d["name"].lower() for d in self.col.decks.values()}

        def rnd(c: _Due) -> int:
            return fnv1a_64(c.id, c.mod)

        if order == consts.REVIEW_ORDER_DAY_THEN_DECK:
            return lambda c: (c.due, names.get(c.did, ""), rnd(c))
        if order == consts.REVIEW_ORDER_DECK_THEN_DAY:
            return lambda c: (names.get(c.did, ""), c.due, rnd(c))
        if order == consts.REVIEW_ORDER_INTERVALS_ASCENDING:
            return lambda c: (c.ivl, rnd(c))
        if order == consts.REVIEW_ORDER_INTERVALS_DESCENDING:
            return lambda c: (-c.ivl, rnd(c))
        if order == consts.REVIEW_ORDER_EASE_ASCENDING:
            return lambda c: (c.factor, rnd(c))
        if order == consts.REVIEW_ORDER_EASE_DESCENDING:
            return lambda c: (-c.factor, rnd(c))
        if order == consts.REVIEW_ORDER_RELATIVE_OVERDUENESS:
            return lambda c: (-((today - c.due) / max(c.ivl, 1)), rnd(c))
        if order == consts.REVIEW_ORDER_RANDOM:
            return rnd
        if order == consts.REVIEW_ORDER_ADDED:
            return lambda c: (c.nid, c.ord, rnd(c))
        if order == consts.REVIEW_ORDER_REVERSE_ADDED:
            return lambda c: (-c.nid, c.ord, rnd(c))
        return lambda c: (c.due, rnd(c))

    def _gather_due(self, queue: int) -> None:
        if self.limits.root_limit_reached(KIND_REVIEW):
            return
        rows = self.col.db.execute(
            f"SELECT id, nid, due, ivl, factor, mod, did, odid, reps, ord FROM cards "
            f"WHERE did IN ({self._in_active()}) AND queue = ? AND due <= ?",
            (queue, self.timing.days_elapsed),
        ).fetchall()
        cards = [_Due(*r) for r in rows]
        order = self.conf.get("reviewOrder", 0) if self.conf else consts.REVIEW_ORDER_DAY
        cards.sort(key=self._review_sort_key(order))
        for card in cards:
            if self.limits.root_limit_reached(KIND_REVIEW):
                break
            if self.limits.limit_reached(card.did, KIND_REVIEW):
                continue
            previous = self._update_bury_mode(card.nid, card.odid or card.did)
            if previous is not None:
                bury = previous.bury_interday_learning if queue == consts.QUEUE_DAY_LRN else previous.bury_reviews
                if bury:
                    continue
            (self.day_learning if queue == consts.QUEUE_DAY_LRN else self.review).append(card)
            self.limits.decrement(card.did, KIND_REVIEW)

    def _new_rows(self, dids: str, sort: str, salt: int) -> list[_New]:
        rows = self.col.db.execute(
            f"SELECT id, nid, ord, mod, did, odid, due FROM cards WHERE did IN ({dids}) AND queue = ?",
            (consts.QUEUE_NEW,),
        ).fetchall()
        cards = [_New(*r) for r in rows]
        if sort == "lowest":
            cards.sort(key=lambda c: (c.due, c.ord))
        elif sort == "highest":
            cards.sort(key=lambda c: (-c.due, c.ord))
        elif sort == "random_notes":
            cards.sort(key=lambda c: (fnv1a_64(c.nid, salt), c.ord))
        elif sort == "random_cards":
            cards.sort(key=lambda c: fnv1a_64(c.id, salt))
        return cards

    def _add_new(self, card: _New) -> None:
        if self.limits.limit_reached(card.did, KIND_NEW):
            return
        previous = self._update_bury_mode(card.nid, card.odid or card.did)
        if previous is not None and previous.bury_new:
            return
        self.new.append(card)
        self.limits.decrement(card.did, KIND_NEW)

    def _gather_new(self) -> None:
        salt = (self.timing.days_elapsed * 2654435761) & 0xFFFFFFFF
        priority = self.conf.get("newGatherPriority", 0) if self.conf else consts.NEW_GATHER_DECK
        if priority in (consts.NEW_GATHER_DECK, consts.NEW_GATHER_DECK_THEN_RANDOM_NOTES):
            sort = "lowest" if priority == consts.NEW_GATHER_DECK else "random_notes"
            names = {int(d["id"]): d["name"] for d in self.col.decks.values()}
            for did in sorted(self.active_ids, key=lambda d: [p.lower() for p in names[d].split("::")]):
                if self.limits.root_limit_reached(KIND_NEW):
                    break
                if self.limits.limit_reached(did, KIND_NEW):
                    continue
                for card in self._new_rows(str(did), sort, salt):
                    if self.limits.limit_reached(did, KIND_NEW):
                        break
                    self._add_new(card)
            return
        sort = {
            consts.NEW_GATHER_LOWEST_POSITION: "lowest",
            consts.NEW_GATHER_HIGHEST_POSITION: "highest",
            consts.NEW_GATHER_RANDOM_NOTES: "random_notes",
            consts.NEW_GATHER_RANDOM_CARDS: "random_cards",
        }.get(priority, "lowest")
        for card in self._new_rows(self._in_active(), sort, salt):
            if self.limits.root_limit_reached(KIND_NEW):
                break
            self._add_new(card)

    # -------------------------------------------------------------- building

    def _sort_new(self) -> None:
        order = self.conf.get("newSortOrder", 0) if self.conf else consts.NEW_SORT_NO_SORT
        salt = self.timing.days_elapsed
        if order == consts.NEW_SORT_TEMPLATE:
            self.new.sort(key=lambda c: c.ord)
        elif order == consts.NEW_SORT_TEMPLATE_THEN_RANDOM:
            self.new.sort(key=lambda c: (c.ord, fnv1a_64(c.id, salt)))
        elif order == consts.NEW_SORT_RANDOM_NOTE_THEN_TEMPLATE:
            self.new.sort(key=lambda c: (fnv1a_64(c.nid, salt), c.ord))
        elif order == consts.NEW_SORT_RANDOM_CARD:
            self.new.sort(key=lambda c: fnv1a_64(c.id, salt))

    def build(self, learn_ahead_secs: int) -> CardQueues:
        self._sort_new()
        self.learning.sort()
        cutoff = int(time.time()) + learn_ahead_secs
        learn_count = sum(1 for e in self.learning if e.due <= cutoff) + len(self.day_learning)
        reviews = [MainEntry(c.id, c.mod, KIND_REVIEW) for c in self.review]
        day_learning = [MainEntry(c.id, c.mod, KIND_DAY_LEARN) for c in self.day_learning]
        new = [MainEntry(c.id, c.mod, KIND_NEW) for c in self.new]
        day_mix = self.conf.get("interdayLearningMix", 0) if self.conf else consts.MIX_WITH_REVIEWS
        new_mix = self.conf.get("newMix", 0) if self.conf else consts.MIX_WITH_REVIEWS
        main = _merge(reviews, day_learning, day_mix, consts.MIX_BEFORE_REVIEWS)
        main = _merge(main, new, new_mix, consts.MIX_BEFORE_REVIEWS)
        return CardQueues(
            Counts(new=len(new), learning=learn_count, review=len(reviews)),
            main,
            self.learning,
            self.timing.days_elapsed,
            learn_ahead_secs,
            int(self.root["id"]),
        )

"""Statistics, modelled on the sections of Anki's / AnkiDroid's stats screen."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from . import consts

if TYPE_CHECKING:
    from .collection import Collection

MATURE_IVL = 21
DAY_MS = 86_400_000

# revlog types that count as answers for stats (manual/rescheduled entries are excluded)
ANSWER_TYPES = (consts.REVLOG_LRN, consts.REVLOG_REV, consts.REVLOG_RELRN, consts.REVLOG_FILTERED)


@dataclass
class TodayStats:
    cards: int = 0
    time_ms: int = 0
    again: int = 0
    learn: int = 0
    review: int = 0
    relearn: int = 0
    filtered: int = 0
    mature_count: int = 0
    mature_correct: int = 0

    @property
    def correct_pct(self) -> float | None:
        return 100 * (self.cards - self.again) / self.cards if self.cards else None


@dataclass
class FutureDue:
    days: list[int]
    overdue: int
    learning_today: int

    @property
    def tomorrow(self) -> int:
        return self.days[1] if len(self.days) > 1 else 0

    @property
    def total(self) -> int:
        return sum(self.days) + self.overdue


@dataclass
class CalendarStats:
    per_day: dict[int, int]  # day offset (0 = today, -1 = yesterday) -> reviews
    days_studied: int
    period_days: int
    current_streak: int
    longest_streak: int
    total: int

    @property
    def average_on_study_days(self) -> float:
        return self.total / self.days_studied if self.days_studied else 0.0

    @property
    def average_overall(self) -> float:
        return self.total / self.period_days if self.period_days else 0.0


REVIEW_KINDS = ("learn", "relearn", "young", "mature", "filtered")


@dataclass
class ReviewHistory:
    counts: list[dict[str, int]]  # oldest first, one entry per day
    times: list[dict[str, int]]   # milliseconds, same layout

    def total(self, kind: str | None = None) -> int:
        return sum(sum(d.values()) if kind is None else d[kind] for d in self.counts)

    def total_time_ms(self) -> int:
        return sum(sum(d.values()) for d in self.times)

    def days_studied(self) -> int:
        return sum(1 for d in self.counts if sum(d.values()))


@dataclass
class CardCounts:
    new: int = 0
    learning: int = 0
    relearning: int = 0
    young: int = 0
    mature: int = 0
    suspended: int = 0
    buried: int = 0

    @property
    def total(self) -> int:
        return self.new + self.learning + self.relearning + self.young + self.mature + self.suspended + self.buried


@dataclass
class Histogram:
    buckets: list[tuple[str, int]]
    total: int
    average: float | None = None
    median: float | None = None


@dataclass
class ButtonStats:
    # kind -> [again, hard, good, easy]
    counts: dict[str, list[int]] = field(default_factory=dict)

    def correct_pct(self, kind: str) -> float | None:
        row = self.counts.get(kind, [0, 0, 0, 0])
        total = sum(row)
        return 100 * (total - row[0]) / total if total else None


@dataclass
class RetentionRow:
    label: str
    young_pass: int = 0
    young_fail: int = 0
    mature_pass: int = 0
    mature_fail: int = 0

    @staticmethod
    def _pct(passed: int, failed: int) -> float | None:
        total = passed + failed
        return 100 * passed / total if total else None

    @property
    def young(self) -> float | None:
        return self._pct(self.young_pass, self.young_fail)

    @property
    def mature(self) -> float | None:
        return self._pct(self.mature_pass, self.mature_fail)

    @property
    def total(self) -> float | None:
        return self._pct(self.young_pass + self.mature_pass, self.young_fail + self.mature_fail)

    @property
    def count(self) -> int:
        return self.young_pass + self.young_fail + self.mature_pass + self.mature_fail


class Stats:
    """Statistics for one deck (with its subdecks), or the whole collection when did is None."""

    def __init__(self, col: "Collection", did: int | None = None):
        self.col = col
        self.did = did
        self.timing = col.timing_today()
        self.today = self.timing.days_elapsed
        self.day_start_ms = (self.timing.next_day_at - 86_400) * 1000
        if did is None:
            self.card_filter = "1"
            self.revlog_filter = "1"
        else:
            ids = ",".join(str(d) for d in col.child_ids(did, include_self=True))
            self.card_filter = f"(did IN ({ids}) OR odid IN ({ids}))"
            self.revlog_filter = f"cid IN (SELECT id FROM cards WHERE {self.card_filter})"

    def _q(self, sql: str, args: tuple = ()):
        return self.col.db.execute(sql, args)

    def _day_offset(self, revlog_id_ms: int) -> int:
        return (revlog_id_ms - self.day_start_ms) // DAY_MS

    def _revlog(self, days: int | None, columns: str):
        cutoff = self.day_start_ms - (days - 1) * DAY_MS if days else 0
        types = ",".join(str(t) for t in ANSWER_TYPES)
        return self._q(
            f"SELECT {columns} FROM revlog WHERE id >= ? AND ease > 0 AND type IN ({types}) "
            f"AND {self.revlog_filter} ORDER BY id",
            (cutoff,),
        ).fetchall()

    @staticmethod
    def _kind(rtype: int, last_ivl: int) -> str:
        if rtype == consts.REVLOG_LRN:
            return "learn"
        if rtype == consts.REVLOG_RELRN:
            return "relearn"
        if rtype == consts.REVLOG_FILTERED:
            return "filtered"
        return "mature" if last_ivl >= MATURE_IVL else "young"

    # ------------------------------------------------------------- sections

    def today_stats(self) -> TodayStats:
        s = TodayStats()
        for ease, rtype, last_ivl, taken in self._revlog(1, "ease, type, lastIvl, time"):
            s.cards += 1
            s.time_ms += taken
            if ease == 1:
                s.again += 1
            kind = self._kind(rtype, last_ivl)
            if kind == "learn":
                s.learn += 1
            elif kind == "relearn":
                s.relearn += 1
            elif kind == "filtered":
                s.filtered += 1
            else:
                s.review += 1
                if kind == "mature":
                    s.mature_count += 1
                    if ease > 1:
                        s.mature_correct += 1
        return s

    def future_due(self, days: int = 30) -> FutureDue:
        buckets = [0] * days
        overdue = 0
        for due, n in self._q(
            "SELECT (CASE WHEN odid != 0 AND odue != 0 THEN odue ELSE due END) AS d, count() FROM cards "
            f"WHERE queue IN ({consts.QUEUE_REV},{consts.QUEUE_DAY_LRN}) AND {self.card_filter} GROUP BY d"
        ):
            offset = due - self.today
            if offset < 0:
                overdue += n
            elif offset < days:
                buckets[offset] += n
        learning = self._q(
            f"SELECT count() FROM cards WHERE queue IN ({consts.QUEUE_LRN},{consts.QUEUE_PREVIEW}) "
            f"AND due < ? AND {self.card_filter}",
            (self.timing.next_day_at,),
        ).fetchone()[0]
        buckets[0] += learning
        return FutureDue(buckets, overdue, learning)

    def calendar(self, days: int = 365) -> CalendarStats:
        per_day: dict[int, int] = {}
        for (rid,) in self._revlog(None, "id"):
            off = self._day_offset(rid)
            per_day[off] = per_day.get(off, 0) + 1
        in_period = {d: n for d, n in per_day.items() if d > -days}
        # streaks consider the whole history; today not studied yet doesn't break a streak
        current = 0
        day = 0 if 0 in per_day else -1
        while day in per_day:
            current += 1
            day -= 1
        longest = run = 0
        prev = None
        for d in sorted(per_day):
            run = run + 1 if prev is not None and d == prev + 1 else 1
            longest = max(longest, run)
            prev = d
        return CalendarStats(
            per_day=in_period,
            days_studied=len(in_period),
            period_days=days,
            current_streak=current,
            longest_streak=longest,
            total=sum(in_period.values()),
        )

    def review_history(self, days: int) -> ReviewHistory:
        counts = [{k: 0 for k in REVIEW_KINDS} for _ in range(days)]
        times = [{k: 0 for k in REVIEW_KINDS} for _ in range(days)]
        for rid, rtype, last_ivl, taken in self._revlog(days, "id, type, lastIvl, time"):
            idx = days - 1 + self._day_offset(rid)
            if 0 <= idx < days:
                kind = self._kind(rtype, last_ivl)
                counts[idx][kind] += 1
                times[idx][kind] += taken
        return ReviewHistory(counts, times)

    def card_counts(self) -> CardCounts:
        c = CardCounts()
        for ctype, queue, ivl, n in self._q(
            f"SELECT type, queue, ivl >= {MATURE_IVL}, count() FROM cards WHERE {self.card_filter} "
            "GROUP BY type, queue, ivl >= ?",
            (MATURE_IVL,),
        ):
            if queue == consts.QUEUE_SUSPENDED:
                c.suspended += n
            elif queue in (consts.QUEUE_SCHED_BURIED, consts.QUEUE_USER_BURIED):
                c.buried += n
            elif ctype == consts.CARD_TYPE_NEW:
                c.new += n
            elif ctype == consts.CARD_TYPE_LRN:
                c.learning += n
            elif ctype == consts.CARD_TYPE_RELEARNING:
                c.relearning += n
            elif ivl:
                c.mature += n
            else:
                c.young += n
        return c

    def intervals(self) -> Histogram:
        ivls = [
            r[0]
            for r in self._q(
                f"SELECT ivl FROM cards WHERE type IN ({consts.CARD_TYPE_REV},{consts.CARD_TYPE_RELEARNING}) "
                f"AND {self.card_filter} ORDER BY ivl"
            )
        ]
        edges = [(1, "1d"), (2, "2d"), (4, "3-4d"), (7, "5-7d"), (14, "1-2w"), (30, "2-4w"),
                 (60, "1-2mo"), (120, "2-4mo"), (180, "4-6mo"), (365, "6-12mo"), (730, "1-2y"), (10**9, "2y+")]
        return _histogram(ivls, edges)

    def eases(self) -> Histogram:
        eases = [
            r[0] // 10
            for r in self._q(
                f"SELECT factor FROM cards WHERE type IN ({consts.CARD_TYPE_REV},{consts.CARD_TYPE_RELEARNING}) "
                f"AND factor > 0 AND {self.card_filter} ORDER BY factor"
            )
        ]
        edges = [(149, "130-149%"), (179, "150-179%"), (209, "180-209%"), (239, "210-239%"),
                 (259, "240-259%"), (279, "260-279%"), (309, "280-309%"), (10**9, "310%+")]
        return _histogram(eases, edges, unit="%")

    def hourly(self, days: int | None) -> list[tuple[int, int]]:
        """[(reviews, correct)] for each hour of the day, in local time."""
        hours = [[0, 0] for _ in range(24)]
        for rid, ease in self._revlog(days, "id, ease"):
            h = datetime.fromtimestamp(rid / 1000).hour
            hours[h][0] += 1
            if ease > 1:
                hours[h][1] += 1
        return [(a, b) for a, b in hours]

    def buttons(self, days: int | None) -> ButtonStats:
        out = ButtonStats({"learning": [0] * 4, "young": [0] * 4, "mature": [0] * 4})
        for ease, rtype, last_ivl in self._revlog(days, "ease, type, lastIvl"):
            kind = self._kind(rtype, last_ivl)
            group = "learning" if kind in ("learn", "relearn", "filtered") else kind
            if 1 <= ease <= 4:
                out.counts[group][ease - 1] += 1
        return out

    def added(self, days: int) -> list[int]:
        out = [0] * days
        cutoff = self.day_start_ms - (days - 1) * DAY_MS
        for (cid,) in self._q(f"SELECT id FROM cards WHERE id >= ? AND {self.card_filter}", (cutoff,)):
            idx = days - 1 + (cid - self.day_start_ms) // DAY_MS
            if 0 <= idx < days:
                out[idx] += 1
        return out

    def true_retention(self) -> list[RetentionRow]:
        periods = [("Today", 0, 1), ("Yesterday", -1, 1), ("Last week", 0, 7), ("Last month", 0, 30),
                   ("Last year", 0, 365), ("All time", 0, None)]
        rows = []
        entries = [
            (self._day_offset(rid), ease, last_ivl)
            for rid, ease, last_ivl in self._q(
                f"SELECT id, ease, lastIvl FROM revlog WHERE type = {consts.REVLOG_REV} AND ease > 0 "
                f"AND {self.revlog_filter}"
            )
        ]
        for label, end, length in periods:
            row = RetentionRow(label)
            for off, ease, last_ivl in entries:
                if off > end or (length is not None and off <= end - length):
                    continue
                mature = last_ivl >= MATURE_IVL
                if ease > 1:
                    if mature:
                        row.mature_pass += 1
                    else:
                        row.young_pass += 1
                elif mature:
                    row.mature_fail += 1
                else:
                    row.young_fail += 1
            rows.append(row)
        return rows

    def first_review_day(self) -> int | None:
        row = self._q(f"SELECT min(id) FROM revlog WHERE {self.revlog_filter}").fetchone()
        return self._day_offset(row[0]) if row and row[0] else None


def _histogram(values: list[int], edges: list[tuple[int, str]], unit: str = "d") -> Histogram:
    buckets = [[label, 0] for _, label in edges]
    for v in values:
        for i, (upper, _) in enumerate(edges):
            if v <= upper:
                buckets[i][1] += 1
                break
    avg = sum(values) / len(values) if values else None
    median = values[len(values) // 2] if values else None
    return Histogram([(label, n) for label, n in buckets], len(values), avg, median)


def now_ms() -> int:
    return int(time.time() * 1000)

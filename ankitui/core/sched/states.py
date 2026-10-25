"""Card states and their transitions.

A faithful port of rslib/src/scheduler/states (the SM-2 based v3 scheduler
shared by Anki desktop and AnkiDroid). FSRS is not implemented.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field, replace
from typing import Union

from ..consts import REVLOG_FILTERED, REVLOG_LRN, REVLOG_RELRN, REVLOG_REV
from .fuzz import rust_round, with_review_fuzz
from .steps import LearningSteps

INITIAL_EASE_FACTOR = 2.5
MINIMUM_EASE_FACTOR = 1.3
EASE_FACTOR_AGAIN_DELTA = -0.2
EASE_FACTOR_HARD_DELTA = -0.15
EASE_FACTOR_EASY_DELTA = 0.15


def f32(x: float) -> float:
    """Round a Python float to single precision, matching Rust's f32 maths."""
    return struct.unpack("f", struct.pack("f", x))[0]


@dataclass(frozen=True)
class IntervalKind:
    secs: int | None = None
    days: int | None = None

    @staticmethod
    def in_secs(secs: int) -> "IntervalKind":
        return IntervalKind(secs=secs)

    @staticmethod
    def in_days(days: int) -> "IntervalKind":
        return IntervalKind(days=days)

    @property
    def is_days(self) -> bool:
        return self.days is not None

    def maybe_as_days(self, secs_until_rollover: int) -> "IntervalKind":
        if self.secs is not None and self.secs >= secs_until_rollover:
            return IntervalKind.in_days((self.secs - secs_until_rollover) // 86_400 + 1)
        return self

    def as_seconds(self) -> int:
        if self.secs is not None:
            return self.secs
        return (self.days or 0) * 86_400

    def as_revlog_interval(self) -> int:
        if self.days is not None:
            return self.days
        return -(self.secs or 0)


@dataclass
class StateContext:
    fuzz_factor: float | None
    steps: LearningSteps
    graduating_interval_good: int
    graduating_interval_easy: int
    initial_ease_factor: float
    hard_multiplier: float
    easy_multiplier: float
    interval_multiplier: float
    maximum_review_interval: int
    leech_threshold: int
    relearn_steps: LearningSteps
    lapse_multiplier: float
    minimum_lapse_interval: int
    in_filtered_deck: bool = False
    preview_again: int = 0
    preview_hard: int = 0
    preview_good: int = 0

    def min_and_max_review_intervals(self, minimum: int) -> tuple[int, int]:
        maximum = max(self.maximum_review_interval, 1)
        minimum = min(max(minimum, 1), maximum)
        return minimum, maximum

    def with_review_fuzz(self, interval: float, minimum: int, maximum: int) -> int:
        return with_review_fuzz(self.fuzz_factor, interval, minimum, maximum)

    @staticmethod
    def defaults_for_testing() -> "StateContext":
        return StateContext(
            fuzz_factor=None,
            steps=LearningSteps([1.0, 10.0]),
            graduating_interval_good=1,
            graduating_interval_easy=4,
            initial_ease_factor=2.5,
            hard_multiplier=1.2,
            easy_multiplier=1.3,
            interval_multiplier=1.0,
            maximum_review_interval=36500,
            leech_threshold=8,
            relearn_steps=LearningSteps([10.0]),
            lapse_multiplier=0.0,
            minimum_lapse_interval=1,
        )


@dataclass(frozen=True)
class NewState:
    position: int = 0

    def interval_kind(self) -> IntervalKind:
        return IntervalKind.in_secs(0)

    def revlog_kind(self) -> int:
        return REVLOG_LRN

    def next_states(self, ctx: StateContext) -> "SchedulingStates":
        nxt = LearnState(
            remaining_steps=ctx.steps.remaining_for_failed(), scheduled_secs=0
        ).next_states(ctx)
        return replace(nxt, current=self)


@dataclass(frozen=True)
class LearnState:
    remaining_steps: int
    scheduled_secs: int
    elapsed_secs: int = 0

    def interval_kind(self) -> IntervalKind:
        return IntervalKind.in_secs(self.scheduled_secs)

    def revlog_kind(self) -> int:
        return REVLOG_LRN

    def next_states(self, ctx: StateContext) -> "SchedulingStates":
        return SchedulingStates(
            current=self,
            again=self._answer_again(ctx),
            hard=self._answer_hard(ctx),
            good=self._answer_good(ctx),
            easy=self._answer_easy(ctx),
        )

    def _graduate(self, ctx: StateContext, interval: float) -> "ReviewState":
        minimum, maximum = ctx.min_and_max_review_intervals(1)
        return ReviewState(
            scheduled_days=ctx.with_review_fuzz(max(float(rust_round(interval)), 1.0), minimum, maximum),
            ease_factor=ctx.initial_ease_factor,
        )

    def _answer_again(self, ctx: StateContext) -> "CardState":
        delay = ctx.steps.again_delay_secs_learn()
        if delay is not None:
            return LearnState(remaining_steps=ctx.steps.remaining_for_failed(), scheduled_secs=delay)
        return self._graduate(ctx, float(ctx.graduating_interval_good))

    def _answer_hard(self, ctx: StateContext) -> "CardState":
        delay = ctx.steps.hard_delay_secs(self.remaining_steps)
        if delay is not None:
            return LearnState(remaining_steps=self.remaining_steps, scheduled_secs=delay)
        return self._graduate(ctx, float(ctx.graduating_interval_good))

    def _answer_good(self, ctx: StateContext) -> "CardState":
        delay = ctx.steps.good_delay_secs(self.remaining_steps)
        if delay is not None:
            return LearnState(
                remaining_steps=ctx.steps.remaining_for_good(self.remaining_steps),
                scheduled_secs=delay,
            )
        return self._graduate(ctx, float(ctx.graduating_interval_good))

    def _answer_easy(self, ctx: StateContext) -> "ReviewState":
        minimum, maximum = ctx.min_and_max_review_intervals(1)
        return ReviewState(
            scheduled_days=ctx.with_review_fuzz(float(ctx.graduating_interval_easy), minimum, maximum),
            ease_factor=ctx.initial_ease_factor,
        )


@dataclass(frozen=True)
class ReviewState:
    scheduled_days: int = 0
    elapsed_days: int = 0
    ease_factor: float = INITIAL_EASE_FACTOR
    lapses: int = 0
    leeched: bool = False

    def days_late(self) -> int:
        return self.elapsed_days - self.scheduled_days

    def interval_kind(self) -> IntervalKind:
        return IntervalKind.in_days(self.scheduled_days)

    def revlog_kind(self) -> int:
        return REVLOG_FILTERED if self.days_late() < 0 else REVLOG_REV

    def next_states(self, ctx: StateContext) -> "SchedulingStates":
        hard, good, easy = self._passing_review_intervals(ctx)
        return SchedulingStates(
            current=self,
            again=self._answer_again(ctx),
            hard=replace(
                self,
                scheduled_days=hard,
                elapsed_days=0,
                ease_factor=f32(max(self.ease_factor + EASE_FACTOR_HARD_DELTA, MINIMUM_EASE_FACTOR)),
            ),
            good=replace(self, scheduled_days=good, elapsed_days=0),
            easy=replace(
                self,
                scheduled_days=easy,
                elapsed_days=0,
                ease_factor=f32(self.ease_factor + EASE_FACTOR_EASY_DELTA),
            ),
        )

    def failing_review_interval(self, ctx: StateContext) -> float:
        minimum, maximum = ctx.min_and_max_review_intervals(ctx.minimum_lapse_interval)
        interval = ctx.with_review_fuzz(
            f32(max(float(self.scheduled_days), 1.0) * ctx.lapse_multiplier), minimum, maximum
        )
        return float(interval)

    def _answer_again(self, ctx: StateContext) -> "CardState":
        lapses = self.lapses + 1
        leeched = leech_threshold_met(lapses, ctx.leech_threshold)
        scheduled_days = self.failing_review_interval(ctx)
        again_review = ReviewState(
            scheduled_days=max(rust_round(scheduled_days), 1),
            elapsed_days=0,
            ease_factor=f32(max(self.ease_factor + EASE_FACTOR_AGAIN_DELTA, MINIMUM_EASE_FACTOR)),
            lapses=lapses,
            leeched=leeched,
        )
        delay = ctx.relearn_steps.again_delay_secs_learn()
        if delay is not None:
            return RelearnState(
                learning=LearnState(
                    remaining_steps=ctx.relearn_steps.remaining_for_failed(),
                    scheduled_secs=delay,
                ),
                review=again_review,
            )
        return again_review

    def _passing_review_intervals(self, ctx: StateContext) -> tuple[int, int, int]:
        if self.days_late() < 0:
            return self._passing_early_review_intervals(ctx)
        return self._passing_nonearly_review_intervals(ctx)

    def _passing_nonearly_review_intervals(self, ctx: StateContext) -> tuple[int, int, int]:
        current_interval = max(float(self.scheduled_days), 1.0)
        days_late = float(max(self.days_late(), 0))
        hard_factor = ctx.hard_multiplier
        hard_minimum = 0 if hard_factor <= 1.0 else self.scheduled_days + 1
        hard_interval = constrain_passing_interval(
            ctx, f32(current_interval * hard_factor), hard_minimum, True
        )
        good_minimum = self.scheduled_days + 1 if hard_factor <= 1.0 else hard_interval + 1
        good_interval = constrain_passing_interval(
            ctx, f32(f32(current_interval + days_late / 2.0) * self.ease_factor), good_minimum, True
        )
        easy_interval = constrain_passing_interval(
            ctx,
            f32(f32((current_interval + days_late) * self.ease_factor) * ctx.easy_multiplier),
            good_interval + 1,
            True,
        )
        return hard_interval, good_interval, easy_interval

    def _passing_early_review_intervals(self, ctx: StateContext) -> tuple[int, int, int]:
        scheduled = max(float(self.scheduled_days), 1.0)
        elapsed = float(self.elapsed_days)
        factor = ctx.hard_multiplier
        half_usual = factor / 2.0
        hard_interval = constrain_passing_interval(
            ctx, f32(max(elapsed * factor, scheduled * half_usual)), 0, False
        )
        good_interval = constrain_passing_interval(
            ctx, f32(max(elapsed * self.ease_factor, scheduled)), 0, False
        )
        reduced_bonus = ctx.easy_multiplier - (ctx.easy_multiplier - 1.0) / 2.0
        easy_interval = constrain_passing_interval(
            ctx, f32(max(elapsed * self.ease_factor, scheduled) * reduced_bonus), 0, False
        )
        return hard_interval, good_interval, easy_interval


@dataclass(frozen=True)
class RelearnState:
    learning: LearnState
    review: ReviewState

    def interval_kind(self) -> IntervalKind:
        return self.learning.interval_kind()

    def revlog_kind(self) -> int:
        return REVLOG_RELRN

    def next_states(self, ctx: StateContext) -> "SchedulingStates":
        return SchedulingStates(
            current=self,
            again=self._answer_again(ctx),
            hard=self._answer_hard(ctx),
            good=self._answer_good(ctx),
            easy=self._answer_easy(ctx),
        )

    def _answer_again(self, ctx: StateContext) -> "CardState":
        scheduled_days = self.review.failing_review_interval(ctx)
        delay = ctx.relearn_steps.again_delay_secs_learn()
        if delay is not None:
            return RelearnState(
                learning=LearnState(
                    remaining_steps=ctx.relearn_steps.remaining_for_failed(),
                    scheduled_secs=delay,
                ),
                review=replace(
                    self.review,
                    scheduled_days=max(rust_round(scheduled_days), 1),
                    elapsed_days=0,
                ),
            )
        return self.review

    def _answer_hard(self, ctx: StateContext) -> "CardState":
        delay = ctx.relearn_steps.hard_delay_secs(self.learning.remaining_steps)
        if delay is not None:
            return RelearnState(
                learning=replace(self.learning, scheduled_secs=delay),
                review=replace(self.review, elapsed_days=0),
            )
        return self.review

    def _answer_good(self, ctx: StateContext) -> "CardState":
        delay = ctx.relearn_steps.good_delay_secs(self.learning.remaining_steps)
        if delay is not None:
            return RelearnState(
                learning=LearnState(
                    remaining_steps=ctx.relearn_steps.remaining_for_good(self.learning.remaining_steps),
                    scheduled_secs=delay,
                ),
                review=replace(self.review, elapsed_days=0),
            )
        return self.review

    def _answer_easy(self, ctx: StateContext) -> "ReviewState":
        return replace(self.review, scheduled_days=self.review.scheduled_days + 1, elapsed_days=0)


@dataclass(frozen=True)
class PreviewState:
    scheduled_secs: int
    finished: bool = False

    def interval_kind(self) -> IntervalKind:
        return IntervalKind.in_secs(self.scheduled_secs)

    def revlog_kind(self) -> int:
        return REVLOG_FILTERED

    def next_states(self, ctx: StateContext) -> "SchedulingStates":
        return SchedulingStates(
            current=self,
            again=_delay_or_return(ctx.preview_again),
            hard=_delay_or_return(ctx.preview_hard),
            good=_delay_or_return(ctx.preview_good),
            easy=_delay_or_return(0),
        )


def _delay_or_return(seconds: int) -> PreviewState:
    if seconds == 0:
        return PreviewState(scheduled_secs=0, finished=True)
    return PreviewState(scheduled_secs=seconds, finished=False)


NormalState = Union[NewState, LearnState, ReviewState, RelearnState]


@dataclass(frozen=True)
class ReschedulingFilterState:
    original_state: NormalState

    def interval_kind(self) -> IntervalKind:
        return self.original_state.interval_kind()

    def revlog_kind(self) -> int:
        return self.original_state.revlog_kind()

    def next_states(self, ctx: StateContext) -> "SchedulingStates":
        normal = self.original_state.next_states(ctx)
        if ctx.in_filtered_deck:
            return SchedulingStates(
                current=self,
                again=_maybe_wrap(normal.again),
                hard=_maybe_wrap(normal.hard),
                good=_maybe_wrap(normal.good),
                easy=_maybe_wrap(normal.easy),
            )
        return normal


def _maybe_wrap(state: "CardState") -> "CardState":
    if isinstance(state, ReviewState):
        return state
    return ReschedulingFilterState(original_state=state)


CardState = Union[NewState, LearnState, ReviewState, RelearnState, PreviewState, ReschedulingFilterState]


@dataclass(frozen=True)
class SchedulingStates:
    current: CardState
    again: CardState
    hard: CardState
    good: CardState
    easy: CardState

    def for_ease(self, ease: int) -> CardState:
        return (self.again, self.hard, self.good, self.easy)[ease - 1]


def review_state_of(state: CardState) -> ReviewState | None:
    if isinstance(state, ReviewState):
        return state
    if isinstance(state, RelearnState):
        return state.review
    if isinstance(state, ReschedulingFilterState):
        return review_state_of(state.original_state)
    return None


def is_leeched(state: CardState) -> bool:
    review = review_state_of(state)
    return bool(review and review.leeched)


def new_position_of(state: CardState) -> int | None:
    if isinstance(state, NewState):
        return state.position
    if isinstance(state, ReschedulingFilterState) and isinstance(state.original_state, NewState):
        return state.original_state.position
    return None


def leech_threshold_met(lapses: int, threshold: int) -> bool:
    if threshold > 0:
        half_threshold = max(int(math.ceil(threshold / 2.0)), 1)
        return lapses >= threshold and (lapses - threshold) % half_threshold == 0
    return False


def constrain_passing_interval(ctx: StateContext, interval: float, minimum: int, fuzz: bool) -> int:
    interval = f32(interval * ctx.interval_multiplier)
    minimum, maximum = ctx.min_and_max_review_intervals(minimum)
    if fuzz:
        return ctx.with_review_fuzz(interval, minimum, maximum)
    return min(max(rust_round(interval), minimum), maximum)

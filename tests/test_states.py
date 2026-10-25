"""Ports of the unit tests in rslib/src/scheduler/states."""

from ankitui.core.sched.fuzz import constrained_fuzz_bounds, with_review_fuzz
from ankitui.core.sched.states import (
    LearnState,
    NewState,
    RelearnState,
    ReviewState,
    StateContext,
    leech_threshold_met,
)
from ankitui.core.sched.steps import DAY, LearningSteps, _maybe_round_in_days


def _delays(steps, remaining, again, hard, good):
    s = LearningSteps(steps)
    assert s.again_delay_secs_learn() == again
    assert s.hard_delay_secs(remaining) == hard
    assert s.good_delay_secs(remaining) == good


def test_delay_secs():
    _delays([10.0], 1, 600, 900, None)
    _delays([3 * DAY / 60], 1, 3 * DAY, 4 * DAY, None)
    _delays([1.0, 10.0], 2, 60, 330, 600)
    _delays([1.0, 10.0], 1, 60, 600, None)
    _delays([1.0, 10.0, 100.0], 3, 60, 330, 600)
    _delays([1.0, 10.0, 100.0], 2, 60, 600, 6000)
    _delays([1.0, 10.0, 100.0], 1, 60, 6000, None)


def test_rounding_days():
    assert _maybe_round_in_days(DAY - 1) == DAY - 1
    assert _maybe_round_in_days(int(1.5 * DAY)) == 2 * DAY


def test_review_fuzz():
    assert with_review_fuzz(None, 1.5, 1, 100) == 2
    assert with_review_fuzz(None, 0.1, 1, 100) == 1
    assert with_review_fuzz(None, 101.0, 1, 100) == 100
    cases = [
        (1.0, 1, 1000, 1, 1, 1),
        (2.49, 1, 1000, 2, 2, 2),
        (2.5, 1, 1000, 2, 3, 4),
        (7.0, 1, 1000, 5, 7, 9),
        (17.0, 1, 1000, 14, 17, 20),
        (37.0, 1, 1000, 33, 37, 41),
        (2.0, 2, 1000, 2, 2, 2),
        (2.0, 3, 1000, 3, 4, 4),
        (2.0, 3, 3, 3, 3, 3),
        (6.9, 3, 1000, 5, 7, 9),
        (19.9, 3, 1000, 17, 20, 23),
        (100.0, 101, 1000, 101, 105, 108),
        (100.0, 1, 99, 92, 96, 99),
        (100.0, 97, 103, 97, 100, 103),
    ]
    for interval, lo, hi, lower, middle, upper in cases:
        assert with_review_fuzz(0.0, interval, lo, hi) == lower
        assert with_review_fuzz(0.5, interval, lo, hi) == middle
        assert with_review_fuzz(0.99, interval, lo, hi) == upper


def test_invalid_fuzz_values_do_not_fail():
    constrained_fuzz_bounds(1.0, 3, 2)


def test_leech_threshold():
    assert not leech_threshold_met(0, 3)
    assert not leech_threshold_met(1, 3)
    assert leech_threshold_met(3, 3)
    assert not leech_threshold_met(4, 3)
    assert leech_threshold_met(5, 3)
    assert leech_threshold_met(7, 3)
    assert not leech_threshold_met(5, 0)


def test_new_card_transitions():
    ctx = StateContext.defaults_for_testing()
    s = NewState(1).next_states(ctx)
    assert s.again == LearnState(2, 60)
    assert s.hard == LearnState(2, 330)
    assert s.good == LearnState(1, 600)
    assert isinstance(s.easy, ReviewState) and s.easy.scheduled_days == 4


def test_learning_graduates():
    ctx = StateContext.defaults_for_testing()
    s = LearnState(1, 600).next_states(ctx)
    assert isinstance(s.good, ReviewState) and s.good.scheduled_days == 1
    assert s.again == LearnState(2, 60)


def test_review_intervals():
    ctx = StateContext.defaults_for_testing()
    s = ReviewState(scheduled_days=10, elapsed_days=10, ease_factor=2.5).next_states(ctx)
    assert s.hard.scheduled_days == 12
    assert s.good.scheduled_days == 25
    assert s.easy.scheduled_days == 33
    assert isinstance(s.again, RelearnState)
    assert s.again.review.lapses == 1
    assert abs(s.again.review.ease_factor - 2.3) < 1e-6


def test_late_review_bonus():
    ctx = StateContext.defaults_for_testing()
    s = ReviewState(scheduled_days=10, elapsed_days=14, ease_factor=2.5).next_states(ctx)
    assert s.good.scheduled_days == 30  # (10 + 4/2) * 2.5
    assert s.easy.scheduled_days == 46  # (10 + 4) * 2.5 * 1.3 = 45.5

"""Learning steps, ported from rslib/src/scheduler/states/steps.rs."""

from __future__ import annotations

DAY = 86_400


def _to_secs(minutes: float) -> int:
    return int(minutes * 60.0)


def _maybe_round_in_days(secs: int) -> int:
    if secs > DAY:
        return int(_round_half_away(secs / DAY)) * DAY
    return secs


def _round_half_away(x: float) -> float:
    # Rust's f32::round rounds half away from zero, unlike Python's round().
    import math

    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


class LearningSteps:
    def __init__(self, steps: list[float]):
        self.steps = list(steps)

    def _get_index(self, remaining: int) -> int:
        total = len(self.steps)
        return min(max(total - (remaining % 1000), 0), max(total - 1, 0))

    def _secs_at_index(self, index: int) -> int | None:
        if 0 <= index < len(self.steps):
            return _to_secs(self.steps[index])
        return None

    def again_delay_secs_learn(self) -> int | None:
        return self._secs_at_index(0)

    def hard_delay_secs(self, remaining: int) -> int | None:
        idx = self._get_index(remaining)
        current = self._secs_at_index(idx)
        if current is None:
            if not self.steps:
                return None
            current = _to_secs(self.steps[0])
        if idx == 0:
            return self._hard_delay_secs_for_first_step(current)
        return current

    def _hard_delay_secs_for_first_step(self, again_secs: int) -> int:
        nxt = self._secs_at_index(1)
        if nxt is not None:
            return _maybe_round_in_days((again_secs + nxt) // 2)
        secs = min(again_secs * 3 // 2, again_secs + DAY)
        return _maybe_round_in_days(secs)

    def good_delay_secs(self, remaining: int) -> int | None:
        idx = self._get_index(remaining)
        return self._secs_at_index(idx + 1)

    def current_delay_secs(self, remaining: int) -> int:
        idx = self._get_index(remaining)
        return self._secs_at_index(idx) or 0

    def remaining_for_good(self, remaining: int) -> int:
        idx = self._get_index(remaining)
        return max(len(self.steps) - (idx + 1), 0)

    def remaining_for_failed(self) -> int:
        return len(self.steps)

    def is_empty(self) -> bool:
        return not self.steps

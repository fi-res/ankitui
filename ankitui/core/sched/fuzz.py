"""Review interval fuzz, ported from rslib/src/scheduler/states/fuzz.rs."""

from __future__ import annotations

import math
import random

_FUZZ_RANGES = (
    (2.5, 7.0, 0.15),
    (7.0, 20.0, 0.1),
    (20.0, float("inf"), 0.05),
)


def rust_round(x: float) -> int:
    """Round half away from zero (Rust semantics)."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def _fuzz_delta(interval: float) -> float:
    if interval < 2.5:
        return 0.0
    delta = 1.0
    for start, end, factor in _FUZZ_RANGES:
        delta += factor * max(min(interval, end) - start, 0.0)
    return delta


def fuzz_bounds(interval: float) -> tuple[int, int]:
    delta = _fuzz_delta(interval)
    return rust_round(interval - delta), rust_round(interval + delta)


def constrained_fuzz_bounds(interval: float, minimum: int, maximum: int) -> tuple[int, int]:
    minimum = min(minimum, maximum)
    interval = min(max(interval, float(minimum)), float(maximum))
    lower, upper = fuzz_bounds(interval)
    lower = min(max(lower, minimum), maximum)
    upper = min(max(upper, minimum), maximum)
    if upper == lower and upper > 2 and upper < maximum:
        upper = lower + 1
    return lower, upper


def with_review_fuzz(fuzz_factor: float | None, interval: float, minimum: int, maximum: int) -> int:
    if fuzz_factor is not None:
        lower, upper = constrained_fuzz_bounds(interval, minimum, maximum)
        return int(math.floor(lower + fuzz_factor * (1 + upper - lower)))
    return min(max(rust_round(interval), minimum), maximum)


def fuzz_seed(card_id: int, reps: int) -> int:
    return (card_id + reps) & 0xFFFFFFFFFFFFFFFF


def fuzz_factor_for_seed(seed: int | None) -> float | None:
    if seed is None:
        return None
    return random.Random(seed).random()


def learning_ivl_with_fuzz(seed: int | None, secs: int) -> int:
    """Up to 25% (max 5 minutes) of extra delay for learning steps."""
    if seed is None:
        return secs
    upper_exclusive = secs + int(math.floor(min(secs * 0.25, 300.0)))
    if secs >= upper_exclusive:
        return secs
    return random.Random(seed).randrange(secs, upper_exclusive)

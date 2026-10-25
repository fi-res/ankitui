"""Validated access to deck options, mirroring ensure_deck_config_values_valid()."""

from __future__ import annotations

# (min, max, default) per option, as enforced by rslib/src/deckconfig/mod.rs
LIMITS = {
    "new_per_day": (0, 9999, 20),
    "reviews_per_day": (0, 9999, 200),
    "initial_ease": (1.31, 5.0, 2.5),
    "easy_multiplier": (1.0, 5.0, 1.3),
    "hard_multiplier": (0.5, 1.3, 1.2),
    "lapse_multiplier": (0.0, 1.0, 0.0),
    "interval_multiplier": (0.5, 2.0, 1.0),
    "maximum_review_interval": (1, 36_500, 36_500),
    "minimum_lapse_interval": (1, 36_500, 1),
    "graduating_interval_good": (1, 36_500, 1),
    "graduating_interval_easy": (1, 36_500, 4),
    "leech_threshold": (1, 9999, 8),
    "cap_answer_time_to_secs": (1, 9999, 60),
}


def _valid(name: str, value) -> float:
    lo, hi, default = LIMITS[name]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    if value != value or value < lo or value > hi:
        return default
    return value


def sched_params(conf: dict) -> dict:
    ints = list(conf["new"].get("ints", [1, 4, 0])) + [1, 4]
    return {
        "learn_steps": [float(s) for s in conf["new"].get("delays", [])],
        "relearn_steps": [float(s) for s in conf["lapse"].get("delays", [])],
        "new_per_day": int(_valid("new_per_day", conf["new"].get("perDay", 20))),
        "reviews_per_day": int(_valid("reviews_per_day", conf["rev"].get("perDay", 200))),
        "initial_ease": _valid("initial_ease", conf["new"].get("initialFactor", 2500) / 1000.0),
        "easy_multiplier": _valid("easy_multiplier", conf["rev"].get("ease4", 1.3)),
        "hard_multiplier": _valid("hard_multiplier", conf["rev"].get("hardFactor", 1.2)),
        "lapse_multiplier": _valid("lapse_multiplier", conf["lapse"].get("mult", 0.0)),
        "interval_multiplier": _valid("interval_multiplier", conf["rev"].get("ivlFct", 1.0)),
        "maximum_review_interval": int(_valid("maximum_review_interval", conf["rev"].get("maxIvl", 36500))),
        "minimum_lapse_interval": int(_valid("minimum_lapse_interval", conf["lapse"].get("minInt", 1))),
        "graduating_interval_good": int(_valid("graduating_interval_good", ints[0])),
        "graduating_interval_easy": int(_valid("graduating_interval_easy", ints[1])),
        "leech_threshold": int(_valid("leech_threshold", conf["lapse"].get("leechFails", 8))),
        "leech_action": int(conf["lapse"].get("leechAction", 1)),
        "cap_answer_time_to_secs": int(_valid("cap_answer_time_to_secs", conf.get("maxTaken", 60))),
    }

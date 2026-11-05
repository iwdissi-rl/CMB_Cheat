"""Random pacing: roll a reading time per passage inside a user-set range."""

from __future__ import annotations

import random


def roll_seconds(min_minutes: float, max_minutes: float, rng: random.Random | None = None) -> float:
    """Pick a random whole-second time inside [min, max] minutes.

    Both bounds are minutes and may be fractional. min must be <= max and
    >= 0. Rolls are whole seconds so printed plans stay tidy.
    """
    if min_minutes < 0 or max_minutes < 0:
        raise ValueError("times must not be negative")
    if min_minutes > max_minutes:
        raise ValueError(f"min ({min_minutes}) is greater than max ({max_minutes})")
    rng = rng or random
    lo = int(round(min_minutes * 60))
    hi = int(round(max_minutes * 60))
    return float(rng.randint(lo, hi))


def parse_range(text: str) -> tuple[float, float]:
    """Parse ``5``, ``5-10`` or ``2.5-7`` (minutes) into a (min, max) tuple.

    A single number means min = max = that number.
    """
    t = text.strip()
    if "-" in t:
        lo, _, hi = t.partition("-")
        return (float(lo), float(hi))
    v = float(t)
    return (v, v)

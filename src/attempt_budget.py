"""Small deadline utility for bounded fallback chains.

Fallback chains should spend a predictable amount of wall-clock time on a
preferred provider before giving the next provider a chance.  This module keeps
that policy independent from any one downloader and makes the limits easy to
test and tune through environment variables.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass


class AttemptBudgetExhausted(TimeoutError):
    """Raised when a fallback chain has consumed its configured time budget."""


@dataclass(frozen=True)
class AttemptBudget:
    deadline: float
    clock: callable = time.monotonic

    @classmethod
    def from_seconds(cls, seconds: float, *, clock=time.monotonic) -> "AttemptBudget":
        return cls(deadline=clock() + max(0.0, seconds), clock=clock)

    @classmethod
    def from_env(
        cls,
        name: str,
        default: float,
        *,
        clock=time.monotonic,
    ) -> "AttemptBudget":
        raw = os.getenv(name, "").strip()
        seconds = default
        if raw:
            try:
                seconds = float(raw)
            except ValueError:
                seconds = default
        return cls.from_seconds(max(1.0, seconds), clock=clock)

    def remaining(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def timeout(self, cap_seconds: float, *, label: str) -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise AttemptBudgetExhausted(f"{label} skipped: fallback time budget exhausted")
        return max(1.0, min(max(1.0, cap_seconds), remaining))

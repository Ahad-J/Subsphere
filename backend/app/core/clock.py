"""Dependency-injected deterministic clock for domain logic and testing."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable


class Clock:
    def __init__(self, now_fn: Callable[[], datetime] | None = None):
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def now(self) -> datetime:
        dt = self._now_fn()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def now_iso(self) -> str:
        return self.now().isoformat().replace("+00:00", "Z")


_default_clock = Clock()


def get_clock() -> Clock:
    return _default_clock


def set_clock(clock: Clock) -> None:
    global _default_clock
    _default_clock = clock

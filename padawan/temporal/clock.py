from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """A clock boundary; operational and scenario clocks must be supplied explicitly."""

    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    """Real UTC wall time plus a monotonic duration source."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class VirtualClock:
    """Deterministic scenario clock that never sleeps or controls operational leases."""

    def __init__(self, initial: datetime, *, monotonic_seconds: float = 0.0) -> None:
        if initial.tzinfo is None or initial.utcoffset() is None:
            raise ValueError("virtual clock requires a timezone-aware initial instant")
        if monotonic_seconds < 0:
            raise ValueError("virtual monotonic time cannot be negative")
        self._now = initial.astimezone(UTC)
        self._monotonic = float(monotonic_seconds)

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, duration: timedelta) -> datetime:
        seconds = duration.total_seconds()
        if seconds < 0:
            raise ValueError("virtual clock cannot advance by a negative duration")
        self._now += duration
        self._monotonic += seconds
        return self._now

    def set(self, instant: datetime) -> datetime:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("virtual clock requires timezone-aware instants")
        normalized = instant.astimezone(UTC)
        if normalized < self._now:
            raise ValueError("virtual clock cannot move backwards")
        return self.advance(normalized - self._now)

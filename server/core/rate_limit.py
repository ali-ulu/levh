"""Small in-process sliding-window rate limiter.

This is deliberately dependency-free and is only used when the optional
STACKMEMORY_TOKEN gate is enabled. It is not a distributed quota system; its
purpose is to slow local/network brute-force attempts against a single
StackMemory process.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Thread-safe per-key sliding-window limiter."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = max(1, int(limit))
        self.window_seconds = max(0.001, float(window_seconds))
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for ``key``."""
        current = time.monotonic() if now is None else float(now)
        cutoff = current - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (current - events[0]) + 0.999))
                return False, retry_after
            events.append(current)
            return True, 0

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

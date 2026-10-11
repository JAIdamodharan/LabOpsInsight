"""Small in-memory sliding-window rate limiter.

Single-process by design (this app runs as one local server). If it is ever run behind several workers or a proxy,
swap this for a shared store such as Redis; the interface (`check`, `hit`, `count`, `reset`) is deliberately tiny.
"""
import threading
import time
from collections import deque


RETAIN = 86400.0     # keep a day of history per key so that different windows can be asked about the same key


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._ops = 0

    @staticmethod
    def _recent(q: deque, now: float, window: float) -> list[float]:
        """Timestamps within `window` (oldest first). Never mutates, so asking about a short window cannot erase history."""
        out = []
        for t in reversed(q):
            if t <= now - window:
                break
            out.append(t)
        out.reverse()
        return out

    def _append(self, key: str, now: float) -> None:
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - RETAIN:
            q.popleft()
        q.append(now)
        self._ops += 1
        if self._ops % 2000 == 0 and len(self._hits) > 5000:
            for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - RETAIN]:
                del self._hits[k]

    def check(self, key: str, limit: int, window: float) -> tuple[bool, float]:
        """Record one request and say whether it is allowed. Returns (allowed, seconds_until_a_slot_frees)."""
        now = self._clock()
        with self._lock:
            recent = self._recent(self._hits.get(key, deque()), now, window)
            if len(recent) >= limit:
                return False, max(0.0, recent[len(recent) - limit] + window - now)
            self._append(key, now)
            return True, 0.0

    def hit(self, key: str, window: float = RETAIN) -> None:
        """Record an event (e.g. a failed login) without limiting it."""
        with self._lock:
            self._append(key, self._clock())

    def count(self, key: str, window: float) -> int:
        now = self._clock()
        with self._lock:
            return len(self._recent(self._hits.get(key, deque()), now, window))

    def retry_after(self, key: str, limit: int, window: float) -> float:
        """Seconds until `key` drops below `limit` events in `window` (0 if it already is)."""
        now = self._clock()
        with self._lock:
            recent = self._recent(self._hits.get(key, deque()), now, window)
            if len(recent) < limit:
                return 0.0
            return max(0.0, recent[len(recent) - limit] + window - now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

"""Small, dependency-free security helpers used by request handlers."""

from collections import defaultdict, deque
from hashlib import sha256
from threading import Lock
from time import monotonic


class AttemptLimiter:
    """Process-local sliding-window limiter for authentication failures.

    This protects local and single-worker deployments immediately. Multi-worker
    production deployments should move the same keys to a shared Redis store.
    """

    def __init__(self):
        self._attempts = defaultdict(deque)
        self._lock = Lock()
        self._operations = 0

    def _prune(self, attempts, now, window_seconds):
        cutoff = now - window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()

    def blocked(self, key, limit, window_seconds):
        now = monotonic()
        with self._lock:
            attempts = self._attempts[key]
            self._prune(attempts, now, window_seconds)
            if len(attempts) < limit:
                return False, 0
            retry_after = max(1, int(window_seconds - (now - attempts[0])))
            return True, retry_after

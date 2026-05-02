from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[tuple[int, str], deque[float]] = defaultdict(deque)

    def check(self, user_id: int, bucket: str) -> RateLimitResult:
        if self.max_requests <= 0 or self.window_seconds <= 0:
            return RateLimitResult(allowed=True)

        now = monotonic()
        key = (user_id, bucket)
        requests = self._requests[key]
        cutoff = now - self.window_seconds
        while requests and requests[0] <= cutoff:
            requests.popleft()

        if len(requests) >= self.max_requests:
            retry_after = max(1, int(self.window_seconds - (now - requests[0])) + 1)
            return RateLimitResult(allowed=False, retry_after_seconds=retry_after)

        requests.append(now)
        return RateLimitResult(allowed=True)

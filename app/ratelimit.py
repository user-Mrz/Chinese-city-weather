"""按客户端 IP 的令牌桶限流。

公网 IP 一定会被扫描器扫到，没有限流的话高德配额会在几分钟内被烧光。
"""

from __future__ import annotations

import threading
import time


class TokenBucketRateLimiter:
    def __init__(self, rate_per_minute: int, burst: int | None = None) -> None:
        self._rate = max(1, int(rate_per_minute)) / 60.0  # 每秒补充的令牌
        self._capacity = float(max(1, int(burst if burst is not None else rate_per_minute)))
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_seen)
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            tokens, last_seen = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last_seen) * self._rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True

    def _sweep(self, now: float) -> None:
        """丢弃长时间不活跃的桶，避免字典无限增长。"""
        if now - self._last_sweep < 300.0:
            return
        self._last_sweep = now
        idle_before = now - 900.0
        stale = [k for k, (_, last_seen) in self._buckets.items() if last_seen < idle_before]
        for key in stale:
            self._buckets.pop(key, None)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"buckets": len(self._buckets)}

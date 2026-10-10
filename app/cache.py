"""进程内 LRU + TTL 缓存。

单进程 asyncio 服务只需要挡住重复的高德调用，用 OrderedDict 实现即可，
不引入额外依赖，也不需要 Redis / SQLite。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, maxsize: int, ttl: float) -> None:
        self._maxsize = max(1, int(maxsize))
        self._ttl = float(ttl)
        self._data: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> T | None:
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self._misses += 1
                return None
            expires_at, value = item
            if expires_at <= now:
                del self._data[key]
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: T, ttl: float | None = None) -> None:
        expires_at = time.monotonic() + (self._ttl if ttl is None else float(ttl))
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._data), "hits": self._hits, "misses": self._misses}

"""缓存与限流。"""

from __future__ import annotations

import time

from app.cache import TTLCache
from app.ratelimit import TokenBucketRateLimiter


def test_cache_stores_and_returns():
    cache: TTLCache[str] = TTLCache(maxsize=10, ttl=60)
    assert cache.get("a") is None
    cache.set("a", "value")
    assert cache.get("a") == "value"
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1


def test_cache_expires():
    cache: TTLCache[str] = TTLCache(maxsize=10, ttl=0.05)
    cache.set("a", "value")
    time.sleep(0.08)
    assert cache.get("a") is None
    assert cache.stats()["size"] == 0


def test_cache_lru_eviction():
    cache: TTLCache[int] = TTLCache(maxsize=3, ttl=60)
    for index in range(3):
        cache.set(str(index), index)
    cache.get("0")            # 让 "0" 变成最近使用
    cache.set("3", 3)         # 触发淘汰，最久未使用的 "1" 应被踢掉
    assert cache.get("1") is None
    assert cache.get("0") == 0
    assert cache.get("3") == 3


def test_cache_clear():
    cache: TTLCache[str] = TTLCache(maxsize=10, ttl=60)
    cache.set("a", "value")
    cache.clear()
    assert cache.stats()["size"] == 0
    assert cache.get("a") is None


def test_rate_limiter_allows_burst_then_blocks():
    limiter = TokenBucketRateLimiter(rate_per_minute=60, burst=3)
    assert [limiter.allow("1.1.1.1") for _ in range(3)] == [True, True, True]
    assert limiter.allow("1.1.1.1") is False


def test_rate_limiter_is_per_key():
    limiter = TokenBucketRateLimiter(rate_per_minute=60, burst=1)
    assert limiter.allow("1.1.1.1") is True
    assert limiter.allow("1.1.1.1") is False
    assert limiter.allow("2.2.2.2") is True


def test_rate_limiter_refills():
    # 600/分钟 = 每秒 10 个令牌；桶容量 1，睡 0.25 秒应该回满
    limiter = TokenBucketRateLimiter(rate_per_minute=600, burst=1)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False
    time.sleep(0.15)
    assert limiter.allow("k") is True


def test_rate_limiter_evicts_idle_buckets(monkeypatch):
    limiter = TokenBucketRateLimiter(rate_per_minute=60, burst=1)
    limiter.allow("a")
    limiter.allow("b")
    assert limiter.stats()["buckets"] == 2

    # 直接把 sweep 的时间推进到很远的将来
    monkeypatch.setattr(time, "monotonic", lambda: 100000.0)
    limiter._last_sweep = 0.0
    limiter.allow("c")
    assert "a" not in limiter._buckets

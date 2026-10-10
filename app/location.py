"""访问者定位的公共部分：取真实 IP、过滤非公网地址、省级兜底表。

具体的「IP → 城市」由 app/service.py 按所选数据源决定。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from functools import lru_cache

from fastapi import Request

from .config import DATA_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 客户端 IP


def normalize_public_ipv4(raw: str) -> str | None:
    """返回可用的公网 IPv4 字符串；内网 / 回环 / IPv6 / 非法值一律返回 None。

    拿到内网地址时必须直接走人工选择，而不是把它送去查归属地 ——
    那既浪费配额，也会得到完全错误的结果。
    """
    candidate = (raw or "").strip()
    if not candidate:
        return None
    # XFF 里偶尔会带端口，例如 "1.2.3.4:5678"
    if candidate.count(":") == 1 and "." in candidate.split(":", 1)[0]:
        candidate = candidate.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return None

    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is None:
            return None  # 原生 IPv6：两个免注册来源都不支持
        addr = mapped

    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or not addr.is_global
    ):
        return None
    return str(addr)


def client_ip(request: Request, trusted_proxies: frozenset[str]) -> str:
    """取访问者 IP。

    直连（无反向代理）时必须使用 ``request.client.host``。

    只有在对端确实是**我们配置的可信代理**时，才读取 ``X-Forwarded-For``。
    否则任何人都能伪造 XFF 来指定城市并绕过限流 —— 这是个真实的后门。
    """
    direct = request.client.host if request.client else ""
    if trusted_proxies and direct in trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for", "")
        for part in forwarded.split(","):
            candidate = part.strip()
            if candidate:
                return candidate
    return direct


# ---------------------------------------------------------------- 省级兜底表

_SUFFIX_RE = re.compile(r"(特别行政区|维吾尔自治区|壮族自治区|回族自治区|自治区|省|市)$")


def _normalize_province(name: str) -> str:
    return _SUFFIX_RE.sub("", name.strip())


class ProvinceTable:
    def __init__(self, entries: dict[str, dict[str, str]]) -> None:
        self._entries = entries
        self._by_normalized = {_normalize_province(key): key for key in entries}

    def lookup(self, province: str) -> tuple[str, str] | None:
        """返回该省的 ``(省会名, 省会 adcode)``。"""
        name = (province or "").strip()
        if not name:
            return None

        key = name if name in self._entries else self._by_normalized.get(_normalize_province(name))
        if key is None:
            # 不同数据源偶尔返回简称（"浙江" / "内蒙古"），退一步做前缀匹配
            key = next(
                (k for k in self._entries if k.startswith(name) or name.startswith(k)),
                None,
            )
        if key is None:
            return None

        entry = self._entries[key]
        return entry["capital_name"], entry["capital_adcode"]


@lru_cache(maxsize=1)
def load_province_table() -> ProvinceTable:
    raw = json.loads((DATA_DIR / "province_capitals.json").read_text(encoding="utf-8"))
    return ProvinceTable(raw)

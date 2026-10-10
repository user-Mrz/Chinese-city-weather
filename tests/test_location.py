"""定位的公共部分：IP 过滤、X-Forwarded-For 安全边界、省级兜底表。"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.location import (
    client_ip,
    load_province_table,
    normalize_public_ipv4,
)


def make_request(host: str = "1.2.3.4", xff: str | None = None, client: bool = True) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "server": ("localhost", 80),
    }
    if client:
        scope["client"] = (host, 50000)
    return Request(scope)


# ------------------------------------------------------------ IP 过滤


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("114.114.114.114", "114.114.114.114"),
        ("  8.8.8.8  ", "8.8.8.8"),
        ("1.2.3.4:5678", "1.2.3.4"),  # XFF 里带端口
        ("::ffff:114.114.114.114", "114.114.114.114"),  # IPv4-mapped IPv6
        ("127.0.0.1", None),  # 回环
        ("10.0.0.5", None),  # 私网
        ("192.168.1.1", None),  # 私网
        ("169.254.1.1", None),  # 链路本地
        ("0.0.0.0", None),  # 未指定
        ("224.0.0.1", None),  # 组播
        ("::1", None),  # 原生 IPv6：免注册来源都不支持
        ("2400:3200::1", None),  # 原生 IPv6
        ("", None),
        ("not-an-ip", None),
    ],
)
def test_normalize_public_ipv4(raw, expected):
    assert normalize_public_ipv4(raw) == expected


def test_cgnat_is_rejected():
    """100.64.0.0/10（运营商级 NAT）不是公网地址，不能拿去查归属地。"""
    assert normalize_public_ipv4("100.64.0.1") is None


# ------------------------------------------------------------ 客户端 IP


def test_client_ip_direct_ignores_forwarded_header():
    """直连模式下必须忽略 X-Forwarded-For —— 否则任何人都能伪造位置并绕过限流。"""
    request = make_request(host="203.0.113.9", xff="1.1.1.1")
    assert client_ip(request, frozenset()) == "203.0.113.9"


def test_client_ip_trusts_forwarded_header_only_from_configured_proxy():
    trusted = frozenset({"10.0.0.1"})
    from_proxy = make_request(host="10.0.0.1", xff="203.0.113.9, 10.0.0.1")
    assert client_ip(from_proxy, trusted) == "203.0.113.9"

    from_stranger = make_request(host="10.0.0.99", xff="203.0.113.9")
    assert client_ip(from_stranger, trusted) == "10.0.0.99"


def test_client_ip_without_client_info():
    request = make_request(client=False)
    assert client_ip(request, frozenset()) == ""


# ------------------------------------------------------------ 省级兜底表


def test_province_table_exact_and_short_names():
    table = load_province_table()
    assert table.lookup("浙江省") == ("杭州市", "330100")
    assert table.lookup("浙江") == ("杭州市", "330100")  # 上游有时返回简称
    assert table.lookup("内蒙古自治区") == ("呼和浩特市", "150100")
    assert table.lookup("内蒙古") == ("呼和浩特市", "150100")
    assert table.lookup("北京市") == ("北京市", "110000")  # 直辖市
    assert table.lookup("不存在的省") is None
    assert table.lookup("") is None
    assert table.lookup(None) is None

"""IP 归属地解析：换行包裹的 JSON、失败的换源降级。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.geoip import IpApiResolver, PconlineResolver, _extract_json, resolve_place

from .conftest import PCONLINE_FAILURE, PCONLINE_HANGZHOU, PCONLINE_ONLY_PROVINCE, make_transport

IP_API_SUCCESS = {
    "status": "success",
    "country": "中国",
    "regionName": "山东",
    "city": "济南市",
}


def run(coro):
    return asyncio.run(coro)


async def _with_client(transport: httpx.MockTransport, coro_factory):
    async with httpx.AsyncClient(transport=transport) as client:
        return await coro_factory(client)


def test_extract_json_handles_newline_padding():
    data = _extract_json(PCONLINE_HANGZHOU)
    assert data is not None
    assert data["city"] == "杭州市"


def test_extract_json_returns_none_for_garbage():
    assert _extract_json("not json at all") is None
    assert _extract_json("") is None


def test_pconline_resolves_city():
    place = run(_with_client(make_transport(), lambda c: PconlineResolver().resolve(c, "114.114.114.114")))
    assert place is not None
    assert place.name == "杭州市"
    assert place.province == "浙江省"
    assert place.source == "ip"


def test_pconline_province_only():
    transport = make_transport(pconline_text=PCONLINE_ONLY_PROVINCE)
    place = run(_with_client(transport, lambda c: PconlineResolver().resolve(c, "1.1.1.1")))
    assert place is not None
    assert place.province == "浙江省"
    assert place.name == "浙江省"  # city 为空时退回省份名


def test_pconline_error_field_is_rejected():
    transport = make_transport(pconline_text=PCONLINE_FAILURE)
    place = run(_with_client(transport, lambda c: PconlineResolver().resolve(c, "1.1.1.1")))
    assert place is None


def test_resolve_place_uses_pconline_first():
    place = run(_with_client(make_transport(), lambda c: resolve_place(c, "114.114.114.114")))
    assert place is not None
    assert place.name == "杭州市"


def test_resolve_place_falls_back_to_ip_api():
    """pconline 挂了要能自动换源，而不是直接白屏。"""
    transport = make_transport(pconline_text=PCONLINE_FAILURE, ip_api=IP_API_SUCCESS)
    place = run(_with_client(transport, lambda c: resolve_place(c, "114.114.114.114")))
    assert place is not None
    assert place.name == "济南市"
    assert place.province == "山东"


def test_resolve_place_returns_none_when_all_sources_fail():
    transport = make_transport(pconline_text=PCONLINE_FAILURE, ip_api=None)
    assert run(_with_client(transport, lambda c: resolve_place(c, "114.114.114.114"))) is None


def test_ip_api_resolver_rejects_unsuccessful_status():
    transport = make_transport(ip_api={"status": "fail", "message": "reserved range"})
    place = run(_with_client(transport, lambda c: IpApiResolver().resolve(c, "10.0.0.1")))
    assert place is None


def test_resolvers_propagate_http_errors():
    transport = make_transport(pconline_text=None)
    with pytest.raises(httpx.HTTPError):
        run(_with_client(transport, lambda c: PconlineResolver().resolve(c, "1.1.1.1")))

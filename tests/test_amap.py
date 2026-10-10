"""高德客户端：响应解析、空数组坑、错误处理。"""

from __future__ import annotations

import asyncio

import pytest

from app.amap import AmapClient, AmapError, as_text

from .conftest import (
    DISTRICT_SEARCH,
    INVALID_KEY,
    IP_EMPTY_ARRAYS,
    IP_SUCCESS,
    REGEO,
    WEATHER_FORECAST,
    WEATHER_LIVE,
    make_error_transport,
    make_settings,
    make_transport,
)


def run(coro):
    return asyncio.run(coro)


def client(**kwargs) -> AmapClient:
    # 高德客户端测试必须带上 Key，否则会在发请求前就被拦下
    settings = kwargs.pop("settings", None) or make_settings(amap_key="test-key")
    return AmapClient(settings, transport=kwargs.pop("transport", make_transport()))


# ------------------------------------------------------------ as_text


def test_as_text_handles_empty_array():
    """这是本项目最容易踩的坑：定位失败时高德返回的是 [] 而不是字符串。"""
    assert as_text([]) == ""
    assert as_text("杭州市") == "杭州市"
    assert as_text("  杭州市  ") == "杭州市"
    assert as_text(None) == ""
    assert as_text(0) == ""


# ------------------------------------------------------------ IP 定位


def test_ip_locate_success():
    result = run(client().ip_locate("114.114.114.114"))
    assert result.resolved
    assert result.province == "浙江省"
    assert result.city == "杭州市"
    assert result.adcode == "330100"


def test_ip_locate_empty_arrays_is_unresolved():
    result = run(client(transport=make_transport(amap_ip=IP_EMPTY_ARRAYS)).ip_locate("1.1.1.1"))
    assert not result.resolved
    assert result.province == ""
    assert result.city == ""
    assert result.adcode == ""


def test_ip_locate_sends_ip_explicitly():
    """必须显式传 ip，否则高德定位到的是我们自己的服务器。"""
    seen: list[str] = []

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("ip", "<missing>"))
        return httpx.Response(200, json=IP_SUCCESS)

    amap = AmapClient(make_settings(amap_key="test-key"), transport=httpx.MockTransport(handler))
    run(amap.ip_locate("114.114.114.114"))
    assert seen == ["114.114.114.114"]


# ------------------------------------------------------------ 天气


def test_weather_parses_live_and_forecast():
    live, forecast = run(client().weather("330100"))
    assert live is not None
    assert live.city == "杭州市"
    assert live.weather == "多云"
    assert live.temperature == "26"
    assert live.winddirection == "东南"
    assert live.windpower == "≤3"

    assert forecast is not None
    assert len(forecast.days) == 2
    assert forecast.days[0].dayweather == "多云"
    assert forecast.days[0].nightweather == "小雨"
    assert forecast.days[1].daypower == "3-4"


def test_weather_requests_both_extensions():
    """实况和预报是两个不同的 extensions，必须都请求到。"""
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/weather/weatherInfo":
            seen.append(request.url.params.get("extensions", ""))
            if request.url.params.get("extensions") == "base":
                return httpx.Response(200, json=WEATHER_LIVE)
            return httpx.Response(200, json=WEATHER_FORECAST)
        return httpx.Response(200, json=IP_SUCCESS)

    amap = AmapClient(make_settings(amap_key="test-key"), transport=httpx.MockTransport(handler))
    run(amap.weather("330100"))
    assert sorted(seen) == ["all", "base"]


def test_weather_tolerates_one_side_failing():
    """预报挂了不该导致整个页面什么都没有。"""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/weather/weatherInfo" and request.url.params.get("extensions") == "all":
            return httpx.Response(200, json=INVALID_KEY)
        return httpx.Response(200, json=WEATHER_LIVE)

    amap = AmapClient(make_settings(amap_key="test-key"), transport=httpx.MockTransport(handler))
    live, forecast = run(amap.weather("330100"))
    assert live is not None
    assert forecast is None


def test_weather_raises_when_both_fail():
    amap = client(transport=make_transport(amap_live=INVALID_KEY, amap_forecast=INVALID_KEY))
    with pytest.raises(AmapError):
        run(amap.weather("330100"))


def test_weather_empty_lives_returns_none():
    """接口成功但没有任何数据 —— 应返回 (None, None) 而不是抛错。"""
    empty_live = {"status": "1", "info": "OK", "infocode": "10000", "count": "0", "lives": []}
    empty_forecast = {"status": "1", "info": "OK", "infocode": "10000", "count": "0", "forecasts": []}
    amap = client(transport=make_transport(amap_live=empty_live, amap_forecast=empty_forecast))
    live, forecast = run(amap.weather("330100"))
    assert live is None
    assert forecast is None


# ------------------------------------------------------------ 错误处理


def test_business_error_raises_amap_error():
    amap = client(transport=make_transport(amap_ip=INVALID_KEY))
    with pytest.raises(AmapError, match="INVALID_USER_KEY"):
        run(amap.ip_locate("1.1.1.1"))


def test_http_error_raises_amap_error():
    amap = AmapClient(make_settings(amap_key="test-key"), transport=make_error_transport(500))
    with pytest.raises(AmapError):
        run(amap.ip_locate("1.1.1.1"))


def test_missing_key_raises_before_requesting():
    amap = AmapClient(make_settings(amap_key=""), transport=make_transport())
    with pytest.raises(AmapError, match="AMAP_KEY"):
        run(amap.ip_locate("1.1.1.1"))


# ------------------------------------------------------------ 其它接口


def test_search_districts():
    amap = client(transport=make_transport(amap_district=DISTRICT_SEARCH))
    districts = run(amap.search_districts("杭州"))
    assert [d.name for d in districts] == ["杭州市"]
    assert districts[0].adcode == "330100"


def test_search_districts_skips_entries_without_adcode():
    payload = {
        "status": "1",
        "info": "OK",
        "infocode": "10000",
        "districts": [{"name": "无编码区", "adcode": []}, {"name": "杭州市", "adcode": "330100"}],
    }
    amap = client(transport=make_transport(amap_district=payload))
    districts = run(amap.search_districts("杭州"))
    assert [d.adcode for d in districts] == ["330100"]


def test_reverse_geocode():
    amap = client(transport=make_transport(amap_regeo=REGEO))
    assert run(amap.reverse_geocode(120.15, 30.28)) == "330100"

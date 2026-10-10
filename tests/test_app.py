"""端到端：整页渲染、城市切换、JSON 接口、限流、两条数据源链路。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import (
    NMC_WEATHER_MISSING,
    PCONLINE_FAILURE,
    make_settings,
    make_transport,
)


def build_client(transport=None, **settings_overrides):
    settings = make_settings(**settings_overrides)
    app = create_app(settings, transport=transport or make_transport())
    return TestClient(app)


def amap_client(transport=None, **settings_overrides):
    settings = make_settings(weather_backend="amap", amap_key="test-key", **settings_overrides)
    app = create_app(settings, transport=transport or make_transport())
    return TestClient(app)


# ------------------------------------------------------------ 健康检查


def test_healthz_defaults_to_nmc():
    with build_client() as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["backend"] == "nmc"
    assert body["amap_key_configured"] is False


def test_healthz_reports_amap_when_key_present():
    with amap_client() as client:
        body = client.get("/healthz").json()
    assert body["backend"] == "amap"
    assert body["amap_key_configured"] is True


# ------------------------------------------------------------ 默认链路（中央气象台）


def test_index_renders_full_reading():
    with build_client() as client:
        response = client.get("/?debug_ip=114.114.114.114")
    assert response.status_code == 200
    html = response.text
    assert "杭州" in html
    assert "晴" in html
    assert "23.2" in html
    assert "70%" in html                       # 湿度
    assert "西北风 微风" in html                # 中央气象台的写法要原样拼对
    assert "2026-10-10 09:35" in html
    assert "未来2天" in html
    assert "小雨" in html                      # 第二天预报
    assert "中央气象台" in html                 # 页脚标注数据源


def test_index_province_only_falls_back_to_capital():
    transport = make_transport(pconline_text='\n{"pro":"浙江省","city":"","err":""}\n')
    with build_client(transport) as client:
        response = client.get("/?debug_ip=114.114.114.114")
    assert response.status_code == 200
    assert "只定位到" in response.text
    assert "杭州" in response.text


def test_index_falls_back_to_manual_picker():
    transport = make_transport(pconline_text=PCONLINE_FAILURE, ip_api=None)
    with build_client(transport) as client:
        response = client.get("/?debug_ip=114.114.114.114")
    assert response.status_code == 200
    assert "未识别" in response.text
    assert "换个地方" in response.text


def test_private_ip_never_reaches_upstreams():
    calls: list[str] = []
    with build_client(make_transport(calls=calls)) as client:
        response = client.get("/?debug_ip=192.168.1.10")
    assert response.status_code == 200
    assert "未识别" in response.text
    assert calls == []


def test_weather_failure_shows_friendly_error():
    transport = make_transport(nmc_weather={"HIieJ": NMC_WEATHER_MISSING})
    with build_client(transport) as client:
        response = client.get("/?debug_ip=114.114.114.114")
    assert response.status_code == 200
    assert "暂时拿不到" in response.text


def test_geoip_down_still_renders():
    """上游全挂也不能 500，要给出可操作的页面。"""
    transport = make_transport(pconline_text=None, ip_api=None)
    with build_client(transport) as client:
        response = client.get("/?debug_ip=114.114.114.114")
    assert response.status_code == 200
    assert "未识别" in response.text


# ------------------------------------------------------------ 高德链路（可选）


def test_amap_backend_renders():
    with amap_client() as client:
        response = client.get("/?debug_ip=114.114.114.114")
    assert response.status_code == 200
    html = response.text
    assert "杭州市" in html
    assert "多云" in html
    assert "26" in html
    assert "东南风 ≤3级" in html
    assert "高德地图" in html


def test_amap_backend_without_key_falls_back_to_nmc():
    settings = make_settings(weather_backend="amap", amap_key="")
    app = create_app(settings, transport=make_transport())
    with TestClient(app) as client:
        body = client.get("/healthz").json()
    assert body["backend"] == "nmc"


def test_auto_backend_picks_amap_only_with_key():
    settings = make_settings(weather_backend="auto", amap_key="")
    app = create_app(settings, transport=make_transport())
    with TestClient(app) as client:
        assert client.get("/healthz").json()["backend"] == "nmc"

    settings = make_settings(weather_backend="auto", amap_key="test-key")
    app = create_app(settings, transport=make_transport())
    with TestClient(app) as client:
        assert client.get("/healthz").json()["backend"] == "amap"


# ------------------------------------------------------------ 城市搜索与切换


def test_search_renders_hits():
    with build_client() as client:
        response = client.get("/search", params={"q": "杭州"})
    assert response.status_code == 200
    assert "杭州" in response.text
    assert "浙江省" in response.text
    assert "/set-city?name=" in response.text


def test_search_without_query_shows_form():
    with build_client() as client:
        response = client.get("/search")
    assert response.status_code == 200
    assert "输入城市名" in response.text


def test_search_no_match():
    with build_client() as client:
        response = client.get("/search", params={"q": "压根不存在的地名"})
    assert response.status_code == 200
    assert "没找到" in response.text


def test_set_city_sets_cookie_and_redirects():
    with build_client() as client:
        response = client.get(
            "/set-city", params={"name": "嘉兴", "province": "浙江省"}, follow_redirects=False
        )
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert "city_name=" in response.headers["set-cookie"]


def test_chinese_city_name_survives_cookie_round_trip():
    """HTTP 头是 latin-1 编码的，中文城市名必须先 URL 编码，否则直接 500。"""
    with build_client() as client:
        redirect = client.get(
            "/set-city", params={"name": "杭州", "province": "浙江省"}, follow_redirects=False
        )
        assert redirect.status_code == 302
        page = client.get("/")
    assert page.status_code == 200
    assert "杭州" in page.text


def test_cookie_overrides_ip_detection():
    # 浏览器回传的就是服务端写下的 URL 编码值
    with build_client() as client:
        client.cookies.set("city_name", quote("嘉兴", safe=""))
        client.cookies.set("city_province", quote("浙江省", safe=""))
        response = client.get("/")
    assert response.status_code == 200
    assert "嘉兴" in response.text


def test_reset_clears_cookies():
    with build_client() as client:
        client.cookies.set("city_name", quote("嘉兴", safe=""))
        response = client.get("/reset", follow_redirects=False)
    assert response.status_code == 302
    assert "city_name=" in response.headers["set-cookie"]


def test_set_city_without_arguments_redirects_home():
    with build_client() as client:
        response = client.get("/set-city", follow_redirects=False)
    assert response.status_code == 302


# ------------------------------------------------------------ JSON 接口


def test_api_weather_by_city_on_nmc():
    with build_client() as client:
        response = client.get("/api/weather", params={"city": "杭州"})
    assert response.status_code == 200
    body = response.json()
    assert body["place"]["name"] == "杭州"
    assert body["place"]["station"] == "HIieJ"
    assert body["live"]["weather"] == "晴"
    assert len(body["forecast"]["days"]) == 2
    assert body["source"] == "中央气象台"


def test_api_weather_by_adcode_on_amap():
    with amap_client() as client:
        response = client.get("/api/weather", params={"adcode": "330100"})
    assert response.status_code == 200
    assert response.json()["live"]["weather"] == "多云"


def test_api_weather_by_lat_lng_requires_amap():
    with build_client() as client:
        response = client.get("/api/weather", params={"lat": 30.28, "lng": 120.15})
    assert response.status_code == 400

    with amap_client() as client:
        response = client.get("/api/weather", params={"lat": 30.28, "lng": 120.15})
    assert response.status_code == 200
    assert response.json()["place"]["adcode"] == "330100"


def test_api_weather_requires_arguments():
    with build_client() as client:
        response = client.get("/api/weather")
    assert response.status_code == 400


def test_api_weather_unknown_city_is_502():
    with build_client() as client:
        response = client.get("/api/weather", params={"city": "压根不存在的地名"})
    assert response.status_code == 502


def test_api_city_search():
    with build_client() as client:
        response = client.get("/api/city/search", params={"q": "杭州"})
    assert response.status_code == 200
    assert response.json()["results"][0]["station"] == "HIieJ"


# ------------------------------------------------------------ 限流与缓存


def test_rate_limit_returns_429():
    settings = make_settings(rate_limit_per_minute=10, rate_limit_burst=3)
    app = create_app(settings, transport=make_transport())
    with TestClient(app) as client:
        codes = [client.get("/?debug_ip=114.114.114.114").status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


def test_healthz_is_exempt_from_rate_limit():
    settings = make_settings(rate_limit_per_minute=10, rate_limit_burst=1)
    app = create_app(settings, transport=make_transport())
    with TestClient(app) as client:
        client.get("/?debug_ip=114.114.114.114")
        codes = [client.get("/healthz").status_code for _ in range(5)]
    assert set(codes) == {200}


def test_weather_cache_avoids_duplicate_upstream_calls():
    calls: list[str] = []
    with build_client(make_transport(calls=calls)) as client:
        client.get("/api/weather", params={"city": "杭州"})
        first = len(calls)
        client.get("/api/weather", params={"city": "杭州"})
    assert first > 0
    assert len(calls) == first  # 第二次完全命中缓存


def test_location_cache_avoids_repeated_geoip_calls():
    calls: list[str] = []
    with build_client(make_transport(calls=calls)) as client:
        client.get("/?debug_ip=114.114.114.114")
        first = [c for c in calls if "pconline" in c]
        client.get("/?debug_ip=114.114.114.114")
        second = [c for c in calls if "pconline" in c]
    assert len(first) == 1
    assert len(second) == 1  # IP 归属地结果被缓存了

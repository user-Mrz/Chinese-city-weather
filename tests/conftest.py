"""共用的测试数据与工具。

响应体都按上游真实返回的结构构造，包括那些反直觉的地方：
高德定位失败时 province/city 是空数组、中央气象台站点不存在时 data 是空字符串、
pconline 的 JSON 裹在一堆换行里。

所有出站请求都通过一个 ``httpx.MockTransport`` 走，按 host 分发到假响应，
这样一条链路（IP 归属地 → 城市站点 → 天气）能在完全离线的情况下跑通。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings

# ------------------------------------------------------------------ 高德

IP_SUCCESS: dict[str, Any] = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "province": "浙江省",
    "city": "杭州市",
    "adcode": "330100",
}

IP_EMPTY_ARRAYS: dict[str, Any] = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "province": [],
    "city": [],
    "adcode": [],
}

IP_PROVINCE_ONLY: dict[str, Any] = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "province": "浙江省",
    "city": [],
    "adcode": [],
}

WEATHER_LIVE: dict[str, Any] = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "count": "1",
    "lives": [
        {
            "province": "浙江",
            "city": "杭州市",
            "adcode": "330100",
            "weather": "多云",
            "temperature": "26",
            "winddirection": "东南",
            "windpower": "≤3",
            "humidity": "58",
            "reporttime": "2026-02-03 12:00:00",
        }
    ],
}

WEATHER_FORECAST: dict[str, Any] = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "count": "1",
    "forecasts": [
        {
            "city": "杭州市",
            "adcode": "330100",
            "province": "浙江",
            "reporttime": "2026-02-03 11:00:00",
            "casts": [
                {
                    "date": "2026-02-03", "week": "2",
                    "dayweather": "多云", "nightweather": "小雨",
                    "daytemp": "18", "nighttemp": "8",
                    "daywind": "东南", "daypower": "≤3",
                    "nightwind": "东", "nightpower": "≤3",
                },
                {
                    "date": "2026-02-04", "week": "3",
                    "dayweather": "小雨", "nightweather": "小雨",
                    "daytemp": "16", "nighttemp": "9",
                    "daywind": "东北", "daypower": "3-4",
                    "nightwind": "东北", "nightpower": "3-4",
                },
            ],
        }
    ],
}

DISTRICT_SEARCH: dict[str, Any] = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "count": "1",
    "districts": [
        {"citycode": "0571", "adcode": "330100", "name": "杭州市", "center": "120.2,30.26", "level": "city"}
    ],
}

REGEO: dict[str, Any] = {
    "status": "1",
    "info": "OK",
    "infocode": "10000",
    "regeocode": {
        "formatted_address": "浙江省杭州市西湖区",
        "addressComponent": {"province": "浙江省", "city": "杭州市", "adcode": "330100"},
    },
}

INVALID_KEY: dict[str, Any] = {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}


# ------------------------------------------------------------------ 中央气象台

NMC_PROVINCES: list[dict[str, Any]] = [
    {"code": "AZJ", "name": "浙江省", "url": "/publish/forecast/AZJ.html"},
    {"code": "ABJ", "name": "北京市", "url": "/publish/forecast/ABJ.html"},
]

NMC_CITIES_AZJ: list[dict[str, Any]] = [
    {"code": "HIieJ", "province": "浙江省", "city": "杭州", "url": "/publish/forecast/AZJ/hangzhou.html"},
    {"code": "pxfeS", "province": "浙江省", "city": "嘉兴", "url": "/publish/forecast/AZJ/jiaxing.html"},
]

NMC_CITIES_ABJ: list[dict[str, Any]] = [
    {"code": "Wqsps", "province": "北京市", "city": "北京", "url": "/publish/forecast/ABJ/beijing.html"},
]

NMC_WEATHER_HANGZHOU: dict[str, Any] = {
    "msg": "success",
    "code": 0,
    "data": {
        "real": {
            "station": {"code": "HIieJ", "province": "浙江省", "city": "杭州"},
            "publish_time": "2026-10-10 09:35",
            "weather": {
                "temperature": 23.2,
                "humidity": 70.0,
                "info": "晴",
                "feelst": 25.0,
                "airpressure": 9999.0,  # 缺测占位值
            },
            "wind": {"direct": "西北风", "degree": 323.0, "power": "微风", "speed": 1.7},
        },
        "predict": {
            "station": {"code": "HIieJ", "province": "浙江省", "city": "杭州"},
            "publish_time": "2026-10-10 08:00",
            "detail": [
                {
                    "date": "2026-10-10",
                    "day": {
                        "weather": {"info": "晴", "temperature": "27"},
                        "wind": {"direct": "西南风", "power": "微风"},
                    },
                    "night": {
                        "weather": {"info": "多云", "temperature": "18"},
                        "wind": {"direct": "西南风", "power": "微风"},
                    },
                },
                {
                    "date": "2026-10-11",
                    "day": {
                        "weather": {"info": "小雨", "temperature": "24"},
                        "wind": {"direct": "东北风", "power": "3-4"},
                    },
                    "night": {
                        "weather": {"info": "小雨", "temperature": "16"},
                        "wind": {"direct": "东北风", "power": "3-4"},
                    },
                },
            ],
        },
    },
}

# 站点不存在时，中央气象台的 data 是空字符串而不是对象
NMC_WEATHER_MISSING: dict[str, Any] = {"msg": "success", "code": 0, "data": ""}


# ------------------------------------------------------------------ IP 归属地

PCONLINE_HANGZHOU = (
    '\n\n\n{"ip":"114.114.114.114","pro":"浙江省","proCode":"330000",'
    '"city":"杭州市","cityCode":"330100","region":"西湖区","err":""}\n\n'
)

PCONLINE_ONLY_PROVINCE = '\n{"ip":"1.1.1.1","pro":"浙江省","city":"","err":""}\n'

PCONLINE_FAILURE = '\n{"ip":"","pro":"","city":"","err":"N/A"}\n'


# ------------------------------------------------------------------ 工具


def make_settings(**overrides: Any) -> Settings:
    """构造测试用配置；显式关掉 .env 读取，保证结果可复现。"""
    base: dict[str, Any] = {
        "weather_backend": "nmc",
        "amap_key": "",
        "amap_retries": 0,
        "amap_timeout": 2.0,
        "nmc_retries": 0,
        "nmc_timeout": 2.0,
        "geoip_timeout": 2.0,
        "allow_debug_ip": True,
        # 测试里关掉后台预热：否则它会在用例断言「有没有发请求」时插进来
        "warm_index_on_startup": False,
        "rate_limit_per_minute": 600,
        "rate_limit_burst": 600,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def make_transport(
    *,
    # IP 归属地
    pconline_text: str | None = PCONLINE_HANGZHOU,
    ip_api: dict[str, Any] | None = None,
    # 中央气象台
    nmc_provinces: list[dict[str, Any]] | None = NMC_PROVINCES,
    nmc_cities: dict[str, list[dict[str, Any]]] | None = None,
    nmc_weather: dict[str, dict[str, Any]] | None = None,
    # 高德
    amap_ip: dict[str, Any] | None = IP_SUCCESS,
    amap_live: dict[str, Any] | None = WEATHER_LIVE,
    amap_forecast: dict[str, Any] | None = WEATHER_FORECAST,
    amap_district: dict[str, Any] | None = DISTRICT_SEARCH,
    amap_regeo: dict[str, Any] | None = REGEO,
    calls: list[str] | None = None,
) -> httpx.MockTransport:
    """按 host 分发的假上游。``calls`` 记录每次请求的 host+path，用于验证缓存。"""
    cities = {"AZJ": NMC_CITIES_AZJ, "ABJ": NMC_CITIES_ABJ}
    if nmc_cities:
        cities.update(nmc_cities)
    weather_by_station = {"HIieJ": NMC_WEATHER_HANGZHOU, "Wqsps": NMC_WEATHER_HANGZHOU}
    if nmc_weather:
        weather_by_station.update(nmc_weather)

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        path = request.url.path
        if calls is not None:
            calls.append(f"{host}{path}")

        if host == "whois.pconline.com.cn":
            if pconline_text is None:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, text=pconline_text)

        if host == "ip-api.com":
            if ip_api is None:
                return httpx.Response(500, json={})
            return httpx.Response(200, json=ip_api)

        if host == "www.nmc.cn":
            if path == "/rest/province/all":
                if nmc_provinces is None:
                    return httpx.Response(500, json={})
                return httpx.Response(200, json=nmc_provinces)
            if path.startswith("/rest/province/"):
                code = path.rsplit("/", 1)[-1]
                payload = cities.get(code)
                if payload is None:
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json=payload)
            if path == "/rest/weather":
                station = request.url.params.get("stationid", "")
                payload = weather_by_station.get(station)
                if payload is None:
                    return httpx.Response(200, json=NMC_WEATHER_MISSING)
                return httpx.Response(200, json=payload)
            return httpx.Response(404, json={})

        if host == "restapi.amap.com":
            if path == "/v3/ip":
                if amap_ip is None:
                    return httpx.Response(500, json={})
                return httpx.Response(200, json=amap_ip)
            if path == "/v3/weather/weatherInfo":
                extensions = request.url.params.get("extensions")
                payload = amap_live if extensions == "base" else amap_forecast
                if payload is None:
                    return httpx.Response(200, json=INVALID_KEY)
                return httpx.Response(200, json=payload)
            if path == "/v3/config/district":
                if amap_district is None:
                    return httpx.Response(200, json=INVALID_KEY)
                return httpx.Response(200, json=amap_district)
            if path == "/v3/geocode/regeo":
                if amap_regeo is None:
                    return httpx.Response(200, json=INVALID_KEY)
                return httpx.Response(200, json=amap_regeo)
            return httpx.Response(404, json={})

        return httpx.Response(404, json={"error": f"unmocked host {host}"})

    return httpx.MockTransport(handler)


def make_error_transport(status_code: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={})

    return httpx.MockTransport(handler)

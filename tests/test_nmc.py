"""中央气象台公开接口：结构解析、城市匹配、缺测值处理。"""

from __future__ import annotations

import asyncio

import pytest

from app.nmc import NmcClient, NmcError, Station, as_text, normalize_name, week_of

from .conftest import NMC_WEATHER_MISSING, make_settings, make_transport


def run(coro):
    return asyncio.run(coro)


def client(**kwargs) -> NmcClient:
    return NmcClient(kwargs.pop("settings", None) or make_settings(), transport=kwargs.pop("transport", make_transport()))


# ------------------------------------------------------------ 文本处理


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("杭州市", "杭州"),
        ("浙江省", "浙江"),
        ("内蒙古自治区", "内蒙古"),
        ("新疆维吾尔自治区", "新疆"),
        ("广西壮族自治区", "广西"),
        ("香港特别行政区", "香港"),
        ("北京市", "北京"),
        ("  杭州 市 ", "杭州"),
        ("", ""),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (9999, ""),
        (9999.0, ""),
        (23.2, "23.2"),
        (27.0, "27"),
        (0.0, "0"),
        ("27", "27"),
        ("9999", ""),
        ("", ""),
        (None, ""),
        ("晴", "晴"),
    ],
)
def test_as_text(raw, expected):
    assert as_text(raw) == expected


def test_week_of():
    assert week_of("2024-01-01") == "1"  # 2024-01-01 是周一
    assert week_of("2024-01-07") == "7"  # 周日
    assert week_of("乱写") == ""
    assert week_of("") == ""


# ------------------------------------------------------------ 省份与站点


def test_province_index():
    index = run(client().province_index())
    assert index == {"浙江省": "AZJ", "北京市": "ABJ"}


def test_find_province_code_tolerates_short_names():
    nmc = client()
    assert run(nmc.find_province_code("浙江省")) == "AZJ"
    assert run(nmc.find_province_code("浙江")) == "AZJ"
    assert run(nmc.find_province_code("不存在的省")) == ""


def test_find_station_within_province():
    station = run(client().find_station("浙江省", "杭州"))
    assert station is not None
    assert station.code == "HIieJ"
    assert station.city == "杭州"


def test_find_station_falls_back_to_global_index():
    """没有省份时（手动选城市）要靠全国索引，不能直接放弃。"""
    station = run(client().find_station("", "北京"))
    assert station is not None
    assert station.code == "Wqsps"


def test_find_station_tolerates_suffix_difference():
    """pconline 给「杭州市」，nmc 里存的是「杭州」。"""
    station = run(client().find_station("浙江省", "杭州市"))
    assert station is not None
    assert station.code == "HIieJ"


def test_find_station_unknown_returns_none():
    assert run(client().find_station("浙江省", "压根没有这个市")) is None


def test_search_returns_candidates():
    results = run(client().search("杭州"))
    assert [s.code for s in results] == ["HIieJ"]


# ------------------------------------------------------------ 天气解析


def test_weather_parses_live_and_forecast():
    live, forecast = run(client().weather("HIieJ"))

    assert live is not None
    assert live.city == "杭州"
    assert live.weather == "晴"
    assert live.temperature == "23.2"
    assert live.humidity == "70"
    assert live.winddirection == "西北风"
    assert live.windpower == "微风"
    assert live.reporttime == "2026-10-10 09:35"

    assert forecast is not None
    assert len(forecast.days) == 2
    first, second = forecast.days
    assert first.date == "2026-10-10"
    assert first.dayweather == "晴"
    assert first.nightweather == "多云"
    assert first.daytemp == "27"
    assert first.nighttemp == "18"
    assert first.week == week_of("2026-10-10")
    assert second.daypower == "3-4"


def test_weather_missing_station_raises():
    """站点不存在时 data 是空字符串，必须当成错误而不是解析出空对象。"""
    nmc = client(transport=make_transport(nmc_weather={"HIieJ": NMC_WEATHER_MISSING}))
    with pytest.raises(NmcError):
        run(nmc.weather("HIieJ"))


def test_weather_unknown_station_raises():
    with pytest.raises(NmcError):
        run(client().weather("NOPE"))


def test_weather_handles_missing_sections():
    """只有实况没有预报时不应崩。"""
    payload = {"msg": "success", "code": 0, "data": {"real": {"weather": {"info": "晴", "temperature": 20}}}}
    nmc = client(transport=make_transport(nmc_weather={"HIieJ": payload}))
    live, forecast = run(nmc.weather("HIieJ"))
    assert live is not None and live.weather == "晴"
    assert forecast is None


def test_province_list_failure_raises():
    nmc = client(transport=make_transport(nmc_provinces=None))
    with pytest.raises(NmcError):
        run(nmc.province_index())


def test_global_index_collects_all_provinces():
    index = run(client().global_index())
    codes = {station.code for station in index}
    assert {"HIieJ", "pxfeS", "Wqsps"} <= codes


def test_match_prefers_same_province():
    from app.nmc import _match

    stations = [
        Station(code="A", province="浙江省", city="城关"),
        Station(code="B", province="甘肃省", city="城关"),
    ]
    hits = _match(stations, "城关", "甘肃省")
    assert [s.code for s in hits] == ["B"]

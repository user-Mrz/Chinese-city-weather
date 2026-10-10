"""展示层纯函数：图标、主色、风向角度、文案格式化。"""

from __future__ import annotations

import pytest

from app.render import (
    accent_color,
    location_caption,
    short_date,
    temperature_text,
    weather_icon,
    weekday_label,
    wind_angle,
    wind_text,
)


@pytest.mark.parametrize(
    "weather,expected",
    [
        ("晴", "☀️"),
        ("多云", "⛅"),
        ("阴", "☁️"),
        ("雷阵雨", "⛈️"),        # 「雷」优先于「雨」
        ("雨夹雪", "🌨️"),        # 「雪」优先于「雨」
        ("暴雨", "🌧️"),
        ("中雨", "🌧️"),
        ("小雨", "🌦️"),
        ("雾", "🌫️"),
        ("霾", "🌫️"),
        ("沙尘暴", "🌪️"),
        ("晴转多云", "⛅"),      # 「多云」规则在「晴」之前，避免晴天图标压过多云
        ("", "🌡️"),
        ("未知现象", "🌡️"),
    ],
)
def test_weather_icon(weather, expected):
    assert weather_icon(weather) == expected


def test_accent_color_tracks_weather():
    assert accent_color("晴") != accent_color("暴雨")
    assert accent_color("") == "#4A5A52"
    assert accent_color("大雨").startswith("#")


@pytest.mark.parametrize(
    "direction,expected",
    [
        ("北", 0),
        ("东北", 45),
        ("东", 90),
        ("东南", 135),
        ("南", 180),
        ("西南", 225),
        ("西", 270),
        ("西北", 315),
        ("东南风", 135),
        ("", 0),
        ("乱七八糟", 0),
    ],
)
def test_wind_angle(direction, expected):
    assert wind_angle(direction) == expected


def test_wind_angle_prefers_longest_match():
    """「西北」不能被「西」抢先命中 —— 这是靠长度倒序匹配才对的。"""
    assert wind_angle("西北") == 315
    assert wind_angle("东北") == 45
    assert wind_angle("西南") == 225
    assert wind_angle("东南") == 135


@pytest.mark.parametrize(
    "direction,power,expected",
    [
        # 高德：风向不带「风」，风力是等级，两样都要补
        ("东南", "≤3", "东南风 ≤3级"),
        ("北", "3-4", "北风 3-4级"),
        ("东南风", "5", "东南风 5级"),
        ("", "", "—"),
        ("东南", "", "东南风"),
        ("", "3", "3级"),
        # 中央气象台：风向自带「风」、风力是「微风」这种描述，都不能再补
        ("西南风", "微风", "西南风 微风"),
        ("西北风", "3-4", "西北风 3-4级"),
        ("东风", "4-5级", "东风 4-5级"),
    ],
)
def test_wind_text(direction, power, expected):
    assert wind_text(direction, power) == expected


def test_weekday_label():
    assert weekday_label("1") == "周一"
    assert weekday_label("7") == "周日"
    assert weekday_label("9") == ""
    assert weekday_label("") == ""


def test_short_date():
    assert short_date("2026-02-03") == "02-03"
    assert short_date("") == ""
    assert short_date("怪格式") == "怪格式"


def test_temperature_text():
    assert temperature_text("26") == "26°"
    assert temperature_text("") == "—"


def test_location_caption():
    assert "杭州市" in location_caption("杭州市", "ip")
    assert "浙江省" in location_caption("浙江省", "province")

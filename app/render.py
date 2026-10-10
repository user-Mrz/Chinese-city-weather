"""展示层的纯函数：天气现象 → 图标、风向风力格式化、日期星期格式化。

单独成模块是为了能脱离 FastAPI 直接做单元测试。
"""

from __future__ import annotations

import re

# 形如 3 / 3-4 / ≤3 / <3 的才算风力等级；「微风」这类描述不能补「级」
_POWER_LEVEL_RE = re.compile(r"[≤<>≥]?\s*\d+\s*(?:-\s*\d+)?")

# 顺序有意义：先匹配到的规则胜出。
# 例如「雷阵雨」要命中「雷」，「雨夹雪」要命中「雪」。
_ICON_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("雷",), "⛈️"),
    (("雪", "冰雹", "霰"), "🌨️"),
    (("沙尘暴",), "🌪️"),
    (("雾", "霾", "浮尘", "扬沙"), "🌫️"),
    (("暴雨", "大雨", "中雨"), "🌧️"),
    (("雨",), "🌦️"),
    (("多云",), "⛅"),
    (("阴",), "☁️"),
    (("晴",), "☀️"),
    (("风",), "💨"),
)

_DEFAULT_ICON = "🌡️"

# 主色由天气现象本身决定 —— 颜色在这里是信息，不是装饰。
# 色值刻意压得偏「墨」，避免手机屏幕上刺眼。
_ACCENT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("雷",), "#B4551F"),
    (("雪", "冰雹", "霰"), "#4A7C96"),
    (("沙尘暴", "浮尘", "扬沙"), "#8A8060"),
    (("雾", "霾"), "#7C8577"),
    (("暴雨", "大雨", "中雨"), "#1F4E79"),
    (("雨",), "#2E7BA6"),
    (("多云",), "#6E7B8A"),
    (("阴",), "#5A646E"),
    (("晴",), "#C9781A"),
)

_DEFAULT_ACCENT = "#4A5A52"

# 风向标按气象惯例指向「风的来向」；SVG 里箭头默认朝上（正北）。
_WIND_ANGLES = {
    "北": 0,
    "东北": 45,
    "东": 90,
    "东南": 135,
    "南": 180,
    "西南": 225,
    "西": 270,
    "西北": 315,
}

_WEEKDAYS = {
    "1": "周一",
    "2": "周二",
    "3": "周三",
    "4": "周四",
    "5": "周五",
    "6": "周六",
    "7": "周日",
}


def weather_icon(text: str) -> str:
    """把高德返回的中文天气现象映射成 emoji。"""
    value = (text or "").strip()
    for keywords, icon in _ICON_RULES:
        if any(keyword in value for keyword in keywords):
            return icon
    return _DEFAULT_ICON


def accent_color(text: str) -> str:
    """天气现象 → 页面主色。"""
    value = (text or "").strip()
    for keywords, color in _ACCENT_RULES:
        if any(keyword in value for keyword in keywords):
            return color
    return _DEFAULT_ACCENT


def wind_angle(direction: str) -> int:
    """风向 → 旋转角度（度）。按长度倒序匹配，避免「西北风」被「西」抢先命中。"""
    value = (direction or "").strip().removesuffix("风")
    if value in _WIND_ANGLES:
        return _WIND_ANGLES[value]
    for name in sorted(_WIND_ANGLES, key=len, reverse=True):
        if value.startswith(name):
            return _WIND_ANGLES[name]
    return 0


def wind_text(direction: str, power: str) -> str:
    """拼出「东南风 ≤3级」这种读法。

    两家的写法不一样，都要吃下：
      * 高德：winddirection="东南"、windpower="≤3"   → 要补「风」和「级」
      * 中央气象台：direct="西南风"、power="微风"    → 已经完整，不能再补
    所以只有形如 3 / 3-4 / ≤3 的才当成风力等级补「级」。
    """
    wind_direction = (direction or "").strip()
    if wind_direction and not wind_direction.endswith("风"):
        wind_direction += "风"

    wind_power = (power or "").strip()
    if wind_power and not wind_power.endswith(("级", "风")) and _POWER_LEVEL_RE.fullmatch(wind_power):
        wind_power += "级"

    return " ".join(part for part in (wind_direction, wind_power) if part) or "—"


def weekday_label(week: str) -> str:
    """高德 ``week`` 是 "1".."7"（周一到周日）。"""
    return _WEEKDAYS.get((week or "").strip(), "")


def short_date(date: str) -> str:
    """``2026-02-03`` → ``02-03``；格式不认识就原样返回。"""
    value = (date or "").strip()
    parts = value.split("-")
    if len(parts) == 3:
        return f"{parts[1]}-{parts[2]}"
    return value


def temperature_text(value: str) -> str:
    text = (value or "").strip()
    return f"{text}°" if text else "—"


def location_caption(label: str, source: str) -> str:
    if source == "ip":
        return f"检测到你在中国 · {label}"
    if source == "province":
        return f"检测到你在中国 · {label}"
    return label

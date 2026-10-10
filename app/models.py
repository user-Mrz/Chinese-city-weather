"""与数据源无关的模型。

页面和测试只认这几个类型，换数据源时只要映射过来即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Place:
    """一个观测地点。不同数据源用不同字段，能填多少填多少。"""

    name: str  # 展示名，例如「杭州」
    province: str = ""  # 省名，例如「浙江省」
    adcode: str = ""  # 高德用的 6 位行政区编码
    station: str = ""  # 中央气象台用的站点编码，例如 HIieJ
    source: str = ""  # ip | province | manual
    note: str = ""

    @property
    def cache_key(self) -> str:
        return self.adcode or self.station or f"{self.province}/{self.name}"

    @property
    def resolved(self) -> bool:
        return bool(self.adcode or self.station or self.name)


@dataclass(frozen=True)
class LiveWeather:
    city: str
    weather: str
    temperature: str
    humidity: str
    winddirection: str
    windpower: str
    reporttime: str
    province: str = ""


@dataclass(frozen=True)
class ForecastDay:
    date: str
    week: str = ""
    dayweather: str = ""
    nightweather: str = ""
    daytemp: str = ""
    nighttemp: str = ""
    daywind: str = ""
    nightwind: str = ""
    daypower: str = ""
    nightpower: str = ""


@dataclass(frozen=True)
class Forecast:
    city: str = ""
    reporttime: str = ""
    days: list[ForecastDay] = field(default_factory=list)

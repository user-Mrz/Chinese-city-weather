"""中央气象台（www.nmc.cn）公开接口 —— 零注册的默认天气数据源。

链路：省列表 → 该省城市站点 → 站点编码 → 天气。

这些接口是公开页面在用的、无需任何 Key，境内直连。代价是它们并非官方承诺的
开放 API，字段和路径有可能变；所以这里做了多级缓存和明确的失败降级，
绝不让上游抖动变成白屏。

实测核对（2026-10）：
  GET /rest/province/all              -> [{code, name, url}]      （34 个省级区域）
  GET /rest/province/{provinceCode}   -> [{code, province, city, url}]
  GET /rest/weather?stationid={code}  -> {msg, code, data:{real, predict, air, ...}}

坑：
  * ``stationid`` 要的是上面的自定义 ``code``（形如 ``HIieJ``），不是全国实况表里
    的数字 WMO 站号（``58457``）—— 后者返回的是空数据。
  * 站点不存在时 ``data`` 是空字符串 ``""``，不是对象。
  * 缺测值统一是 ``9999``（数字和字符串都会出现）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

from .cache import TTLCache
from .config import Settings
from .models import Forecast, ForecastDay, LiveWeather

logger = logging.getLogger(__name__)

PROVINCE_LIST_TTL = 7 * 24 * 3600  # 省级区域列表几乎不变
CITY_LIST_TTL = 7 * 24 * 3600  # 城市站点列表同样按周缓存

_SUFFIXES = (
    "特别行政区",
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "自治区",
    "自治州",
    "地区",
    "盟",
    "省",
    "市",
    "县",
    "区",
)

_PLACEHOLDERS = {"", "9999", "9999.0", "null", "None", "nan"}


class NmcError(RuntimeError):
    """中央气象台接口不可用或返回了意外结构。"""


def normalize_name(value: str) -> str:
    """「杭州市」→「杭州」，用于跨数据源的城市名匹配。"""
    text = re.sub(r"\s+", "", value or "")
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
    return text


def as_text(value: object) -> str:
    """把 9999 之类的缺测值统一成空串。"""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return "" if float(value) == 9999.0 else f"{value:g}"
    text = str(value).strip()
    return "" if text in _PLACEHOLDERS else text


def week_of(date_text: str) -> str:
    """中央气象台不返回星期，用日期自己算成 "1".."7"。"""
    try:
        return str(datetime.strptime(date_text.strip(), "%Y-%m-%d").isoweekday())
    except ValueError:
        return ""


@dataclass(frozen=True)
class Station:
    code: str
    province: str
    city: str


def _match(
    stations: list[Station],
    city: str,
    province: str = "",
    *,
    limit: int = 0,
) -> list[Station]:
    """按城市名匹配站点，先精确、再规范化、最后包含。province 非空时优先同省。"""
    wanted = (city or "").strip()
    if not wanted:
        return []

    def in_province(station: Station) -> bool:
        return not province or normalize_name(station.province) == normalize_name(province)

    for predicate in (
        lambda s: s.city == wanted,
        lambda s: normalize_name(s.city) == normalize_name(wanted),
        lambda s: normalize_name(wanted) and normalize_name(wanted) in normalize_name(s.city),
        lambda s: normalize_name(s.city) in normalize_name(wanted),
    ):
        hits = [s for s in stations if predicate(s)]
        preferred = [s for s in hits if in_province(s)]
        chosen = preferred or hits
        if chosen:
            return chosen[:limit] if limit else chosen[:1]
    return []


class NmcClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._base_url = settings.nmc_base_url.rstrip("/")
        self._transport = transport
        self._sem = asyncio.Semaphore(max(1, settings.max_concurrency))
        self._client: httpx.AsyncClient | None = None

        self._provinces: TTLCache[dict[str, str]] = TTLCache(maxsize=2, ttl=PROVINCE_LIST_TTL)
        self._cities: TTLCache[list[Station]] = TTLCache(maxsize=64, ttl=CITY_LIST_TTL)
        self._all_cities: TTLCache[list[Station]] = TTLCache(maxsize=1, ttl=CITY_LIST_TTL)
        self._global_lock = asyncio.Lock()

    # ---------- 生命周期 ----------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._settings.nmc_timeout),
                headers={
                    "User-Agent": self._settings.http_user_agent,
                    "Referer": f"{self._base_url}/",
                    "Accept": "application/json,text/plain,*/*",
                },
                limits=httpx.Limits(max_connections=max(10, self._settings.max_concurrency * 2)),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        attempts = max(0, self._settings.nmc_retries) + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                async with self._sem:
                    response = await self.client.get(path, params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise NmcError(f"中央气象台请求失败：{exc}") from exc

        raise NmcError(f"中央气象台请求失败：{last_error}")

    # ---------- 省级区域 ----------

    async def province_index(self) -> dict[str, str]:
        """省名 → 省级编码。整表缓存一周。"""
        cached = self._provinces.get("all")
        if cached is not None:
            return cached

        payload = await self._get_json("/rest/province/all")
        if not isinstance(payload, list):
            raise NmcError("省级区域列表结构异常")

        by_name: dict[str, str] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            code = as_text(item.get("code"))
            name = as_text(item.get("name"))
            if code and name:
                by_name[name] = code

        if not by_name:
            raise NmcError("省级区域列表为空")

        self._provinces.set("all", by_name)
        return by_name

    async def find_province_code(self, province: str) -> str:
        by_name = await self.province_index()
        name = (province or "").strip()
        if not name:
            return ""
        if name in by_name:
            return by_name[name]

        target = normalize_name(name)
        for candidate, code in by_name.items():
            normalized = normalize_name(candidate)
            if normalized == target or normalized.startswith(target) or target.startswith(normalized):
                return code
        return ""

    # ---------- 城市站点 ----------

    async def cities(self, province_code: str) -> list[Station]:
        cached = self._cities.get(province_code)
        if cached is not None:
            return cached

        payload = await self._get_json(f"/rest/province/{province_code}")
        stations: list[Station] = []
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                code = as_text(item.get("code"))
                city = as_text(item.get("city"))
                if code and city:
                    stations.append(
                        Station(code=code, province=as_text(item.get("province")), city=city)
                    )

        self._cities.set(province_code, stations)
        return stations

    async def global_index(self, concurrency: int | None = None) -> list[Station]:
        """全国城市站点索引。

        手动选城市时没有省份可用，省内匹配也无从谈起，所以需要全量索引。
        代价是 34 次请求，一周只做一次。

        ``concurrency`` 用来限制并发：后台预热时传一个小值，
        避免把 ``max_concurrency`` 的槽位占满，让真实用户请求排在后面。
        """
        cached = self._all_cities.get("all")
        if cached is not None:
            return cached

        async with self._global_lock:
            cached = self._all_cities.get("all")
            if cached is not None:
                return cached

            by_name = await self.province_index()
            gate = asyncio.Semaphore(max(1, concurrency or self._settings.max_concurrency))

            async def fetch(code: str) -> list[Station]:
                async with gate:
                    return await self.cities(code)

            results = await asyncio.gather(
                *(fetch(code) for code in by_name.values()),
                return_exceptions=True,
            )
            stations: list[Station] = []
            for result in results:
                if isinstance(result, list):
                    stations.extend(result)
                else:
                    logger.warning("某省城市列表获取失败：%s", result)

            if not stations:
                raise NmcError("无法取得任何城市站点")

            self._all_cities.set("all", stations)
            logger.info("中央气象台全国城市索引已建立：%d 个站点", len(stations))
            return stations

    async def find_station(self, province: str, city: str) -> Station | None:
        """按省市名匹配站点。省内先查（1~2 次请求），不行再退到全国索引。"""
        if province:
            try:
                province_code = await self.find_province_code(province)
            except NmcError as exc:
                logger.warning("省级列表获取失败：%s", exc)
                province_code = ""
            if province_code:
                hits = _match(await self.cities(province_code), city, province)
                if hits:
                    return hits[0]

        try:
            hits = _match(await self.global_index(), city, province)
        except NmcError as exc:
            logger.warning("全国城市索引不可用：%s", exc)
            return None
        return hits[0] if hits else None

    async def search(self, keyword: str, limit: int = 12) -> list[Station]:
        """城市名搜索，供页面上的手动选择用。"""
        return _match(await self.global_index(), keyword, "", limit=limit)

    # ---------- 天气 ----------

    async def _weather_payload(self, station_code: str) -> dict:
        payload = await self._get_json("/rest/weather", {"stationid": station_code})
        if not isinstance(payload, dict):
            raise NmcError("天气接口结构异常")

        data = payload.get("data")
        # 站点不存在时 data 是空字符串，不是对象
        if not isinstance(data, dict):
            message = as_text(payload.get("msg")) or "unknown"
            raise NmcError(f"中央气象台没有该站点的数据（code={payload.get('code')}, msg={message}）")
        return data

    @staticmethod
    def _parse_live(data: dict) -> LiveWeather | None:
        real = data.get("real")
        if not isinstance(real, dict):
            return None
        weather = real.get("weather") if isinstance(real.get("weather"), dict) else {}
        wind = real.get("wind") if isinstance(real.get("wind"), dict) else {}
        station = real.get("station") if isinstance(real.get("station"), dict) else {}

        info = as_text(weather.get("info"))
        temperature = as_text(weather.get("temperature"))
        if not info and not temperature:
            return None

        return LiveWeather(
            city=as_text(station.get("city")),
            province=as_text(station.get("province")),
            weather=info,
            temperature=temperature,
            humidity=as_text(weather.get("humidity")),
            winddirection=as_text(wind.get("direct")),
            windpower=as_text(wind.get("power")),
            reporttime=as_text(real.get("publish_time")),
        )

    @staticmethod
    def _parse_forecast(data: dict) -> Forecast | None:
        predict = data.get("predict")
        if not isinstance(predict, dict):
            return None
        detail = predict.get("detail")
        if not isinstance(detail, list) or not detail:
            return None

        station = predict.get("station") if isinstance(predict.get("station"), dict) else {}
        days: list[ForecastDay] = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            date = as_text(item.get("date"))
            if not date:
                continue
            day = item.get("day") if isinstance(item.get("day"), dict) else {}
            night = item.get("night") if isinstance(item.get("night"), dict) else {}
            day_weather = day.get("weather") if isinstance(day.get("weather"), dict) else {}
            night_weather = night.get("weather") if isinstance(night.get("weather"), dict) else {}
            day_wind = day.get("wind") if isinstance(day.get("wind"), dict) else {}
            night_wind = night.get("wind") if isinstance(night.get("wind"), dict) else {}

            days.append(
                ForecastDay(
                    date=date,
                    week=week_of(date),
                    dayweather=as_text(day_weather.get("info")),
                    nightweather=as_text(night_weather.get("info")),
                    daytemp=as_text(day_weather.get("temperature")),
                    nighttemp=as_text(night_weather.get("temperature")),
                    daywind=as_text(day_wind.get("direct")),
                    nightwind=as_text(night_wind.get("direct")),
                    daypower=as_text(day_wind.get("power")),
                    nightpower=as_text(night_wind.get("power")),
                )
            )

        if not days:
            return None
        return Forecast(
            city=as_text(station.get("city")),
            reporttime=as_text(predict.get("publish_time")),
            days=days,
        )

    async def weather(self, station_code: str) -> tuple[LiveWeather | None, Forecast | None]:
        data = await self._weather_payload(station_code)
        live = self._parse_live(data)
        forecast = self._parse_forecast(data)
        if live is None and forecast is None:
            raise NmcError("中央气象台返回了空数据")
        return live, forecast

"""数据源编排：定位访问者 → 取天气，并负责缓存。

支持两种后端：

* **nmc**（默认，零注册）—— 太平洋电脑网 / ip-api 解析 IP 归属地，
  中央气象台公开接口取天气。境内直连、不需要任何 Key。
* **amap**（可选增强）—— 配了 AMAP_KEY 时自动启用，数据更全、坐标直接匹配。

``WEATHER_BACKEND=auto`` 时按「有没有 AMAP_KEY」自动选。
"""

from __future__ import annotations

import logging

import httpx

from .amap import AmapClient, AmapError
from .cache import TTLCache
from .config import Settings
from .geoip import resolve_place as geoip_resolve_place
from .location import ProvinceTable
from .models import Forecast, LiveWeather, Place
from .nmc import NmcClient, NmcError

logger = logging.getLogger(__name__)


class WeatherUnavailable(RuntimeError):
    """定位或天气查询失败，页面应给出友好提示而不是 500。"""


class WeatherService:
    def __init__(
        self,
        settings: Settings,
        province_table: ProvinceTable,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._province_table = province_table
        # 同一个 transport 同时喂给高德、中央气象台和 IP 归属地三个客户端，
        # 测试只需要构造一个假上游就能覆盖整条链路。
        self._amap = AmapClient(settings, transport=transport)
        self._nmc = NmcClient(settings, transport=transport)
        self._transport = transport

        self._geo_client: httpx.AsyncClient | None = None
        self.place_cache: TTLCache[Place] = TTLCache(
            maxsize=settings.ip_cache_size, ttl=settings.ip_cache_ttl
        )
        self.station_cache: TTLCache[Place] = TTLCache(
            maxsize=settings.ip_cache_size, ttl=settings.ip_cache_ttl
        )
        self.weather_cache: TTLCache[tuple[LiveWeather | None, Forecast | None]] = TTLCache(
            maxsize=settings.weather_cache_size, ttl=settings.weather_cache_ttl
        )

    # ---------- 生命周期 ----------

    @property
    def geo_client(self) -> httpx.AsyncClient:
        if self._geo_client is None:
            self._geo_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.geoip_timeout),
                headers={"User-Agent": self._settings.http_user_agent},
                transport=self._transport,
            )
        return self._geo_client

    async def aclose(self) -> None:
        await self._amap.aclose()
        await self._nmc.aclose()
        if self._geo_client is not None:
            await self._geo_client.aclose()
            self._geo_client = None

    # ---------- 后端选择 ----------

    @property
    def backend(self) -> str:
        configured = (self._settings.weather_backend or "auto").strip().lower()
        if configured == "amap":
            if not self._settings.amap_key:
                logger.warning("WEATHER_BACKEND=amap 但没有配置 AMAP_KEY，退回中央气象台")
                return "nmc"
            return "amap"
        if configured == "nmc":
            return "nmc"
        return "amap" if self._settings.amap_key else "nmc"

    @property
    def has_city_search(self) -> bool:
        """两种后端都支持城市搜索。"""
        return True

    # ---------- 定位 ----------

    def _capital(self, province: str) -> tuple[str, str] | None:
        return self._province_table.lookup(province)

    async def resolve_place(self, ip: str) -> Place | None:
        """把访问者 IP 解析成 Place。只缓存成功结果。"""
        cached = self.place_cache.get(ip)
        if cached is not None:
            return cached

        place = await self._resolve_amap(ip) if self.backend == "amap" else await self._resolve_geoip(ip)
        if place is not None:
            self.place_cache.set(ip, place)
        return place

    async def _resolve_geoip(self, ip: str) -> Place | None:
        base = await geoip_resolve_place(self.geo_client, ip)
        if base is None:
            logger.info("IP 归属地解析失败：%s", ip)
            return None

        # 只解析到省时，用省会兜底 —— 总比什么都不显示强，页面上会说明
        if not base.name or base.name == base.province:
            capital = self._capital(base.province)
            if capital is not None:
                return Place(
                    name=capital[0],
                    province=base.province,
                    source="province",
                    note=f"只定位到了{base.province}，下面显示{capital[0]}的天气",
                )
        return base

    async def _resolve_amap(self, ip: str) -> Place | None:
        try:
            found = await self._amap.ip_locate(ip)
        except AmapError as exc:
            logger.warning("高德 IP 定位失败 ip=%s: %s", ip, exc)
            return None

        if found.resolved:
            return Place(
                name=found.city or found.province or "未知位置",
                province=found.province,
                adcode=found.adcode,
                source="ip",
            )

        if found.province:
            capital = self._capital(found.province)
            if capital is not None:
                return Place(
                    name=capital[0],
                    province=found.province,
                    adcode=capital[1],
                    source="province",
                    note=f"只定位到了省级，下面显示{capital[0]}的天气",
                )
        return None

    # ---------- 天气 ----------

    async def weather(self, place: Place) -> tuple[Place, LiveWeather | None, Forecast | None]:
        """返回 (落地后的地点, 实况, 预报)。

        地点可能与传入的不同：中央气象台会把「浙江省」落到具体站点城市。
        """
        if self.backend == "amap":
            return await self._weather_amap(place)
        return await self._weather_nmc(place)

    async def _weather_amap(self, place: Place) -> tuple[Place, LiveWeather | None, Forecast | None]:
        if not place.adcode:
            raise WeatherUnavailable("没有可用的行政区编码")
        cached = self.weather_cache.get(place.adcode)
        if cached is not None:
            return place, cached[0], cached[1]

        try:
            live, forecast = await self._amap.weather(place.adcode)
        except AmapError as exc:
            raise WeatherUnavailable(str(exc)) from exc

        self._store(place.adcode, live, forecast)
        return place, live, forecast

    async def _weather_nmc(self, place: Place) -> tuple[Place, LiveWeather | None, Forecast | None]:
        resolved = await self._resolve_station(place)
        if resolved is None:
            raise WeatherUnavailable("没有找到这个城市的气象站点")

        cached = self.weather_cache.get(resolved.station)
        if cached is not None:
            return resolved, cached[0], cached[1]

        try:
            live, forecast = await self._nmc.weather(resolved.station)
        except NmcError as exc:
            raise WeatherUnavailable(str(exc)) from exc

        if live is None and forecast is None:
            raise WeatherUnavailable("中央气象台没有返回这个城市的数据")

        self._store(resolved.station, live, forecast)
        return resolved, live, forecast

    async def _resolve_station(self, place: Place) -> Place | None:
        cached = self.station_cache.get(place.cache_key)
        if cached is not None:
            return cached

        station = await self._nmc.find_station(place.province, place.name)
        note = place.note
        if station is None:
            capital = self._capital(place.province)
            if capital is not None:
                station = await self._nmc.find_station(place.province, capital[0])
                if station is not None:
                    note = f"只定位到{place.province}，下面显示{station.city}的天气"

        if station is None:
            return None

        resolved = Place(
            name=station.city,
            province=station.province or place.province,
            station=station.code,
            source=place.source,
            note=note,
        )
        self.station_cache.set(place.cache_key, resolved)
        return resolved

    def _store(self, key: str, live: LiveWeather | None, forecast: Forecast | None) -> None:
        """空结果不缓存，避免把一次上游抖动钉死十分钟。"""
        if key and (live is not None or forecast is not None):
            self.weather_cache.set(key, (live, forecast))

    # ---------- 预热 ----------

    async def warm_index(self) -> int:
        """后台预热全国城市站点索引。

        第一次手动搜城市需要 34 次请求（几秒），放在启动后台做掉，用户就不会撞上。
        并发压到 2 是为了不占满槽位 —— 预热期间到达的真实请求必须能立刻出去。
        失败无所谓，真正用到时会再试一次。
        """
        if self.backend != "nmc":
            return 0
        return len(await self._nmc.global_index(concurrency=2))

    # ---------- 城市搜索 ----------

    async def search_cities(self, keyword: str, limit: int = 12) -> list[dict[str, str]]:
        """城市名搜索。两种后端都支持：高德走行政区接口，中央气象台走全国站点索引。"""
        if self.backend == "amap":
            try:
                districts = await self._amap.search_districts(keyword)
            except AmapError as exc:
                logger.warning("行政区查询失败 q=%s: %s", keyword, exc)
                raise WeatherUnavailable(str(exc)) from exc
            return [
                {"name": d.name, "province": "", "adcode": d.adcode, "station": "", "level": d.level}
                for d in districts
            ]

        try:
            stations = await self._nmc.search(keyword, limit)
        except NmcError as exc:
            logger.warning("城市站点搜索失败 q=%s: %s", keyword, exc)
            raise WeatherUnavailable(str(exc)) from exc
        return [
            {"name": s.city, "province": s.province, "adcode": "", "station": s.code, "level": "city"}
            for s in stations
        ]

    @property
    def backend_label(self) -> str:
        return "高德地图" if self.backend == "amap" else "中央气象台"

    async def reverse_adcode(self, lng: float, lat: float) -> str:
        """经纬度 → adcode。只有高德支持，用于将来的浏览器 GPS 升级路径。"""
        if not self._settings.amap_key:
            raise WeatherUnavailable("经纬度反查需要配置 AMAP_KEY")
        try:
            return await self._amap.reverse_geocode(lng, lat)
        except AmapError as exc:
            raise WeatherUnavailable(str(exc)) from exc

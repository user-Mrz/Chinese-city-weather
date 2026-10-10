"""高德 Web 服务 API 客户端。

已核实的接口契约（文档最后更新 2026-02-02）：

* IP 定位  https://restapi.amap.com/v3/ip
  - ``ip`` 参数必须显式传访问者 IP；不传时高德定位的是「发起请求的客户」，
    也就是我们自己的服务器，会得到完全错误的城市。
  - 仅支持 IPv4，不支持国外 IP。
  - 定位失败时 ``province`` / ``city`` / ``adcode`` 返回的是**空数组 []** 而不是
    字符串，直接 str() 会渲染出 "['']" 这种东西。

* 天气查询 https://restapi.amap.com/v3/weather/weatherInfo
  - ``city`` 必填，且必须是 adcode（不能是城市名）。
  - ``extensions=base`` 返回 ``lives``（实况），``extensions=all`` 返回
    ``forecasts``（预报）—— 两者不互相包含，所以要看实况+预报需要两次调用。

* 行政区查询 https://restapi.amap.com/v3/config/district
  - 直接返回行政区的 ``adcode`` 与 ``level``，比输入提示接口更适合做城市选择。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .models import Forecast, ForecastDay, LiveWeather

logger = logging.getLogger(__name__)


class AmapError(RuntimeError):
    """高德接口不可用或返回了业务错误。"""


def as_text(value: Any) -> str:
    """把高德可能返回成空数组的字段安全地转成字符串。

    定位成功时是 ``"杭州市"``；定位失败时是 ``[]``。这个函数把后者变成 ``""``。
    """
    if isinstance(value, str):
        return value.strip()
    return ""


@dataclass(frozen=True)
class IpLocation:
    province: str = ""
    city: str = ""
    adcode: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.adcode)


@dataclass(frozen=True)
class District:
    name: str
    adcode: str
    level: str


class AmapClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._base_url = settings.amap_base_url.rstrip("/")
        self._transport = transport
        self._sem = asyncio.Semaphore(max(1, settings.max_concurrency))
        self._client: httpx.AsyncClient | None = None

    # ---------- 生命周期 ----------

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建，这样单元测试不需要跑 lifespan 也能构造。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._settings.amap_timeout),
                headers={"User-Agent": "weather-web/1.0"},
                limits=httpx.Limits(max_connections=max(10, self._settings.max_concurrency * 2)),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------- 底层请求 ----------

    async def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._settings.amap_key:
            raise AmapError("未配置 AMAP_KEY，请在 .env 中填写高德 Web 服务 Key")

        query = {**params, "key": self._settings.amap_key}
        attempts = max(0, self._settings.amap_retries) + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                async with self._sem:
                    response = await self.client.get(path, params=query)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("响应不是 JSON 对象")
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise AmapError(f"高德请求失败：{exc}") from exc

            if str(data.get("status")) != "1":
                info = as_text(data.get("info")) or "未知错误"
                infocode = as_text(data.get("infocode"))
                raise AmapError(f"高德返回错误：{info}（{infocode}）")
            return data

        raise AmapError(f"高德请求失败：{last_error}")

    # ---------- IP 定位 ----------

    async def ip_locate(self, ip: str) -> IpLocation:
        data = await self._get_json("/v3/ip", {"ip": ip})
        return IpLocation(
            province=as_text(data.get("province")),
            city=as_text(data.get("city")),
            adcode=as_text(data.get("adcode")),
        )

    # ---------- 天气 ----------

    async def _weather_live(self, adcode: str) -> LiveWeather | None:
        data = await self._get_json("/v3/weather/weatherInfo", {"city": adcode, "extensions": "base"})
        lives = data.get("lives")
        if not isinstance(lives, list) or not lives:
            return None
        item = lives[0]
        return LiveWeather(
            province=as_text(item.get("province")),
            city=as_text(item.get("city")),
            weather=as_text(item.get("weather")),
            temperature=as_text(item.get("temperature")),
            winddirection=as_text(item.get("winddirection")),
            windpower=as_text(item.get("windpower")),
            humidity=as_text(item.get("humidity")),
            reporttime=as_text(item.get("reporttime")),
        )

    async def _weather_forecast(self, adcode: str) -> Forecast | None:
        data = await self._get_json("/v3/weather/weatherInfo", {"city": adcode, "extensions": "all"})
        forecasts = data.get("forecasts")
        if not isinstance(forecasts, list) or not forecasts:
            return None
        item = forecasts[0]
        casts = item.get("casts") if isinstance(item.get("casts"), list) else []
        days = [
            ForecastDay(
                date=as_text(cast.get("date")),
                week=as_text(cast.get("week")),
                dayweather=as_text(cast.get("dayweather")),
                nightweather=as_text(cast.get("nightweather")),
                daytemp=as_text(cast.get("daytemp")),
                nighttemp=as_text(cast.get("nighttemp")),
                daywind=as_text(cast.get("daywind")),
                daypower=as_text(cast.get("daypower")),
                nightwind=as_text(cast.get("nightwind")),
                nightpower=as_text(cast.get("nightpower")),
            )
            for cast in casts
        ]
        return Forecast(
            city=as_text(item.get("city")),
            reporttime=as_text(item.get("reporttime")),
            days=days,
        )

    async def weather(self, adcode: str) -> tuple[LiveWeather | None, Forecast | None]:
        """并发取实况与预报。任一失败不影响另一个，两个都失败才抛错。"""
        live_result, forecast_result = await asyncio.gather(
            self._weather_live(adcode),
            self._weather_forecast(adcode),
            return_exceptions=True,
        )

        errors = [r for r in (live_result, forecast_result) if isinstance(r, BaseException)]
        live = None if isinstance(live_result, BaseException) else live_result
        forecast = None if isinstance(forecast_result, BaseException) else forecast_result

        if errors and live is None and forecast is None:
            raise AmapError(str(errors[0]))
        for error in errors:
            logger.warning("高德天气接口部分失败：%s", error)

        return live, forecast

    # ---------- 逆地理编码 ----------

    async def reverse_geocode(self, lng: float, lat: float) -> str:
        """经纬度 → adcode。

        为将来接入浏览器 GPS（需要 HTTPS）预留。注意高德与微信/浏览器定位
        同为 GCJ-02 坐标系，不需要做坐标转换。
        """
        data = await self._get_json(
            "/v3/geocode/regeo",
            {"location": f"{lng:.6f},{lat:.6f}", "extensions": "base", "radius": "1000"},
        )
        regeocode = data.get("regeocode")
        if not isinstance(regeocode, dict):
            return ""
        component = regeocode.get("addressComponent")
        if not isinstance(component, dict):
            return ""
        return as_text(component.get("adcode"))

    # ---------- 行政区查询（城市选择） ----------

    async def search_districts(self, keyword: str) -> list[District]:
        data = await self._get_json(
            "/v3/config/district",
            {"keywords": keyword, "subdistrict": "0", "extensions": "base"},
        )
        raw = data.get("districts")
        if not isinstance(raw, list):
            return []
        result: list[District] = []
        for item in raw:
            adcode = as_text(item.get("adcode"))
            name = as_text(item.get("name"))
            if adcode and name:
                result.append(District(name=name, adcode=adcode, level=as_text(item.get("level"))))
        return result

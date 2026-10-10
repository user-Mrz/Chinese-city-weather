"""FastAPI 应用：一个页面 + 少量 JSON 接口。"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote, unquote

import httpx
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .cache import TTLCache
from .config import STATIC_DIR, TEMPLATES_DIR, Settings, get_settings
from .location import ProvinceTable, client_ip, load_province_table, normalize_public_ipv4
from .models import Forecast, LiveWeather, Place
from .ratelimit import TokenBucketRateLimiter
from .render import (
    accent_color,
    location_caption,
    short_date,
    temperature_text,
    weather_icon,
    weekday_label,
    wind_angle,
    wind_text,
)
from .service import WeatherService, WeatherUnavailable

logger = logging.getLogger(__name__)

COOKIE_NAME = "city_name"
COOKIE_PROVINCE = "city_province"
COOKIE_ADCODE = "city_adcode"
COOKIE_MAX_AGE = 86400 * 30
EXEMPT_PATHS = {"/healthz", "/favicon.ico"}


@dataclass
class AppContext:
    settings: Settings
    service: WeatherService
    limiter: TokenBucketRateLimiter
    province_table: ProvinceTable
    search_cache: TTLCache[list[dict[str, str]]] = field(
        default_factory=lambda: TTLCache(maxsize=512, ttl=86400)
    )
    started_at: float = field(default_factory=time.time)


def build_context(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AppContext:
    """``transport`` 只在测试里传，用来把上游换成假响应。"""
    settings = settings or get_settings()
    return AppContext(
        settings=settings,
        service=WeatherService(settings, load_province_table(), transport=transport),
        limiter=TokenBucketRateLimiter(
            rate_per_minute=settings.rate_limit_per_minute,
            burst=settings.rate_limit_burst,
        ),
        province_table=load_province_table(),
    )


# ---------------------------------------------------------------- 请求解析


def debug_ip(request: Request, settings: Settings) -> str | None:
    """仅当 ALLOW_DEBUG_IP=true 时生效，用于本地模拟不同地区访问者。"""
    if not settings.allow_debug_ip:
        return None
    value = request.query_params.get("debug_ip")
    return value.strip() if value else None


def place_from_query(request: Request) -> Place | None:
    """URL 上显式指定的地点，优先级最高。"""
    params = request.query_params
    adcode = (params.get("adcode") or "").strip()
    name = (params.get("name") or "").strip() or (params.get("city") or "").strip()
    province = (params.get("province") or "").strip()

    adcode = adcode if adcode.isdigit() else ""
    if not adcode and not name:
        return None
    return Place(name=name or adcode, province=province, adcode=adcode, source="manual")


def place_from_cookie(request: Request) -> Place | None:
    """用户上次手动选过的城市。

    Cookie 值是 URL 编码后写入的：HTTP 头是 latin-1 编码，中文直接写会 500。
    """
    adcode = (request.cookies.get(COOKIE_ADCODE) or "").strip()
    name = unquote(request.cookies.get(COOKIE_NAME) or "")
    province = unquote(request.cookies.get(COOKIE_PROVINCE) or "")

    adcode = adcode if adcode.isdigit() else ""
    if not adcode and not name:
        return None
    return Place(name=name or adcode, province=province, adcode=adcode, source="manual")


def set_place_cookies(response: Response, place: Place) -> None:
    response.set_cookie(COOKIE_NAME, quote(place.name, safe=""), max_age=COOKIE_MAX_AGE, samesite="lax")
    response.set_cookie(
        COOKIE_PROVINCE, quote(place.province, safe=""), max_age=COOKIE_MAX_AGE, samesite="lax"
    )
    response.set_cookie(COOKIE_ADCODE, place.adcode or "", max_age=COOKIE_MAX_AGE, samesite="lax")


async def resolve_place(ctx: AppContext, request: Request) -> Place | None:
    """定位优先级：URL 参数 > Cookie（用户手动选过）> 访问者 IP。"""
    explicit = place_from_query(request) or place_from_cookie(request)
    if explicit is not None:
        return explicit

    raw = debug_ip(request, ctx.settings)
    if raw is None:
        raw = client_ip(request, ctx.settings.trusted_proxy_set)

    public_ip = normalize_public_ipv4(raw)
    if public_ip is None:
        logger.info("访问者地址不可用于定位：%r", raw)
        return None

    return await ctx.service.resolve_place(public_ip)


async def warm_index(ctx: AppContext) -> None:
    """后台预热全国城市站点索引。失败只记日志，不影响服务。"""
    try:
        count = await ctx.service.warm_index()
        if count:
            logger.info("全国城市索引预热完成：%d 个站点", count)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 预热失败不能拖垮启动
        logger.warning("全国城市索引预热失败（不影响正常使用）：%s", exc)


# ---------------------------------------------------------------- 应用


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    ctx = build_context(settings, transport=transport)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
        logger.info(
            "天气服务启动，数据源＝%s（WEATHER_BACKEND=%s）",
            ctx.service.backend_label,
            settings.weather_backend,
        )
        # 全国城市索引要 30 多次请求，放到后台预热，别让用户撞上这几秒
        warmup = asyncio.create_task(warm_index(ctx)) if settings.warm_index_on_startup else None
        yield
        if warmup is not None:
            warmup.cancel()
        await ctx.service.aclose()
        logger.info("天气服务已停止")

    app = FastAPI(
        title=settings.site_title,
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.ctx = ctx

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ------------------------------------------------------------ 限流

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith("/static") or path in EXEMPT_PATHS:
            return await call_next(request)

        ip = client_ip(request, settings.trusted_proxy_set)
        if not ctx.limiter.allow(ip):
            logger.warning("限流命中 ip=%s path=%s", ip, path)
            return templates.TemplateResponse(
                request,
                "error.html",
                {
                    "title": settings.site_title,
                    "heading": "访问太频繁了",
                    "message": "你在一分钟内的请求有点多，等一会儿再刷新试试。",
                    "version": __version__,
                },
                status_code=429,
            )
        return await call_next(request)

    # ------------------------------------------------------------ 页面

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        place = await resolve_place(ctx, request)

        live: LiveWeather | None = None
        forecast: Forecast | None = None
        error: str | None = None

        if place is not None:
            try:
                place, live, forecast = await ctx.service.weather(place)
            except WeatherUnavailable as exc:
                logger.warning("天气查询失败 place=%s: %s", place.name, exc)
                error = f"暂时拿不到这个地点的天气：{exc}"
            if live is None and forecast is None and error is None:
                error = "没查到这里的天气，换个城市试试？"

        context = {
            "title": settings.site_title,
            "place": place,
            "location_caption": location_caption(place.name, place.source) if place else "",
            "live": live,
            "forecast": forecast,
            "error": error,
            "accent": accent_color(live.weather) if live else "#4A5A52",
            "weather_icon": weather_icon,
            "accent_color": accent_color,
            "wind_text": wind_text,
            "wind_angle": wind_angle,
            "temperature_text": temperature_text,
            "short_date": short_date,
            "weekday_label": weekday_label,
            "source_label": ctx.service.backend_label,
            "show_debug": settings.allow_debug_ip,
            "auto_refresh_seconds": max(0, settings.page_refresh_seconds),
            "version": __version__,
        }
        response = templates.TemplateResponse(request, "index.html", context)

        explicit = place_from_query(request)
        if explicit is not None:
            set_place_cookies(response, place or explicit)
        return response

    @app.get("/search", response_class=HTMLResponse)
    async def search(request: Request, q: str = Query("", max_length=40)) -> Response:
        keyword = q.strip()
        results: list[dict[str, str]] = []
        error: str | None = None

        if keyword:
            cached = ctx.search_cache.get(keyword)
            if cached is not None:
                results = cached
            else:
                try:
                    results = await ctx.service.search_cities(keyword)
                    ctx.search_cache.set(keyword, results)
                except WeatherUnavailable as exc:
                    error = f"城市查询暂时不可用：{exc}"

        return templates.TemplateResponse(
            request,
            "search.html",
            {
                "title": settings.site_title,
                "query": keyword,
                "results": results,
                "error": error,
                "source_label": ctx.service.backend_label,
                "version": __version__,
            },
        )

    @app.get("/set-city")
    async def set_city(
        name: str = Query("", max_length=40),
        province: str = Query("", max_length=40),
        adcode: str = Query("", max_length=12),
    ) -> Response:
        clean_adcode = adcode if adcode.isdigit() else ""
        if not clean_adcode and not name.strip():
            return RedirectResponse("/", status_code=302)

        response = RedirectResponse("/", status_code=302)
        set_place_cookies(
            response,
            Place(name=name.strip() or clean_adcode, province=province.strip(), adcode=clean_adcode),
        )
        return response

    @app.get("/reset")
    async def reset() -> Response:
        response = RedirectResponse("/", status_code=302)
        for cookie in (COOKIE_NAME, COOKIE_PROVINCE, COOKIE_ADCODE):
            response.delete_cookie(cookie)
        return response

    # ------------------------------------------------------------ JSON 接口

    @app.get("/api/city/search")
    async def api_city_search(q: str = Query("", max_length=40)) -> JSONResponse:
        keyword = q.strip()
        if not keyword:
            return JSONResponse({"query": keyword, "results": []})
        try:
            results = await ctx.service.search_cities(keyword)
        except WeatherUnavailable as exc:
            return JSONResponse({"query": keyword, "results": [], "error": str(exc)}, status_code=502)
        return JSONResponse({"query": keyword, "results": results})

    @app.get("/api/weather")
    async def api_weather(
        adcode: str | None = Query(None, max_length=12),
        city: str | None = Query(None, max_length=40),
        province: str | None = Query(None, max_length=40),
        lat: float | None = Query(None, ge=-90, le=90),
        lng: float | None = Query(None, ge=-180, le=180),
    ) -> JSONResponse:
        """JSON 版天气接口。

        预留的升级路径：将来加上 HTTPS（Let's Encrypt 已支持公网 IP 证书）之后，
        前端可以用浏览器的 Geolocation API 拿到经纬度直接调 ``lat``/``lng``，
        后端会先逆地理编码成 adcode 再查 —— 不需要改动本文件的结构。
        """
        if lat is not None and lng is not None:
            if not settings.amap_key:
                return JSONResponse(
                    {"error": "经纬度查询需要配置 AMAP_KEY（或改用 city 参数）"}, status_code=400
                )
            try:
                resolved = await ctx.service.reverse_adcode(lng, lat)
            except WeatherUnavailable as exc:
                return JSONResponse({"error": str(exc)}, status_code=502)
            if not resolved:
                return JSONResponse({"error": "无法从该坐标解析出行政区"}, status_code=404)
            place = Place(name=resolved, adcode=resolved, source="manual")
        elif adcode or city:
            place = Place(
                name=(city or adcode or "").strip(),
                province=(province or "").strip(),
                adcode=adcode.strip() if adcode and adcode.strip().isdigit() else "",
                source="manual",
            )
        else:
            return JSONResponse({"error": "需要提供 city、adcode 或 lat+lng"}, status_code=400)

        try:
            place, live, forecast = await ctx.service.weather(place)
        except WeatherUnavailable as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)

        return JSONResponse(
            {
                "place": asdict(place),
                "live": asdict(live) if live else None,
                "forecast": asdict(forecast) if forecast else None,
                "source": ctx.service.backend_label,
            }
        )

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> Response:
        body = {
            "status": "ok",
            "version": __version__,
            "backend": ctx.service.backend,
            "backend_label": ctx.service.backend_label,
            "amap_key_configured": bool(settings.amap_key),
            "uptime_seconds": round(time.time() - ctx.started_at, 1),
            "place_cache": ctx.service.place_cache.stats(),
            "weather_cache": ctx.service.weather_cache.stats(),
            "rate_limiter": ctx.limiter.stats(),
        }
        return JSONResponse(body)

    # 一些内嵌预览容器（如 IDE 内置浏览器）会给每个页面注入 Vite HMR
    # 引导脚本 ``import('/@vite/client')``。本站不是 Vite 开发服务器，
    # 该请求 404 会让这类容器判定页面没加载完、反复重试整页导航，
    # 表现为搜索等表单跳转一直转圈。这里给一个零行为的兼容桩：
    # 对真实用户和普通浏览器完全无影响（它们永远不会请求这个路径）。
    @app.get("/@vite/client", response_class=PlainTextResponse)
    async def vite_client_stub() -> Response:
        stub = (
            "export function createHotContext(){return{"
            "on:function(){},off:function(){},"
            "send:function(){},close:function(){}};}\n"
        )
        return Response(stub, media_type="application/javascript; charset=utf-8")

    return app


app = create_app()


def main() -> None:
    """systemd 的入口：``python -m app.main``。

    直接把 app 对象交给 uvicorn，而不是传 "app.main:app" 字符串 ——
    后者会把模块再导入一次，导致 create_app() 跑两遍、多出一整套
    没人用的缓存和限流器。代价是不能开 reload / 多 worker，
    而这两样本项目本来就不需要（见 deploy/weather-web.service 的说明）。
    """
    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        server_header=False,
    )


if __name__ == "__main__":
    main()

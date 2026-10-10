"""运行时配置。所有配置项都可以用环境变量或 .env 覆盖。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 数据源 ----
    # auto : 配了 AMAP_KEY 就用高德，否则用中央气象台（零注册默认）
    # nmc  : 强制用中央气象台公开接口（无需任何 Key）
    # amap : 强制用高德（必须配 AMAP_KEY）
    weather_backend: str = "auto"
    http_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # ---- 高德开放平台（可选）----
    amap_key: str = ""
    amap_base_url: str = "https://restapi.amap.com"
    amap_timeout: float = 5.0
    amap_retries: int = 1

    # ---- 中央气象台公开接口（默认数据源，无需 Key）----
    nmc_base_url: str = "https://www.nmc.cn"
    nmc_timeout: float = 6.0
    nmc_retries: int = 1

    # ---- IP 归属地（免注册）----
    geoip_timeout: float = 5.0

    # ---- 服务监听 ----
    host: str = "0.0.0.0"
    port: int = 80
    site_title: str = "天气查询"

    # ---- 缓存（秒）----
    weather_cache_ttl: int = 600
    weather_cache_size: int = 2048
    ip_cache_ttl: int = 3600
    ip_cache_size: int = 10000
    district_cache_ttl: int = 86400

    # 首页自动刷新间隔（秒）。默认与天气缓存一致：每次自动刷新刚好拿到新数据。
    # 设为 0 关闭页面自动刷新（仍可手动刷新）。
    page_refresh_seconds: int = 600

    # ---- 限流 ----
    rate_limit_per_minute: int = 60
    rate_limit_burst: int = 20

    # ---- 并发上限，保护高德配额 ----
    max_concurrency: int = 8

    # 启动后在后台预热全国城市站点索引（30 多次请求，约 5~8 秒）
    warm_index_on_startup: bool = True

    # ---- 安全 ----
    # 可信反向代理 IP（逗号分隔）。留空 = 直连模式，完全不信任 X-Forwarded-For。
    trusted_proxies: str = ""
    # 仅本地调试：允许 ?debug_ip= 模拟访问者。生产必须为 false。
    allow_debug_ip: bool = False

    @property
    def trusted_proxy_set(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.trusted_proxies.split(",") if part.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

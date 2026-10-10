"""免注册的 IP 归属地解析。

主源是太平洋电脑网的公开接口：境内直连、免注册、直接返回中文省市。
兜底 ip-api.com（境外，但同样免注册，且键名稳定）。
两个都失败时返回 None，页面会退回到手动选择城市 —— 不会白屏。

注意：ip-api.com 的免费端点不支持 HTTPS，只能用 http。服务端出站调用没问题，
但它是明文的，所以我们把它放在兜底位置。
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from .models import Place

logger = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def _decode(response: httpx.Response) -> str:
    """按响应声明的字符集解码，失败再退到 utf-8 / gbk。"""
    for encoding in (response.charset_encoding, "utf-8", "gbk"):
        if not encoding:
            continue
        try:
            return response.content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return response.content.decode("utf-8", "replace")


def _extract_json(text: str) -> dict | None:
    """接口会在 JSON 前后裹一堆换行，这里把对象抠出来。"""
    match = _JSON_OBJECT.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class PconlineResolver:
    """太平洋电脑网 IP 归属地。境内、免注册、返回中文省市区。"""

    URL = "https://whois.pconline.com.cn/ipJson.jsp"

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout

    async def resolve(self, client: httpx.AsyncClient, ip: str) -> Place | None:
        response = await client.get(self.URL, params={"ip": ip, "json": "true"}, timeout=self._timeout)
        response.raise_for_status()
        data = _extract_json(_decode(response))
        if not data or str(data.get("err") or "").strip():
            return None

        province = str(data.get("pro") or "").strip()
        city = str(data.get("city") or "").strip()
        if not province and not city:
            return None
        return Place(name=city or province, province=province, source="ip")


class IpApiResolver:
    """ip-api.com 兜底。免费端点只支持 http。"""

    URL = "http://ip-api.com/json/{ip}"

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout

    async def resolve(self, client: httpx.AsyncClient, ip: str) -> Place | None:
        response = await client.get(
            self.URL.format(ip=ip),
            params={"lang": "zh-CN", "fields": "status,country,regionName,city"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("status") != "success":
            return None

        province = str(data.get("regionName") or "").strip()
        city = str(data.get("city") or "").strip()
        if not province and not city:
            return None
        return Place(name=city or province, province=province, source="ip")


async def resolve_place(client: httpx.AsyncClient, ip: str) -> Place | None:
    """依次尝试各个 IP 归属地来源，第一个成功的胜出。"""
    for resolver in (PconlineResolver(), IpApiResolver()):
        try:
            place = await resolver.resolve(client, ip)
        except Exception as exc:  # noqa: BLE001 - 换源继续，不能因为一个源挂了就白屏
            logger.warning("IP 归属地解析失败（%s）：%s", type(resolver).__name__, exc)
            continue
        if place is not None and place.resolved:
            return place
    return None

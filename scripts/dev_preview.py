"""本地预览：带假数据起一个服务，不需要联网、也不需要任何 Key。

    python scripts/dev_preview.py --port 8080

想直接看真实数据的话不用这个脚本 —— 默认数据源本来就免注册，
直接 `python -m app.main` 配合 PORT=8080 即可。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

# ---- IP 归属地 ----
PCONLINE = '\n\n{"ip":"114.114.114.114","pro":"浙江省","city":"杭州市","err":""}\n\n'

# ---- 中央气象台 ----
PROVINCES = [
    {"code": "AZJ", "name": "浙江省", "url": "/publish/forecast/AZJ.html"},
    {"code": "ABJ", "name": "北京市", "url": "/publish/forecast/ABJ.html"},
]
CITIES = {
    "AZJ": [
        {"code": "HIieJ", "province": "浙江省", "city": "杭州"},
        {"code": "pxfeS", "province": "浙江省", "city": "嘉兴"},
        {"code": "VNwVr", "province": "浙江省", "city": "洞头"},
    ],
    "ABJ": [{"code": "Wqsps", "province": "北京市", "city": "北京"}],
}
def _d(date: str, day: str, night: str, day_t: str, night_t: str, wind: str, power: str) -> dict:
    return {
        "date": date,
        "day": {"weather": {"info": day, "temperature": day_t}, "wind": {"direct": wind, "power": power}},
        "night": {"weather": {"info": night, "temperature": night_t}, "wind": {"direct": wind, "power": power}},
    }


WEATHER = {
    "HIieJ": {
        "msg": "success",
        "code": 0,
        "data": {
            "real": {
                "station": {"code": "HIieJ", "province": "浙江省", "city": "杭州"},
                "publish_time": "2026-10-10 09:35",
                "weather": {"temperature": 23.2, "humidity": 70.0, "info": "多云", "feelst": 25.0},
                "wind": {"direct": "西北风", "power": "微风", "speed": 1.7},
            },
            "predict": {
                "station": {"code": "HIieJ", "province": "浙江省", "city": "杭州"},
                "publish_time": "2026-10-10 08:00",
                "detail": [
                    _d("2026-10-10", "晴", "多云", "27", "18", "西南风", "微风"),
                    _d("2026-10-11", "小雨", "小雨", "24", "16", "东北风", "3-4"),
                    _d("2026-10-12", "雷阵雨", "阴", "22", "15", "西北风", "4-5"),
                    _d("2026-10-13", "多云", "晴", "26", "17", "南风", "微风"),
                    _d("2026-10-14", "晴", "晴", "28", "19", "东南风", "微风"),
                    _d("2026-10-15", "阴", "小雨", "25", "18", "东风", "3-4"),
                    _d("2026-10-16", "中雨", "小雨", "21", "16", "北风", "4-5"),
                ],
            },
        },
    }
}


def demo_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host or ""
        path = request.url.path

        if host == "whois.pconline.com.cn" or host.endswith("pconline.com.cn"):
            return httpx.Response(200, text=PCONLINE)
        if host == "www.nmc.cn":
            if path == "/rest/province/all":
                return httpx.Response(200, json=PROVINCES)
            if path.startswith("/rest/province/"):
                return httpx.Response(200, json=CITIES.get(path.rsplit("/", 1)[-1], []))
            if path == "/rest/weather":
                payload = WEATHER.get(request.url.params.get("stationid", ""))
                if payload:
                    return httpx.Response(200, json=payload)
            return httpx.Response(200, json={"msg": "success", "code": 0, "data": ""})

        return httpx.Response(404, json={"error": f"unmocked host {host}"})

    return httpx.MockTransport(handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="本地预览（假数据，无需联网、无需 Key）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    settings = Settings(
        _env_file=None,
        weather_backend="nmc",
        allow_debug_ip=True,
        site_title="天气查询 · 预览",
    )
    app = create_app(settings, transport=demo_transport())

    print(f"\n  预览地址：http://{args.host}:{args.port}/?debug_ip=114.114.114.114")
    print(f"  城市搜索：http://{args.host}:{args.port}/search?q=杭州")
    print(f"  直接出 JSON：http://{args.host}:{args.port}/api/weather?city=杭州\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", server_header=False)


if __name__ == "__main__":
    main()

"""对已经部署好的服务做一遍冒烟检查。

    python scripts/smoke.py http://127.0.0.1:8080
    python scripts/smoke.py http://203.0.113.10          # 线上

只依赖标准库，可以在任何一台能访问到服务的机器上跑。
默认数据源不需要任何 Key，所以这里不检查凭证，只检查链路是否通。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from urllib.parse import quote

TIMEOUT = 15


def fetch(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "weather-web-smoke"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001 - 连接失败也要变成一条结果，而不是崩掉
        print(f"  连接失败：{exc}")
        return 0, b""


def main() -> int:
    parser = argparse.ArgumentParser(description="天气服务冒烟检查")
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8080", help="服务地址")
    parser.add_argument("--city", default="杭州", help="用来测试的城市名")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    quoted_city = quote(args.city)
    print(f"目标：{base}\n")

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}{f'  {detail}' if (detail and not ok) else ''}")
        if not ok:
            failures.append(name)

    # 1. 健康检查
    status, body = fetch(f"{base}/healthz")
    health: dict = {}
    if status == 200 and body:
        try:
            health = json.loads(body)
        except json.JSONDecodeError:
            health = {}
    check("/healthz 可访问", status == 200, f"HTTP {status}")
    check("/healthz 返回预期字段", "version" in health)
    if health:
        print(f"  [信息] 数据源 = {health.get('backend_label')}（{health.get('backend')}）")

    # 2. 首页
    status, body = fetch(f"{base}/")
    check("首页可访问", status == 200, f"HTTP {status}")
    text = body.decode("utf-8", "replace")
    check("首页是完整 HTML", text.lstrip().startswith("<!DOCTYPE html>"))
    check("首页含气象读数版式", "观测地点" in text and "换个地方" in text)

    # 3. 静态资源
    check("样式表可访问", fetch(f"{base}/static/style.css")[0] == 200)

    # 4. 天气接口（真正会打上游的那条）
    status, body = fetch(f"{base}/api/weather?city={quoted_city}")
    check("天气接口可访问", status == 200, f"HTTP {status}")
    if status == 200:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        check("天气接口返回地点", bool(payload.get("place")))
        check("天气接口有实况或预报", bool(payload.get("live") or payload.get("forecast")))
        if payload.get("live"):
            live = payload["live"]
            print(f"  [信息] {live.get('city')} {live.get('weather')} {live.get('temperature')}℃ {live.get('reporttime')}")
    else:
        detail = body.decode("utf-8", "replace")[:200]
        print(f"  [信息] 上游返回：{detail}")

    # 5. 城市搜索（会建立全国站点索引，首次可能慢一点）
    status, body = fetch(f"{base}/api/city/search?q={quoted_city}")
    check("城市搜索接口可访问", status == 200, f"HTTP {status}")
    if status == 200:
        try:
            results = json.loads(body).get("results") or []
        except json.JSONDecodeError:
            results = []
        check("城市搜索有结果", bool(results))

    status, _ = fetch(f"{base}/search?q={quoted_city}")
    check("城市搜索页面可访问", status == 200, f"HTTP {status}")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# 打开就知道你在哪、那儿什么天气

一个单页服务：用户用手机或电脑浏览器访问服务器公网 IP，服务端**根据访问者 IP 判断城市**，直接返回当地天气。

**不需要注册任何账号，不需要任何 API Key。** 拉下来部署完就能用。

```
用户浏览器 ──HTTP──▶ http://<公网IP>/
                        uvicorn + FastAPI（单进程）
                          │ ① 取真实 client IP
                          │ ② IP → 省市（太平洋电脑网，缓存 1h）
                          │ ③ 省市 → 气象站（中央气象台，缓存 7 天）
                          │ ④ 站点 → 天气（中央气象台，缓存 10min）
                          │ ⑤ Jinja2 服务端渲染 HTML
                          ▼
                       全境内部署，境内直连
```

无数据库、无反向代理、无外部 CDN、不用备案、不用 HTTPS 证书。内存占用 ~100MB。

---

## 为什么用 IP 定位而不是浏览器定位

`navigator.geolocation` 只在 HTTPS 下工作（[Chrome 从 50 版起已从非安全来源移除](https://developer.chrome.com/blog/geolocation-on-secure-contexts-only?hl=zh-cn)），而 HTTPS 又牵出证书、域名、备案一连串事情。

关键在于：**查天气真正需要的是城市，不是经纬度**。服务端 IP 定位直接给到这个，链路更短、更快、零权限弹窗。精度到城市级 —— 对"今天要不要带伞"完全够用。

想要 GPS 级精度也有升级路径，见文末。

---

## 快速开始

### 本地跑起来（真实数据，不需要 Key）

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

ALLOW_DEBUG_IP=true PORT=8080 python -m app.main
```

打开 <http://127.0.0.1:8080/?debug_ip=114.114.114.114> —— 这是真实数据，不是演示。

想用纯假数据做界面检查（不联网）：

```bash
python scripts/dev_preview.py --port 8080
```

### 部署到阿里云 2核2G

```bash
git clone <你的仓库> weather-web && cd weather-web
sudo bash deploy/install.sh
```

脚本会：装依赖 → 建 `weather` 系统用户 → 同步代码到 `/opt/weather-web` → 建虚拟环境 → 写 `.env` → 装 systemd 服务 → 配 2G swap → 限制 journald 占用 → 设时区并启用时间同步 → 启动服务。

脚本是幂等的，重复执行等于原地升级。

**然后只剩一件事：去阿里云控制台的「安全组」放行你用的端口。**

> 服务在服务器上跑得好好的，外面却打不开 —— 九成是安全组没放行。这是最高频的坑。

验证：

```bash
python3 scripts/smoke.py http://<公网IP>/
```

### 端口怎么选（部署前先花两分钟测）

阿里云对未备案的拦截主要识别**域名**（80 读 Host、443 读 SNI），裸 IP 通常放行，但存在个别阻断报告，所以先实测：

```bash
cd /tmp && python3 -m http.server 80
```

用**手机 4G**（不要用 WiFi）访问 `http://<公网IP>/`：

- **能打开** → 保持默认 `PORT=80`，URL 最干净
- **打不开** → 在 `/opt/weather-web/.env` 里改成 `PORT=8080`，访问 `http://<IP>:8080/`

功能完全一致，只是 URL 多个端口号。

---

## 数据源

| 环节 | 数据源 | 需要 Key | 说明 |
|---|---|---|---|
| IP → 省市 | 太平洋电脑网 `whois.pconline.com.cn` | 否 | 境内直连，返回中文省市 |
| IP → 省市（兜底） | `ip-api.com` | 否 | 境外，只在主源失败时用 |
| 省市 → 气象站 | 中央气象台 `www.nmc.cn/rest/province` | 否 | 全国 2529 个站点，缓存 7 天 |
| 站台 → 天气 | 中央气象台 `www.nmc.cn/rest/weather` | 否 | 实况 + 7 天预报 |

可选增强：配了 `AMAP_KEY` 后自动切到高德（`WEATHER_BACKEND=auto` 的默认行为）。高德需要自己去[控制台](https://console.amap.com/dev/key/app)申请 **Web 服务** 类型的 Key，免费但要实名。

### 需要知道的风险

中央气象台和太平洋电脑网用的**不是官方承诺的开放 API**，而是公开页面在用的接口。它们有可能改路径、改字段。项目的应对是：

- **多级缓存**：IP 归属地 1 小时、城市站点 7 天、天气 10 分钟。绝大多数请求不会碰上游。
- **换源降级**：IP 归属地主源挂了自动换 ip-api。
- **绝不白屏**：上游全挂时页面给出可操作的提示和手动选择城市的入口。
- 如果你更看重接口稳定性，可以配 `AMAP_KEY` 走官方 API。

---

## 配置项

全部通过 `/opt/weather-web/.env` 覆盖，改完 `systemctl restart weather-web`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEATHER_BACKEND` | `auto` | `auto`＝有 Key 用高德、没 Key 用中央气象台；也可强制 `nmc` / `amap` |
| `AMAP_KEY` | 空 | 可选。填了就用高德 |
| `HOST` / `PORT` | `0.0.0.0` / `80` | 监听地址与端口 |
| `WEATHER_CACHE_TTL` | 600 | 天气缓存秒数 |
| `IP_CACHE_TTL` | 3600 | IP→城市 缓存秒数 |
| `RATE_LIMIT_PER_MINUTE` | 60 | 每 IP 每分钟请求数 |
| `RATE_LIMIT_BURST` | 20 | 突发桶容量 |
| `MAX_CONCURRENCY` | 8 | 同时发往上游的请求上限 |
| `WARM_INDEX_ON_STARTUP` | true | 启动后后台预热全国城市索引（约 5~8 秒） |
| `TRUSTED_PROXIES` | 空 | 可信反向代理 IP。**直连时务必留空**，见安全说明 |
| `ALLOW_DEBUG_IP` | false | 允许 `?debug_ip=` 模拟访问者。**生产必须 false** |

---

## 它是怎么工作的

### 定位兜底链

IP 定位不是万能的（IPv6、境外 IP、移动网络出口 IP 都可能失败），所以有三层降级，保证页面永远有结果：

| 层级 | 触发条件 | 行为 |
|---|---|---|
| 1 | 正常 | 解析出城市，查该城市天气 |
| 2 | 只解析到省 | 回退到省会，并在页面明说 |
| 3 | IPv6 / 境外 / 全部失败 | 显示「未识别」，给出城市搜索框 |

### 上游接口的坑（代码里都注释了）

**中央气象台**

- `/rest/weather` 的 `stationid` 要的是 `/rest/province/{省}` 返回的自定义编码（形如 `HIieJ`），**不是**全国实况表里的数字 WMO 站号（`58457`）—— 后者返回空数据。
- 站点不存在时 `data` 返回的是**空字符串 `""`**，不是对象。
- 缺测值统一是 `9999`（数字和字符串都会出现），直接渲染会显示成 `9999℃`。

**太平洋电脑网**

- 返回的 JSON 前后裹着好几行换行，得先把对象抠出来。
- 失败时 `err` 字段非空，而不是 HTTP 错误码。

**城市名匹配**

不同源对同一城市的写法不一样（`杭州市` / `杭州`），所以做了后缀归一化（省/市/县/区/自治区/自治州…）+ 精确 → 归一化 → 包含 的三级匹配，并且优先同省。

---

## 安全说明

这几条是真实存在的洞，代码里都做了处理，改动时请勿绕过：

1. **不要启用 uvicorn 的 `--proxy-headers`**。直连模式下它会让 `X-Forwarded-For` 覆盖客户端地址，而那个头任何人都能伪造 —— 等于同时打开"指定任意城市"和"绕过限流"两个口子。systemd unit 里已注明。
2. **`X-Forwarded-For` 只在可信代理场景下才读**，且必须先把代理 IP 写进 `TRUSTED_PROXIES`。默认直连模式完全不读。
3. **`ALLOW_DEBUG_IP` 生产必须为 false**，否则 `?debug_ip=` 就是一个可任意指定位置的后门。
4. 每 IP 限流 + 全局并发上限，保护上游配额（也避免我们的进程被当成爬虫）。
5. systemd 加固：`NoNewPrivileges`、`ProtectSystem=strict`、`ProtectHome`、`PrivateTmp`，且用 80 端口时**不需要 root**（只授予 `CAP_NET_BIND_SERVICE`）。
6. 上游返回的内容全部当数据用，不当作指令。

---

## 运维手册

```bash
systemctl status weather-web            # 状态
journalctl -u weather-web -f            # 实时日志
journalctl -u weather-web --since '1 hour ago'
systemctl restart weather-web           # 重启
sudo bash /opt/weather-web/deploy/install.sh   # 重新部署（幂等，等于升级）
curl -s localhost/healthz | python3 -m json.tool   # 数据源、缓存命中率、运行时长
```

**内存**：应用约 100MB。systemd 设了 `MemoryHigh=384M` / `MemoryMax=512M` 作为护栏。

```bash
systemctl show weather-web -p MemoryCurrent
```

**日志清理**：`/etc/systemd/journald.conf.d/99-weather-web.conf` 已限制 `SystemMaxUse=200M`。

---

## 常见问题

**打不开 / 连接超时**
1. 安全组放行了吗？（最高频）
2. `systemctl status weather-web` 在跑吗？
3. 服务器上 `curl -s localhost/healthz` 通吗？通说明是网络层的问题。

**城市不对**
IP 定位是城市级的，移动网络的出口 IP 可能落在运营商机房所在城市，这是预期行为不是 bug。页面底部「换个地方」可以手动纠正，选择会被记住 30 天。想回到自动定位点「回到自动定位」。

**首次搜城市很慢**
第一次要建立全国 2529 个站点的索引（34 次请求，约 5~8 秒）。默认启动时已在后台预热，所以正常用户不会撞上。

**返回 429**
触发了每 IP 限流。等一分钟，或调高 `RATE_LIMIT_PER_MINUTE`。

**页面显示「未识别」**
访问者是 IPv6、境外 IP，或两个 IP 归属地源都失败了。可以手动搜城市。

**中文城市名导致 500**
曾经有个真 bug：HTTP 头是 latin-1 编码，中文城市名直接写进 Cookie 会抛 `UnicodeEncodeError`。现在城市名是 URL 编码后写入的（`app/main.py::set_place_cookies`）。测试里有往返用例守着。

---

## 开发与测试

```bash
pip install -r requirements-dev.txt
python -m pytest                      # 155 个用例，全程离线
python -m pytest -k "nmc or wind or cookie"
```

测试用一个按 host 分发的 `httpx.MockTransport` 把所有出站请求换掉，所以两条数据源链路都能在完全离线的情况下端到端跑通。

覆盖的重点：空数组 / 空字符串 / `9999` 这些上游的坑、城市名归一化匹配、X-Forwarded-For 伪造必须被忽略、缓存与限流边界、中文 Cookie 往返。

想验证真实链路（会联网，不需要 Key）：

```bash
python .devtools/verify_real.py      # 仓库外的开发脚本，见 .devtools/
```

---

## 项目结构

```
app/
  config.py       运行时配置（pydantic-settings）
  main.py         FastAPI 应用与路由
  service.py      数据源编排：定位 → 城市 → 站点 → 天气，含缓存与降级
  nmc.py          中央气象台客户端（默认数据源，零注册）
  geoip.py        IP 归属地解析（免注册，双源）
  amap.py         高德客户端（可选增强）
  location.py     取真实 IP、公网地址过滤、省级兜底表
  models.py       与数据源无关的模型
  render.py       图标、主色、风向角度、文案（纯函数，可单测）
  cache.py        进程内 LRU + TTL
  ratelimit.py    按 IP 的令牌桶
  templates/      Jinja2 模板
  static/         样式表
data/
  province_capitals.json   34 个省级行政区 → 省会
scripts/
  dev_preview.py  纯假数据的本地预览（不联网）
  smoke.py        对已部署服务做冒烟检查
deploy/
  install.sh      一键部署（幂等）
  weather-web.service
tests/
```

设计上刻意把 `render.py`、`models.py` 和上游解析做成不依赖 FastAPI 的纯逻辑，所以两条数据源的解析、城市匹配、风向换算都能直接跑单测。

---

## 后续升级路径：HTTPS + GPS 精确定位

后端接口已经预留好了（`/api/weather?lat=&lng=`），**升级不需要改动后端结构**：

```bash
# 1. 给公网 IP 签证书（Let's Encrypt 的 IP 证书已于 2026-01-15 正式 GA）
sudo certbot certonly --preferred-profile shortlived \
  --webroot --webroot-path /var/www/html \
  --ip-address <你的公网IP>

# 2. 用 Caddy 或 nginx 在 443 上终止 TLS 并反代到 127.0.0.1:8080
#    证书路径：/etc/letsencrypt/live/<IP>/fullchain.pem 与 privkey.pem
#    注意 certbot 尚不支持自动安装 IP 证书，需要配 --deploy-hook 让 web 服务器重载
```

然后在页面上加一个「使用我的精确位置」按钮：

```js
navigator.geolocation.getCurrentPosition(async (pos) => {
  const r = await fetch(`/api/weather?lat=${pos.coords.latitude}&lng=${pos.coords.longitude}`);
  // 注意：这个接口目前依赖高德做逆地理编码，需要配 AMAP_KEY
});
```

要点：

- IP 证书**强制**走 `shortlived` profile，有效期 160 小时（约 6.7 天），**必须**确保自动续期可靠。
- 验证方式只支持 `http-01`（80 端口）和 `tls-alpn-01`（443 端口），那两个端口得先通。
- 高德与浏览器定位同为 **GCJ-02** 坐标系，不需要坐标转换。

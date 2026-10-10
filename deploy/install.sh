#!/usr/bin/env bash
#
# 部署「网页版自动定位查天气」到 Ubuntu / Debian 服务器。
# 可重复执行：脚本是幂等的，重新跑一次等于原地升级。
#
#   sudo bash deploy/install.sh
#
# 环境变量：
#   SWAP_SIZE_MB=2048   需要的 swap 大小（已有足够 swap 时会跳过）
#   SKIP_SWAP=1         完全跳过 swap 配置
#
set -euo pipefail

APP_NAME="weather-web"
APP_USER="weather"
APP_DIR="/opt/${APP_NAME}"
UNIT="/etc/systemd/system/${APP_NAME}.service"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWAP_SIZE_MB="${SWAP_SIZE_MB:-2048}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }

# --------------------------------------------------------------- 前置检查

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行：sudo $0" >&2
  exit 1
fi

if [[ ! -f "${SRC_DIR}/requirements.txt" || ! -d "${SRC_DIR}/app" ]]; then
  echo "找不到项目文件，请从仓库根目录运行：sudo bash deploy/install.sh" >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "这个脚本只支持 Ubuntu / Debian（需要 apt-get）。" >&2
  exit 1
fi

# --------------------------------------------------------------- 系统依赖

log "安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip rsync ca-certificates

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')
if [[ "$PY_OK" != "1" ]]; then
  echo "需要 Python 3.10 以上，当前是 $(python3 -V)。" >&2
  exit 1
fi
log "Python 版本 $(python3 -V)"

# --------------------------------------------------------------- 时区与时钟

log "设置时区为 Asia/Shanghai 并启用时间同步"
# 签名与日志都依赖准确时间，时钟漂移会带来排查不出来的怪问题
timedatectl set-timezone Asia/Shanghai 2>/dev/null || warn "时区设置失败，请手动检查"
systemctl enable --now systemd-timesyncd 2>/dev/null || warn "systemd-timesyncd 不可用，请确认时钟准确"

# --------------------------------------------------------------- swap

current_swap_kb=$(awk '/^SwapTotal:/{print $2}' /proc/meminfo)
current_swap_mb=$(( current_swap_kb / 1024 ))

if [[ "${SKIP_SWAP:-0}" == "1" ]]; then
  log "按 SKIP_SWAP=1 跳过 swap 配置"
elif (( current_swap_mb >= SWAP_SIZE_MB )); then
  log "已有 ${current_swap_mb}MB swap，跳过"
else
  log "创建 ${SWAP_SIZE_MB}MB swap（当前 ${current_swap_mb}MB）"
  if [[ ! -f /swapfile ]]; then
    if ! fallocate -l "${SWAP_SIZE_MB}M" /swapfile 2>/dev/null; then
      warn "fallocate 不可用，改用 dd（会慢一些）"
      dd if=/dev/zero of=/swapfile bs=1M count="${SWAP_SIZE_MB}" status=none
    fi
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile 2>/dev/null || warn "swapon 失败，可能已经启用"
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --------------------------------------------------------------- 日志限额

log "限制 journald 占用（小磁盘机器上很有必要）"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/99-${APP_NAME}.conf <<'EOF'
[Journal]
SystemMaxUse=200M
SystemKeepFree=200M
EOF
systemctl restart systemd-journald 2>/dev/null || warn "journald 重载失败"

# --------------------------------------------------------------- 应用用户与目录

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  log "创建系统用户 ${APP_USER}"
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

log "同步代码到 ${APP_DIR}"
mkdir -p "$APP_DIR"
rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.pylibs' \
  --exclude '.devtools' \
  --exclude '.piptmp' \
  --exclude '.wheels' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.env' \
  "${SRC_DIR}/" "${APP_DIR}/"

# --------------------------------------------------------------- 虚拟环境

log "创建虚拟环境并安装依赖"
if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/.venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"

# --------------------------------------------------------------- 配置文件

ENV_CREATED=0
if [[ ! -f "${APP_DIR}/.env" ]]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  ENV_CREATED=1
  log "已生成 ${APP_DIR}/.env"
else
  log "${APP_DIR}/.env 已存在，保持不动"
fi

chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
chmod 600 "${APP_DIR}/.env"

# --------------------------------------------------------------- systemd

log "安装 systemd 服务"
install -m 0644 "${APP_DIR}/deploy/${APP_NAME}.service" "$UNIT"
systemctl daemon-reload
systemctl enable "$APP_NAME" >/dev/null

# --------------------------------------------------------------- 启动

log "启动服务"
systemctl restart "$APP_NAME"
sleep 2

if systemctl is-active --quiet "$APP_NAME"; then
  log "服务已运行"
else
  warn "服务没有起来，最近日志："
  journalctl -u "$APP_NAME" -n 30 --no-pager || true
fi

# --------------------------------------------------------------- 收尾提示

PORT=$(grep -E '^PORT=' "${APP_DIR}/.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]' || true)
PORT="${PORT:-80}"
[[ "$PORT" == "80" ]] && URL_SUFFIX="" || URL_SUFFIX=":${PORT}"

cat <<EOF

────────────────────────────────────────────────────────────────
 部署完成。接下来只有两件事：

 1. 放行端口（必做）

    在阿里云控制台的「安全组」里放行 TCP ${PORT}。
    服务器上跑着但外面打不开，九成是安全组没放行。

 2. 用手机 4G 访问验证（不要用 WiFi，出口 IP 可能是运营商机房）

      http://<你的公网IP>${URL_SUFFIX}/

 默认数据源是中央气象台公开接口，不需要任何 Key，做完上面两步就能用。

 想改用高德（可选）：
     sudo nano ${APP_DIR}/.env      # 填 AMAP_KEY=你的Key
     sudo systemctl restart ${APP_NAME}
   Key 在 https://console.amap.com/dev/key/app 创建，
   服务平台必须选「Web 服务」。填了之后会自动切换（WEATHER_BACKEND=auto）。

 自检：
     python3 ${APP_DIR}/scripts/smoke.py http://127.0.0.1${URL_SUFFIX}/

 常用命令：

    systemctl status ${APP_NAME}          # 状态
    journalctl -u ${APP_NAME} -f          # 实时日志
    systemctl restart ${APP_NAME}         # 重启
    sudo bash ${APP_DIR}/deploy/install.sh     # 重新部署（幂等）

EOF

if [[ "$ENV_CREATED" == "1" ]]; then
  log "${APP_DIR}/.env 是新生成的，默认配置即可直接使用，无需修改。"
fi

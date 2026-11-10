#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

pick_python() {
  if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY' && echo python3 && return 0
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
  fi
  if command -v python >/dev/null 2>&1; then
    python - <<'PY' && echo python && return 0
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
  fi
  return 1
}

PYBIN="$(pick_python || true)"
if [ -z "${PYBIN}" ]; then
  echo "没有找到 Python 3.11 或更高版本。"
  echo "请先安装 Python，然后再双击或运行本脚本。"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "正在创建运行环境（只需第一次）…"
  "$PYBIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "正在检查依赖…"
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

BASE="$(python -c 'from app.portcfg import base_url; print(base_url())')"
echo
echo "=============================================="
echo "  澜音已启动"
echo "  控制台：${BASE}/"
echo "  覆盖层：${BASE}/overlay"
echo "  播放器：${BASE}/player"
echo "  按 Ctrl+C 可以停止"
echo "=============================================="
echo
python -m app

# -*- coding: utf-8 -*-
"""只用标准库读取端口并探测本机服务。

start_win.py、open_console_window.py、bind_to_obs.py 可能在还没装依赖（没有 PyYAML）
的系统 Python 里运行，所以这里不 import yaml，也不 import app 里的其它模块。
"""
from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

# 不走系统代理：国内常开的代理软件可能把 127.0.0.1 也转发出去
_OPENER = build_opener(ProxyHandler({}))


def http_get(url: str, timeout: float = 1.5):
    return _OPENER.open(url, timeout=timeout)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DEFAULT_PORT = 8765
CONSOLE_VERSION = "46"  # 控制台静态资源版本，和 console.html 里的 ?v= 保持一致


def _read_config_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    for enc in ("utf-8-sig", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_port(path: Path | None = None) -> int:
    text = _read_config_text(path or CONFIG_PATH)
    m = re.search(r"^port\s*:\s*['\"]?(\d{2,5})['\"]?\s*(?:#.*)?$", text, flags=re.M)
    if m:
        n = int(m.group(1))
        if 0 < n < 65536:
            return n
    return DEFAULT_PORT


def read_host(path: Path | None = None) -> str:
    text = _read_config_text(path or CONFIG_PATH)
    m = re.search(r"^host\s*:\s*['\"]?([^'\"\s#]+)", text, flags=re.M)
    return m.group(1) if m else "127.0.0.1"


def local_host(host: str | None = None) -> str:
    """浏览器/OBS 用的地址：监听 0.0.0.0 或 :: 时仍连 127.0.0.1。"""
    h = host or read_host()
    return "127.0.0.1" if h in ("0.0.0.0", "::", "", "localhost") else h


def base_url(port: int | None = None, host: str | None = None) -> str:
    return f"http://{local_host(host)}:{port or read_port()}"


def window_url(port: int | None = None) -> str:
    return f"{base_url(port)}/window?v={CONSOLE_VERSION}"


def probe(port: int | None = None, host: str | None = None, timeout: float = 1.5) -> str:
    """返回 "free"（没人监听）、"lanyin"（已有澜音在跑）或 "other"（被别的程序占用）。"""
    port = port or read_port()
    h = local_host(host)
    try:
        with socket.create_connection((h, port), timeout=timeout):
            pass
    except OSError:
        return "free"
    try:
        with http_get(f"http://{h}:{port}/api/health", timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        if isinstance(body, dict) and body.get("ok") and (body.get("app") == "lanyin" or "room_id" in body):
            return "lanyin"
    except Exception:
        pass
    return "other"

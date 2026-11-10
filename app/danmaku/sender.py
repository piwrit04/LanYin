from __future__ import annotations

import time
from typing import Any

import httpx

from app.http_util import DEFAULT_UA
from app.secrets_store import cookie_header, get_platform

SEND_URL = "https://api.live.bilibili.com/msg/send"


class SendError(Exception):
    pass


def is_logged_in() -> bool:
    ck = cookie_header("bilibili")
    return "SESSDATA=" in ck and "bili_jct=" in ck


def logged_in_uid() -> int:
    """当前扫码登录账号的 DedeUserID，未登录则为 0。"""
    cookies = (get_platform("bilibili").get("cookies") or {})
    if isinstance(cookies, dict):
        v = str(cookies.get("DedeUserID") or "").strip()
        if v.isdigit():
            return int(v)
    ck = cookie_header("bilibili")
    for part in ck.split(";"):
        part = part.strip()
        if part.startswith("DedeUserID=") and "DedeUserID__ckMd5" not in part:
            v = part.split("=", 1)[-1].strip()
            if v.isdigit():
                return int(v)
    return 0


def csrf_token() -> str:
    cookies = (get_platform("bilibili").get("cookies") or {})
    if isinstance(cookies, dict):
        return str(cookies.get("bili_jct") or "")
    ck = cookie_header("bilibili")
    for part in ck.split(";"):
        if part.strip().startswith("bili_jct="):
            return part.split("=", 1)[-1].strip()
    return ""


async def send_danmaku(room_id: int, message: str) -> dict[str, Any]:
    """向直播间发送一条短弹幕。必须已登录（SESSDATA + bili_jct）。"""
    if not is_logged_in():
        raise SendError("尚未登录 B 站，无法发送弹幕")
    msg = (message or "").strip()
    if not msg:
        raise SendError("弹幕内容为空")
    if len(msg) > 20:
        msg = msg[:20]
    csrf = csrf_token()
    if not csrf:
        raise SendError("Cookie 中缺少 bili_jct（CSRF），请重新扫码登录")
    data = {
        "bubble": 0,
        "msg": msg,
        "color": 16777215,
        "mode": 1,
        "fontsize": 25,
        "rnd": int(time.time()),
        "roomid": int(room_id),
        "csrf": csrf,
        "csrf_token": csrf,
    }
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": f"https://live.bilibili.com/{room_id}",
        "Origin": "https://live.bilibili.com",
        "Cookie": cookie_header("bilibili"),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.post(SEND_URL, data=data, headers=headers)
        try:
            payload = resp.json()
        except Exception as exc:
            raise SendError(f"发送弹幕失败：HTTP {resp.status_code}") from exc
        if payload.get("code") != 0:
            raise SendError(payload.get("message") or f"发送弹幕失败 code={payload.get('code')}")
        return payload

from __future__ import annotations

import io
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import qrcode

from app.http_util import DEFAULT_UA
from app.secrets_store import get_platform, set_platform

GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# 进行中的扫码会话：key -> httpx cookie jar client（需要同一会话才能拿到 Set-Cookie）
_sessions: dict[str, httpx.AsyncClient] = {}
_session_at: dict[str, float] = {}


def _headers() -> dict[str, str]:
    return {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }


async def start_login() -> dict[str, Any]:
    await _cleanup()
    client = httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=_headers())
    resp = await client.get(GENERATE_URL)
    payload = resp.json()
    if payload.get("code") != 0:
        await client.aclose()
        raise RuntimeError(payload.get("message") or "无法获取登录二维码")
    data = payload.get("data") or {}
    url = data.get("url") or ""
    key = data.get("qrcode_key") or ""
    if not url or not key:
        await client.aclose()
        raise RuntimeError("二维码数据不完整")
    _sessions[key] = client
    _session_at[key] = time.time()
    return {"qrcode_key": key, "url": url, "image": _qr_data_url(url)}


async def poll_login(qrcode_key: str) -> dict[str, Any]:
    client = _sessions.get(qrcode_key)
    if not client:
        return {"status": "expired", "message": "二维码已失效，请刷新"}
    resp = await client.get(POLL_URL, params={"qrcode_key": qrcode_key})
    payload = resp.json()
    data = payload.get("data") or {}
    code = data.get("code")
    if code == 86101:
        return {"status": "pending", "message": "等待扫码"}
    if code == 86090:
        return {"status": "scanned", "message": "已扫码，请在手机上确认"}
    if code == 86038:
        await _drop(qrcode_key)
        return {"status": "expired", "message": "二维码已过期，请刷新"}
    if code == 0:
        cookies = dict(client.cookies)
        # 有些情况下需要再访问一下返回的 url 以写入 cookie
        jump = data.get("url") or ""
        if jump:
            try:
                await client.get(jump)
                cookies = dict(client.cookies)
            except Exception:
                pass
        # 兜底：从 jump URL 解析 DedeUserID 等
        if jump and "DedeUserID" not in cookies:
            try:
                qs = parse_qs(urlparse(jump).query)
                for k in ("DedeUserID", "DedeUserID__ckMd5", "SESSDATA", "bili_jct"):
                    if qs.get(k):
                        cookies[k] = qs[k][0]
            except Exception:
                pass
        if "SESSDATA" not in cookies or "bili_jct" not in cookies:
            await _drop(qrcode_key)
            return {"status": "error", "message": "登录成功但未拿到完整 Cookie，请重试"}
        existing = get_platform("bilibili")
        existing["cookies"] = cookies
        existing["refresh_token"] = data.get("refresh_token") or existing.get("refresh_token")
        existing["logged_in_at"] = time.time()
        set_platform("bilibili", existing)
        uname = cookies.get("DedeUserID") or ""
        await _drop(qrcode_key)
        return {"status": "ok", "message": "登录成功", "uid": uname}
    return {"status": "pending", "message": data.get("message") or "等待扫码"}


async def logout() -> None:
    set_platform("bilibili", None)


async def _drop(key: str) -> None:
    client = _sessions.pop(key, None)
    _session_at.pop(key, None)
    if client:
        await client.aclose()


async def _cleanup() -> None:
    now = time.time()
    expired = [k for k, t in _session_at.items() if now - t > 180]
    for k in expired:
        await _drop(k)


def _qr_data_url(url: str) -> str:
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    import base64

    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"

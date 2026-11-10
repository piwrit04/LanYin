from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from app.http_util import DEFAULT_UA
from app.models import Playable, SearchResult, SourceStatus
from app.secrets_store import cookie_header, get_platform
from app.sources.base import SourceAdapter

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

# 免费试听/预览常见标识，遇到即视为不可完整播放
_PREVIEW_HINTS = ("ts24", "ts12", "preview", "trial", "试听")


class QQAdapter(SourceAdapter):
    id = "qq"
    display_name = "QQ 音乐"
    status = SourceStatus.NEEDS_LOGIN
    note = (
        "可搜索公开曲目。完整播放取决于该账号是否有权收听："
        "免费曲目通常可播；会员/付费曲若接口只给 30 秒试听或空地址，将自动跳过，不会破解。"
        "可在控制台粘贴本机 QQ 音乐 Cookie（可选，能提高可播比例）。"
    )

    def is_logged_in(self) -> bool:
        ck = cookie_header("qq")
        return any(k in ck for k in ("qm_keyst=", "qqmusic_key=", "uin="))

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": DEFAULT_UA,
            "Referer": "https://y.qq.com/",
            "Origin": "https://y.qq.com",
        }
        ck = cookie_header("qq")
        if ck:
            headers["Cookie"] = ck
        return headers

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        params = {
            "w": query,
            "p": 1,
            "n": max(limit, 10),
            "format": "json",
            "cr": 1,
            "g_tk": 5381,
            "t": 0,
            "aggr": 1,
        }
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(SEARCH_URL, params=params, headers=self._headers())
            resp.raise_for_status()
            payload = resp.json()
            songs = (((payload.get("data") or {}).get("song") or {}).get("list")) or []
            out: list[SearchResult] = []
            for item in songs:
                mid = item.get("songmid") or item.get("mid") or ""
                if not mid:
                    continue
                singers = item.get("singer") or []
                artist = " / ".join(s.get("name") or "" for s in singers if s.get("name"))
                title = item.get("songname") or item.get("name") or ""
                duration = int(item.get("interval") or item.get("duration") or 0)
                album = (item.get("albumname") or "") if isinstance(item.get("albumname"), str) else ""
                albummid = item.get("albummid") or ""
                cover = (
                    f"https://y.gtimg.cn/music/photo_new/T002R300x300M000{albummid}.jpg"
                    if albummid
                    else ""
                )
                pay = item.get("pay") or {}
                out.append(
                    SearchResult(
                        source=self.id,
                        source_id=mid,
                        title=title,
                        artist=artist,
                        duration=duration,
                        cover_url=cover,
                        extra={
                            "songmid": mid,
                            "songid": item.get("songid"),
                            "album": album,
                            "payplay": pay.get("payplay") or pay.get("pay_play") or 0,
                            "filename": item.get("strMediaMid") or "",
                        },
                    )
                )
                if len(out) >= limit * 2:
                    break
            return out

    async def resolve_playable(self, result: SearchResult) -> Optional[Playable]:
        mid = result.source_id
        if not mid:
            return None
        uin = _guess_uin()
        body = {
            "req_0": {
                "module": "vkey.GetVkeyServer",
                "method": "CgiGetVkey",
                "param": {
                    "guid": "10000",
                    "songmid": [mid],
                    "songtype": [0],
                    "uin": uin,
                    "loginflag": 1 if uin != "0" else 0,
                    "platform": "20",
                },
            },
            "comm": {"uin": uin, "format": "json", "ct": 24, "cv": 0, "platform": "yqq"},
        }
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.post(
                MUSICU_URL,
                content=json.dumps(body, separators=(",", ":")),
                headers={**self._headers(), "Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                return None
            try:
                payload = resp.json()
            except Exception:
                return None
            info = (
                (((payload.get("req_0") or {}).get("data") or {}).get("midurlinfo") or [{}])[0]
            )
            purl = (info or {}).get("purl") or ""
            sip = (((payload.get("req_0") or {}).get("data") or {}).get("sip") or [])
            if not purl:
                return None
            low = purl.lower()
            if any(h in low for h in _PREVIEW_HINTS):
                return None
            base = sip[0] if sip else "https://isure.stream.qqmusic.qq.com/"
            url = purl if purl.startswith("http") else (base.rstrip("/") + "/" + purl.lstrip("/"))
            # 试听文件通常很短，文件名里也会带试听标记；再根据 filename 检查一次
            filename = (info or {}).get("filename") or ""
            if any(h in filename.lower() for h in _PREVIEW_HINTS):
                return None
            lyric = await self.fetch_lyric(result)
            return Playable(
                result=result,
                url=url,
                headers={"User-Agent": DEFAULT_UA, "Referer": "https://y.qq.com/"},
                is_complete=True,
                lyric=lyric,
                expire_hint_seconds=40 * 60,
            )

    async def fetch_lyric(self, result: SearchResult) -> str:
        mid = result.source_id
        if not mid:
            return ""
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(
                    LYRIC_URL,
                    params={"songmid": mid, "format": "json", "nobase64": 1, "g_tk": 5381},
                    headers=self._headers(),
                )
                if resp.status_code != 200:
                    return ""
                data = resp.json()
                lyric = data.get("lyric") or ""
                return lyric if "[" in lyric else ""
        except Exception:
            return ""


def _guess_uin() -> str:
    entry = get_platform("qq")
    cookies = entry.get("cookies") or {}
    if isinstance(cookies, dict):
        for key in ("uin", "uin32", "wxuin"):
            v = str(cookies.get(key) or "").lstrip("o")
            if v.isdigit() and v != "0":
                return v
    if isinstance(cookies, str) and "uin=" in cookies:
        for part in cookies.split(";"):
            if "uin=" in part and "key" not in part.lower():
                v = part.split("=", 1)[-1].strip().lstrip("o")
                if v.isdigit() and v != "0":
                    return v
    return "0"

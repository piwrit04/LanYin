from __future__ import annotations

import base64
import hashlib
from typing import Any, Optional

import httpx

from app.http_util import DEFAULT_UA
from app.models import Playable, SearchResult, SourceStatus
from app.secrets_store import cookie_header
from app.sources.base import SourceAdapter

SEARCH_URL = "https://music.163.com/api/search/get/web"
SONG_URL = "https://music.163.com/api/song/enhance/player/url"
SONG_URL_V1 = "https://music.163.com/api/song/enhance/player/url/v1"
LYRIC_URL = "https://music.163.com/api/song/lyric"
DETAIL_URL = "https://music.163.com/api/song/detail"


class NeteaseAdapter(SourceAdapter):
    id = "netease"
    display_name = "网易云音乐"
    status = SourceStatus.NEEDS_LOGIN
    note = (
        "可搜索公开曲目。只用官方播放接口返回的地址；若只有 30 秒试听或没有链接就跳过。"
        "未登录时很多曲目拿不到完整地址。可在控制台粘贴网易云网页 Cookie（MUSIC_U）。"
        "不会解密加密音源，也不会走第三方换源。"
    )

    def is_logged_in(self) -> bool:
        return "MUSIC_U=" in cookie_header("netease")

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": DEFAULT_UA,
            "Referer": "https://music.163.com/",
            "Origin": "https://music.163.com",
        }
        ck = cookie_header("netease")
        if ck:
            headers["Cookie"] = ck
        return headers

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.post(
                SEARCH_URL,
                data={"s": query, "type": 1, "offset": 0, "limit": max(limit, 10), "total": True},
                headers=self._headers(),
            )
            resp.raise_for_status()
            payload = resp.json()
            songs = ((payload.get("result") or {}).get("songs")) or []
            out: list[SearchResult] = []
            for item in songs:
                sid = str(item.get("id") or "")
                if not sid:
                    continue
                artists = item.get("artists") or item.get("ar") or []
                artist = " / ".join(
                    (a.get("name") or "") for a in artists if isinstance(a, dict) and a.get("name")
                )
                album = (item.get("album") or item.get("al") or {}) or {}
                cover = _album_cover(album, item)
                duration_ms = int(item.get("duration") or item.get("dt") or 0)
                duration = duration_ms // 1000 if duration_ms > 1000 else duration_ms
                out.append(
                    SearchResult(
                        source=self.id,
                        source_id=sid,
                        title=item.get("name") or "",
                        artist=artist,
                        duration=duration,
                        cover_url=cover,
                        extra={"fee": item.get("fee"), "privilege": item.get("privilege") or {}},
                    )
                )
                if len(out) >= limit * 2:
                    break
            return out

    async def resolve_playable(self, result: SearchResult) -> Optional[Playable]:
        sid = result.source_id
        if not sid:
            return None
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            data = await self._fetch_url(client, sid)
            if not data:
                return None
            if data.get("freeTrialInfo") or data.get("freeTrialPrivilege"):
                # 明确试听
                trial = data.get("freeTrialInfo")
                if trial:
                    return None
            url = (data.get("url") or "").strip()
            if not url:
                return None
            # 官方接口给出的播放时长（毫秒）若明显只有预览长度，则拒绝
            time_ms = int(data.get("time") or data.get("playTime") or 0)
            duration = time_ms // 1000 if time_ms > 1000 else time_ms
            expected = result.duration or 0
            if duration and duration <= 45 and (not expected or expected > 60):
                return None
            if "preview" in url.lower() or "trial" in url.lower():
                return None
            # type == "mp3"/"aac" 等明文流才用；不尝试解密加密格式
            lyric = await self.fetch_lyric(result)
            return Playable(
                result=result,
                url=url,
                headers={"User-Agent": DEFAULT_UA, "Referer": "https://music.163.com/"},
                is_complete=True,
                lyric=lyric,
            )

    async def _fetch_url(self, client: httpx.AsyncClient, sid: str) -> Optional[dict[str, Any]]:
        # 先试 v1，再试旧接口。都是官方公开接口，不改写权限。
        try:
            resp = await client.get(
                SONG_URL_V1,
                params={"id": sid, "level": "standard", "encodeType": "mp3"},
                headers=self._headers(),
            )
            if resp.status_code == 200:
                payload = resp.json()
                arr = payload.get("data") or []
                if arr:
                    return arr[0]
        except Exception:
            pass
        try:
            resp = await client.get(
                SONG_URL,
                params={"id": sid, "ids": f"[{sid}]", "br": 128000},
                headers=self._headers(),
            )
            if resp.status_code == 200:
                payload = resp.json()
                arr = payload.get("data") or []
                if arr:
                    return arr[0]
        except Exception:
            pass
        return None

    async def fetch_lyric(self, result: SearchResult) -> str:
        sid = result.source_id
        if not sid:
            return ""
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(
                    LYRIC_URL,
                    params={"id": sid, "lv": -1, "tv": -1},
                    headers=self._headers(),
                )
                if resp.status_code != 200:
                    return ""
                data = resp.json()
                lyric = (data.get("lrc") or {}).get("lyric") or ""
                return lyric if "[" in lyric else ""
        except Exception:
            return ""

def _https(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def _encrypt_pic_id(pic_id: Any) -> str:
    magic = bytearray(b"3go8&$8*3*3h0k(2)2")
    data = bytearray(str(pic_id), "utf-8")
    for i, b in enumerate(data):
        data[i] = b ^ magic[i % len(magic)]
    out = base64.b64encode(hashlib.md5(data).digest()).decode("ascii")
    return out.replace("/", "_").replace("+", "-")


def _cover_from_pic_id(pic_id: Any) -> str:
    text = str(pic_id or "").strip()
    if not text or text == "0":
        return ""
    return f"https://p1.music.126.net/{_encrypt_pic_id(text)}/{text}.jpg"


def _album_cover(album: dict[str, Any], item: dict[str, Any] | None = None) -> str:
    album = album or {}
    item = item or {}
    cover = (
        album.get("picUrl")
        or album.get("pic_url")
        or album.get("blurPicUrl")
        or item.get("picUrl")
        or ""
    )
    if cover:
        return _https(str(cover))
    pic_id = album.get("picId_str") or album.get("picId") or album.get("pic_id") or album.get("pic") or ""
    return _cover_from_pic_id(pic_id)


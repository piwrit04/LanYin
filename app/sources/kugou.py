from __future__ import annotations

from typing import Any, Optional

import httpx

from app.http_util import DEFAULT_UA
from app.models import Playable, SearchResult, SourceStatus
from app.secrets_store import cookie_header
from app.sources.base import SourceAdapter

SEARCH_URLS = [
    "https://songsearch.kugou.com/song_search_v2",
    "https://mobilecdn.kugou.com/api/v3/search/song",
]
PLAY_URL = "https://wwwapi.kugou.com/yy/index.php"


class KugouAdapter(SourceAdapter):
    id = "kugou"
    display_name = "酷狗概念版"
    status = SourceStatus.NEEDS_LOGIN
    note = (
        "使用酷狗公开搜索接口（概念版与主站曲库相通）。"
        "只有官方接口直接返回完整 play_url 时才播放；空地址或试听会被跳过。"
        "登录后（在控制台粘贴酷狗网页 Cookie）可播的免费曲会更多，不会解锁会员曲。"
    )

    def is_logged_in(self) -> bool:
        ck = cookie_header("kugou")
        return any(k in ck for k in ("KugooID=", "t=", "KuGoo=", "kg_dfid="))

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": DEFAULT_UA,
            "Referer": "https://www.kugou.com/",
        }
        ck = cookie_header("kugou")
        if ck:
            headers["Cookie"] = ck
        return headers

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            # Web 搜索
            try:
                resp = await client.get(
                    SEARCH_URLS[0],
                    params={
                        "keyword": query,
                        "page": 1,
                        "pagesize": max(limit, 10),
                        "userid": 0,
                        "clientver": 20000,
                        "platform": "WebFilter",
                        "filter": 2,
                        "iscorrection": 1,
                        "privilege_filter": 0,
                    },
                    headers=self._headers(),
                )
                resp.raise_for_status()
                payload = resp.json()
                lists = (((payload.get("data") or {}).get("lists")) or [])
                out = [_from_web_item(item) for item in lists]
                out = [x for x in out if x]
                if out:
                    return out[: limit * 2]
            except Exception as exc:
                last_error = exc

            try:
                resp = await client.get(
                    SEARCH_URLS[1],
                    params={
                        "format": "json",
                        "keyword": query,
                        "page": 1,
                        "pagesize": max(limit, 10),
                    },
                    headers=self._headers(),
                )
                resp.raise_for_status()
                payload = resp.json()
                info = ((payload.get("data") or {}).get("info")) or []
                out = [_from_mobile_item(item) for item in info]
                out = [x for x in out if x]
                if out:
                    return out[: limit * 2]
            except Exception as exc:
                last_error = exc

        if last_error:
            raise last_error
        return []

    async def resolve_playable(self, result: SearchResult) -> Optional[Playable]:
        file_hash = (result.extra or {}).get("hash") or result.source_id
        album_id = (result.extra or {}).get("album_id") or 0
        if not file_hash:
            return None
        params = {
            "r": "play/getdata",
            "hash": file_hash,
            "album_id": album_id or "",
            "dfid": "-",
            "mid": "0",
            "platid": 4,
            "_": "0",
        }
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(PLAY_URL, params=params, headers=self._headers())
            if resp.status_code != 200:
                return None
            try:
                payload = resp.json()
            except Exception:
                return None
            data = payload.get("data") or {}
            if isinstance(data, list):
                return None
            url = (data.get("play_url") or data.get("play_backup_url") or "").strip()
            if not url:
                return None
            # 酷狗试听地址常带短时长或 is_free_part
            if data.get("is_free_part") in (1, "1", True):
                return None
            low = url.lower()
            if "t/30" in low or "try/" in low or "preview" in low:
                return None
            duration = int(data.get("timelength") or 0)
            # timelength 有时是毫秒
            if duration > 10000:
                duration = duration // 1000
            expected = result.duration or duration
            if duration and expected and duration <= 45 and expected > 60:
                return None
            if data.get("audio_name"):
                result.title = data.get("audio_name") or result.title
            if data.get("author_name"):
                result.artist = data.get("author_name") or result.artist
            if data.get("img"):
                result.cover_url = data.get("img") or result.cover_url
            if duration:
                result.duration = duration
            lyric = data.get("lyrics") or ""
            return Playable(
                result=result,
                url=url,
                headers={"User-Agent": DEFAULT_UA, "Referer": "https://www.kugou.com/"},
                is_complete=True,
                lyric=lyric if "[" in str(lyric) else "",
            )


def _cover_url(item: dict[str, Any]) -> str:
    raw = (
        item.get("Image")
        or item.get("AlbumImage")
        or item.get("album_sizable_cover")
        or item.get("imgurl")
        or item.get("img")
        or ""
    )
    url = str(raw).strip()
    if not url:
        return ""
    url = url.replace("{size}", "400").replace("{SIZE}", "400")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    return url


def _from_web_item(item: dict[str, Any]) -> SearchResult | None:
    file_hash = item.get("FileHash") or item.get("FileHash".lower()) or ""
    if not file_hash:
        return None
    title = item.get("SongName") or item.get("OriSongName") or ""
    artist = item.get("SingerName") or ""
    duration = int(item.get("Duration") or 0)
    album_id = item.get("AlbumID") or 0
    return SearchResult(
        source="kugou",
        source_id=file_hash,
        title=_clean_em(title),
        artist=_clean_em(artist),
        duration=duration,
        cover_url=_cover_url(item),
        extra={"hash": file_hash, "album_id": album_id},
    )


def _from_mobile_item(item: dict[str, Any]) -> SearchResult | None:
    file_hash = item.get("hash") or item.get("320hash") or ""
    if not file_hash:
        return None
    title = item.get("songname") or item.get("filename") or ""
    artist = item.get("singername") or ""
    duration = int(item.get("duration") or 0)
    return SearchResult(
        source="kugou",
        source_id=file_hash,
        title=title,
        artist=artist,
        duration=duration,
        cover_url=_cover_url(item),
        extra={"hash": file_hash, "album_id": item.get("album_id") or 0},
    )


def _clean_em(text: str) -> str:
    return (
        str(text)
        .replace("<em>", "")
        .replace("</em>", "")
        .replace("<em class=\"keyword\">", "")
        .strip()
    )

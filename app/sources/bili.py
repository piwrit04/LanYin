from __future__ import annotations

import hashlib
import hmac
import re
import time
import urllib.parse
import uuid
from typing import Any, Optional

import httpx

from app.http_util import DEFAULT_UA
from app.models import Playable, SearchResult, SourceStatus
from app.secrets_store import cookie_header
from app.sources.base import SourceAdapter

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
SEARCH_URL_PLAIN = "https://api.bilibili.com/x/web-interface/search/type"
SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
TICKET_URL = "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
VIEW_URL = "https://api.bilibili.com/x/web-interface/wbi/view"
VIEW_URL_PLAIN = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_URL = "https://api.bilibili.com/x/player/wbi/playurl"
PLAYURL_LEGACY = "https://api.bilibili.com/x/player/playurl"

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

BILI_REFERER = "https://www.bilibili.com"


class BiliAdapter(SourceAdapter):
    id = "bili"
    display_name = "哔哩哔哩"
    status = SourceStatus.AVAILABLE
    note = "使用官方公开搜索、视频信息和音轨地址，无需会员。优先选官方/原唱向的视频音轨。"

    def __init__(self) -> None:
        self._mixin_key = ""
        self._mixin_at = 0.0
        self._buvid = _make_buvid3()
        self._session_cookies: dict[str, str] = {}
        self._session_until = 0.0

    def is_logged_in(self) -> bool:
        return "SESSDATA=" in cookie_header("bilibili")

    def _cookie_string(self) -> str:
        parts: dict[str, str] = {}
        for k, v in self._session_cookies.items():
            if v:
                parts[k] = v
        user = cookie_header("bilibili")
        if user:
            for part in user.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    parts[k.strip()] = v.strip()
        if "buvid3" not in parts:
            parts["buvid3"] = self._buvid
        return "; ".join(f"{k}={v}" for k, v in parts.items() if v)

    def _headers(self, referer: str = BILI_REFERER) -> dict[str, str]:
        return {
            "User-Agent": DEFAULT_UA,
            "Referer": referer,
            "Origin": "https://www.bilibili.com",
            "Cookie": self._cookie_string(),
        }

    async def _ensure_session(self, client: httpx.AsyncClient) -> None:
        if self._session_cookies and time.time() < self._session_until - 60:
            return
        try:
            spi = (await client.get(SPI_URL, headers=self._headers())).json()
            data = spi.get("data") or {}
            if data.get("b_3"):
                self._session_cookies["buvid3"] = data["b_3"]
                self._buvid = data["b_3"]
            if data.get("b_4"):
                self._session_cookies["buvid4"] = data["b_4"]
        except Exception:
            self._session_cookies.setdefault("buvid3", self._buvid)
        ts = int(time.time())
        self._session_cookies["b_nut"] = str(ts)
        try:
            hexsign = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode("utf-8"), hashlib.sha256).hexdigest()
            resp = await client.post(
                TICKET_URL,
                params={"key_id": "ec02", "hexsign": hexsign, "context[ts]": ts, "csrf": ""},
                headers=self._headers(),
            )
            payload = resp.json().get("data") or {}
            ticket = payload.get("ticket") or ""
            if ticket:
                self._session_cookies["bili_ticket"] = ticket
                ttl = int(payload.get("ttl") or 3 * 24 * 3600)
                created = int(payload.get("created_at") or ts)
                self._session_cookies["bili_ticket_expires"] = str(created + ttl)
                self._session_until = created + ttl
            else:
                self._session_until = time.time() + 300
        except Exception:
            self._session_until = time.time() + 120

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=self._headers())

    async def _ensure_mixin(self, client: httpx.AsyncClient) -> str:
        now = time.time()
        if self._mixin_key and now - self._mixin_at < 600:
            return self._mixin_key
        resp = await client.get(NAV_URL, headers=self._headers())
        data = resp.json().get("data") or {}
        wbi = data.get("wbi_img") or {}
        img = _filename_key(wbi.get("img_url") or "")
        sub = _filename_key(wbi.get("sub_url") or "")
        raw = img + sub
        if not raw:
            raise RuntimeError("无法获取哔哩哔哩 WBI 签名参数")
        mixed = "".join(raw[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw))[:32]
        self._mixin_key = mixed
        self._mixin_at = now
        return mixed

    async def _signed(self, client: httpx.AsyncClient, params: dict[str, Any]) -> dict[str, Any]:
        mixin = await self._ensure_mixin(client)
        signed = dict(params)
        signed["wts"] = int(time.time())
        filtered = {}
        for k in sorted(signed.keys()):
            v = signed[k]
            s = str(v)
            s = re.sub(r"[!'()*]", "", s)
            filtered[k] = s
        query = urllib.parse.urlencode(filtered)
        filtered["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
        return filtered

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        async with await self._client() as client:
            await self._ensure_session(client)
            payload = await self._search_payload(client, query, limit)
            if payload.get("code") not in (0, None):
                raise RuntimeError(f"哔哩哔哩搜索失败: {payload.get('message') or payload.get('code')}")
            results = ((payload.get("data") or {}).get("result")) or []
            out: list[SearchResult] = []
            for item in results:
                if item.get("type") not in ("video", None) and "bvid" not in item:
                    continue
                bvid = item.get("bvid") or ""
                if not bvid:
                    continue
                title = _strip_em(item.get("title") or "")
                author = item.get("author") or item.get("typename") or ""
                duration = _parse_duration(item.get("duration") or item.get("time") or 0)
                cover = item.get("pic") or ""
                if cover.startswith("//"):
                    cover = "https:" + cover
                out.append(
                    SearchResult(
                        source=self.id,
                        source_id=bvid,
                        title=title,
                        artist=author,
                        duration=duration,
                        cover_url=cover,
                        extra={
                            "aid": item.get("aid"),
                            "bvid": bvid,
                            "typename": item.get("typename") or "",
                            "description": _strip_em(item.get("description") or ""),
                        },
                    )
                )
                if len(out) >= limit * 2:
                    break
            return out

    async def _search_payload(self, client: httpx.AsyncClient, query: str, limit: int) -> dict:
        headers = self._headers("https://search.bilibili.com")
        params_plain = {
            "search_type": "video",
            "keyword": query,
            "page": 1,
            "page_size": max(limit, 20),
            "order": "totalrank",
        }
        # 先走不带 WBI 的官方搜索（配合 bili_ticket 更稳定），失败再试 WBI。
        try:
            resp = await client.get(SEARCH_URL_PLAIN, params=params_plain, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data") or {}
            if payload.get("code") in (0, None) and data.get("result"):
                return payload
        except Exception:
            payload = {}
        params = await self._signed(
            client,
            {
                "search_type": "video",
                "keyword": query,
                "page": 1,
                "page_size": max(limit, 20),
                "order": "totalrank",
                "platform": "pc",
            },
        )
        resp = await client.get(SEARCH_URL, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def resolve_playable(self, result: SearchResult) -> Optional[Playable]:
        bvid = result.source_id or (result.extra or {}).get("bvid")
        if not bvid:
            return None
        async with await self._client() as client:
            await self._ensure_session(client)
            view = await self._get_view(client, bvid)
            if not view:
                return None
            want_cid = 0
            try:
                want_cid = int((result.extra or {}).get("cid") or 0)
            except (TypeError, ValueError):
                want_cid = 0
            pages = view.get("pages") or []
            cid = want_cid or ((pages[0] if pages else {}) or {}).get("cid") or view.get("cid")
            if not cid:
                return None
            duration = int(view.get("duration") or result.duration or 0)
            title = result.title or view.get("title")
            if want_cid:
                for pg in pages:
                    try:
                        if int(pg.get("cid") or 0) == int(cid):
                            if pg.get("duration"):
                                duration = int(pg.get("duration") or duration)
                            part = str(pg.get("part") or "").strip()
                            if part and part not in str(title or ""):
                                base = view.get("title") or title
                                title = f"{base} - {part}" if base else part
                            break
                    except (TypeError, ValueError):
                        continue
            owner = result.artist or (view.get("owner") or {}).get("name") or ""
            cover = result.cover_url or view.get("pic")
            audio_url = await self._get_audio_url(client, bvid, int(cid))
            if not audio_url:
                return None
            result.title = title
            result.artist = owner
            result.duration = duration
            result.cover_url = cover
            result.extra = {**(result.extra or {}), "cid": int(cid), "aid": view.get("aid")}
            return Playable(
                result=result,
                url=audio_url,
                headers={
                    "User-Agent": DEFAULT_UA,
                    "Referer": BILI_REFERER,
                    "Origin": "https://www.bilibili.com",
                },
                is_complete=True,
                expire_hint_seconds=100 * 60,
            )

    async def from_bvid(self, bvid: str) -> Optional[SearchResult]:
        """按 BV 号取视频标题、UP 主和封面，供默认歌单预览。"""
        return await self.from_video(bvid=bvid)

    async def from_video(self, *, bvid: str = "", aid: int | None = None) -> Optional[SearchResult]:
        bvid = (bvid or "").strip()
        aid_i = int(aid) if aid else 0
        if not bvid and not aid_i:
            return None
        async with await self._client() as client:
            await self._ensure_session(client)
            view = await self._get_view(client, bvid=bvid, aid=aid_i or None)
            return self._result_from_view(view, bvid=bvid)

    async def list_collection_episodes(self, result: SearchResult) -> list[dict[str, Any]]:
        """合集或分 P 的全部视频，按官方顺序（含当前这条之前的集）。不是合集时返回空列表。"""
        return await self._list_season_episodes(result, after_current=False)

    async def list_follow_episodes(self, result: SearchResult) -> list[dict[str, Any]]:
        """当前视频之后、同一合集或分 P 里还要续播的条目（不含当前这条）。"""
        return await self._list_season_episodes(result, after_current=True)

    async def _list_season_episodes(self, result: SearchResult, *, after_current: bool) -> list[dict[str, Any]]:
        bvid = (result.source_id or (result.extra or {}).get("bvid") or "").strip()
        if not bvid:
            return []
        cached = (result.extra or {}).get("_collection_episodes")
        if isinstance(cached, list) and cached:
            eps = list(cached)
        else:
            try:
                start_cid = int((result.extra or {}).get("cid") or 0)
            except (TypeError, ValueError):
                start_cid = 0
            async with await self._client() as client:
                await self._ensure_session(client)
                view = await self._get_view(client, bvid)
                if not view:
                    return []
                eps = episodes_from_view(view, start_bvid=bvid, start_cid=start_cid)
        if not eps:
            return []
        if not after_current:
            return eps
        try:
            start_cid = int((result.extra or {}).get("cid") or 0)
        except (TypeError, ValueError):
            start_cid = 0
        idx = episode_start_index(eps, bvid=bvid, cid=start_cid)
        return eps[idx + 1:]

    def _result_from_view(self, view: Optional[dict], bvid: str = "") -> Optional[SearchResult]:
        if not view:
            return None
        bvid = bvid or str(view.get("bvid") or "")
        if not bvid and view.get("aid"):
            # 仍可用 aid 作为 source_id 的兜底，优先 bvid
            bvid = str(view.get("bvid") or "")
        source_id = bvid or (f"av{view.get('aid')}" if view.get("aid") else "")
        if not source_id:
            return None
        title = view.get("title") or ""
        owner = (view.get("owner") or {}).get("name") or ""
        cover = view.get("pic") or ""
        if cover.startswith("//"):
            cover = "https:" + cover
        duration = int(view.get("duration") or 0)
        extra: dict[str, Any] = {"aid": view.get("aid"), "bvid": bvid or view.get("bvid") or ""}
        eps = episodes_from_view(view, start_bvid=bvid or str(view.get("bvid") or ""))
        if eps:
            idx = episode_start_index(eps, bvid=bvid or str(view.get("bvid") or ""))
            cur_ep = eps[idx]
            extra["season_title"] = cur_ep.get("season_title") or ""
            extra["season_index"] = cur_ep.get("season_index")
            extra["season_total"] = cur_ep.get("season_total")
            if cur_ep.get("cid"):
                extra["cid"] = cur_ep["cid"]
            extra["_collection_episodes"] = eps
        return SearchResult(
            source=self.id,
            source_id=bvid or source_id,
            title=title,
            artist=owner,
            duration=duration,
            cover_url=cover,
            extra=extra,
        )

    async def _get_view(
        self,
        client: httpx.AsyncClient,
        bvid: str = "",
        aid: int | None = None,
    ) -> Optional[dict]:
        params: dict[str, Any] = {}
        if bvid:
            params["bvid"] = bvid
        elif aid:
            params["aid"] = int(aid)
        else:
            return None
        resp = await client.get(VIEW_URL_PLAIN, params=params, headers=self._headers())
        payload = resp.json()
        if payload.get("code") == 0:
            return payload.get("data") or {}
        try:
            signed = await self._signed(client, dict(params))
            resp = await client.get(VIEW_URL, params=signed, headers=self._headers())
            payload = resp.json()
            if payload.get("code") == 0:
                return payload.get("data") or {}
        except Exception:
            pass
        resp = await client.get(
            VIEW_URL_PLAIN,
            params=params,
            headers=self._headers(),
        )
        payload = resp.json()
        if payload.get("code") == 0:
            return payload.get("data") or {}
        return None

    async def _get_audio_url(self, client: httpx.AsyncClient, bvid: str, cid: int) -> str:
        params_raw = {
            "bvid": bvid,
            "cid": cid,
            "fnval": 16,
            "fnver": 0,
            "fourk": 1,
            "qn": 0,
        }
        for url, use_sign in ((PLAYURL_URL, True), (PLAYURL_LEGACY, False)):
            try:
                params = await self._signed(client, params_raw) if use_sign else params_raw
                resp = await client.get(url, params=params, headers=self._headers())
                payload = resp.json()
                if payload.get("code") != 0:
                    continue
                data = payload.get("data") or payload.get("result") or {}
                audio = ((data.get("dash") or {}).get("audio")) or []
                if audio:
                    audio_sorted = sorted(
                        audio, key=lambda a: a.get("bandwidth") or a.get("id") or 0, reverse=True
                    )
                    u = audio_sorted[0].get("baseUrl") or audio_sorted[0].get("base_url") or ""
                    if u:
                        return u
                # 无 DASH 时不要用带画面的 durl 当音频（那是视频流）
            except Exception:
                continue
        return ""


def _filename_key(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    return name.split(".")[0]


def _strip_em(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").replace("&amp;", "&").strip()



def episode_start_index(episodes: list[dict[str, Any]], *, bvid: str = "", cid: int = 0) -> int:
    """在官方顺序列表里定位当前这一集（0-based）。找不到时返回 0。"""
    if not episodes:
        return 0
    key = (bvid or "").strip()
    try:
        cid_i = int(cid or 0)
    except (TypeError, ValueError):
        cid_i = 0
    if cid_i:
        for i, ep in enumerate(episodes):
            try:
                same_cid = int(ep.get("cid") or 0) == cid_i
            except (TypeError, ValueError):
                same_cid = False
            if same_cid and (not key or str(ep.get("bvid") or "").strip() == key):
                return i
    if key:
        for i, ep in enumerate(episodes):
            if str(ep.get("bvid") or "").strip() == key:
                return i
    return 0


def collection_play_order(
    episodes: list[dict[str, Any]], start_index: int, *, loop: bool = False
) -> list[dict[str, Any]]:
    """从加入的那一集起的播放顺序；开启循环时把前面的集接到后面。"""
    if not episodes:
        return []
    n = len(episodes)
    start = max(0, min(int(start_index or 0), n - 1))
    later = episodes[start:]
    earlier = episodes[:start]
    return later + earlier if loop else later


def episodes_from_view(view: dict, *, start_bvid: str = "", start_cid: int = 0) -> list[dict[str, Any]]:
    """列出合集或分 P 的全部条目，按官方顺序（含当前这一集之前的集）。

    start_bvid / start_cid 仅用于兼容旧调用，不再截断列表。
    每条包含 bvid/aid/cid/title/artist/cover_url/duration/season_title/season_index/season_total。
    不是合集也不是多分 P 时返回空列表。
    """
    if not view:
        return []
    owner = ((view.get("owner") or {}).get("name") or "").strip()
    cover = str(view.get("pic") or "")
    if cover.startswith("//"):
        cover = "https:" + cover
    cur_bvid = (start_bvid or str(view.get("bvid") or "")).strip()

    collected: list[dict[str, Any]] = []
    season = view.get("ugc_season") or {}
    season_title = str(season.get("title") or "").strip()
    for sec in season.get("sections") or []:
        for ep in sec.get("episodes") or []:
            if not isinstance(ep, dict):
                continue
            arc = ep.get("arc") if isinstance(ep.get("arc"), dict) else {}
            bvid = str(ep.get("bvid") or arc.get("bvid") or "").strip()
            if not bvid:
                continue
            aid = ep.get("aid") or arc.get("aid") or 0
            try:
                aid = int(aid) if aid else 0
            except (TypeError, ValueError):
                aid = 0
            try:
                cid = int(ep.get("cid") or 0)
            except (TypeError, ValueError):
                cid = 0
            title = str(ep.get("title") or arc.get("title") or "").strip()
            pic = str(arc.get("pic") or ep.get("cover") or cover or "")
            if pic.startswith("//"):
                pic = "https:" + pic
            try:
                duration = int(arc.get("duration") or ep.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0
            author = owner
            if isinstance(arc.get("author"), dict):
                author = str(arc["author"].get("name") or author)
            collected.append({
                "bvid": bvid,
                "aid": aid,
                "cid": cid,
                "title": title,
                "artist": author,
                "cover_url": pic,
                "duration": duration,
                "season_title": season_title,
            })
    if len(collected) >= 2:
        total = len(collected)
        out = []
        for i, ep in enumerate(collected):
            item = dict(ep)
            item["season_index"] = i + 1
            item["season_total"] = total
            out.append(item)
        return out

    pages = view.get("pages") or []
    if len(pages) >= 2:
        bvid = cur_bvid or str(view.get("bvid") or "")
        title0 = str(view.get("title") or "").strip()
        out = []
        for i, pg in enumerate(pages):
            try:
                cid = int(pg.get("cid") or 0)
            except (TypeError, ValueError):
                cid = 0
            part = str(pg.get("part") or "").strip()
            title = title0
            if part and part != title0:
                title = f"{title0} - {part}" if title0 else part
            try:
                duration = int(pg.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0
            out.append({
                "bvid": bvid,
                "aid": int(view.get("aid") or 0) or 0,
                "cid": cid,
                "title": title,
                "artist": owner,
                "cover_url": cover,
                "duration": duration,
                "season_title": title0,
                "season_index": i + 1,
                "season_total": len(pages),
            })
        return out
    return []


def _parse_duration(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    total = 0
    for n in nums:
        total = total * 60 + n
    return total


def _make_buvid3() -> str:
    u = uuid.uuid4().hex.upper()
    return f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:32]}infoc"

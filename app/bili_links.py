from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.http_util import DEFAULT_UA

# BV 号：BV + 10 位（字母数字，常见为 12 个字符总长）
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"(?:^|[^A-Za-z0-9])av(\d+)", re.IGNORECASE)
AID_QUERY_RE = re.compile(r"(?:[?&]aid=)(\d+)", re.IGNORECASE)
B23_RE = re.compile(r"https?://(?:www\.)?b23\.tv/[A-Za-z0-9_-]+", re.IGNORECASE)
BILI_HOSTS = ("bilibili.com", "b23.tv", "bili2233.cn")


def parse_bili_input(text: str) -> dict[str, Any]:
    """识别歌名或哔哩哔哩视频引用。

    返回：
      {"kind": "query", "query": "..."}
      {"kind": "bvid", "bvid": "BVxxxx"}
      {"kind": "aid", "aid": 123}
      {"kind": "short", "url": "https://b23.tv/..."}
    """
    raw = (text or "").strip()
    if not raw:
        return {"kind": "query", "query": ""}

    short = _extract_short(raw)
    if short:
        return {"kind": "short", "url": short}

    bvid = _extract_bvid(raw)
    if bvid:
        return {"kind": "bvid", "bvid": bvid}

    aid = _extract_aid(raw)
    if aid:
        return {"kind": "aid", "aid": aid}

    return {"kind": "query", "query": raw}


def is_bili_ref(text: str) -> bool:
    parsed = parse_bili_input(text)
    return parsed.get("kind") in {"bvid", "aid", "short"}


def _extract_short(text: str) -> str:
    m = B23_RE.search(text)
    if m:
        return m.group(0)
    # 无协议的 b23.tv/xxxxx
    m = re.search(r"(?:^|://|\s)((?:www\.)?b23\.tv/[A-Za-z0-9_-]+)", text, re.I)
    if m:
        return "https://" + m.group(1).lstrip("/")
    return ""


def _extract_bvid(text: str) -> str:
    m = BV_RE.search(text)
    return m.group(1) if m else ""


def _extract_aid(text: str) -> int:
    # /video/av12345 或独立 av12345 或 ?aid=12345
    m = AV_RE.search(text if re.match(r"(?i)av\d+", text) else f" {text}")
    # 对纯数字不要误判为 av；只接受显式 av / aid / /video/av
    lowered = text.lower()
    if "av" in lowered or "aid=" in lowered:
        m_av = AV_RE.search(" " + text)
        if m_av:
            try:
                return int(m_av.group(1))
            except ValueError:
                pass
        m_aid = AID_QUERY_RE.search(text)
        if m_aid:
            try:
                return int(m_aid.group(1))
            except ValueError:
                pass
        # 路径 /video/123456 少见，忽略
    return 0


async def follow_short_url(url: str) -> str:
    """跟随 b23.tv 短链重定向，返回最终地址。失败时返回原 URL。"""
    target = (url or "").strip()
    if not target:
        return ""
    if not target.startswith("http"):
        target = "https://" + target.lstrip("/")
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_UA, "Referer": "https://www.bilibili.com/"},
        ) as client:
            resp = await client.get(target)
            return str(resp.url) or target
    except Exception:
        return target


async def resolve_bili_ref(text: str) -> Optional[dict[str, Any]]:
    """把输入解析成 {bvid} 或 {aid}；短链会跟随跳转。歌名返回 None。"""
    parsed = parse_bili_input(text)
    kind = parsed.get("kind")
    if kind == "query":
        return None
    if kind == "short":
        final = await follow_short_url(parsed["url"])
        again = parse_bili_input(final)
        if again.get("kind") == "bvid":
            return {"bvid": again["bvid"], "url": final}
        if again.get("kind") == "aid":
            return {"aid": again["aid"], "url": final}
        return None
    if kind == "bvid":
        return {"bvid": parsed["bvid"]}
    if kind == "aid":
        return {"aid": parsed["aid"]}
    return None


def looks_like_url(text: str) -> bool:
    raw = (text or "").strip().lower()
    if raw.startswith("http://") or raw.startswith("https://"):
        return True
    host = urlparse("https://" + raw if "://" not in raw else raw).hostname or ""
    return any(host == h or host.endswith("." + h) for h in BILI_HOSTS)

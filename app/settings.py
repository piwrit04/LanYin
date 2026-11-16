from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml

from app.queue_rules import (
    DEFAULT_THEME,
    normalize_blacklist,
    normalize_priority_queue,
    normalize_theme,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
DATA_DIR = ROOT / "data"
SECRETS_PATH = DATA_DIR / "secrets.json"
PLAYBACK_PATH = DATA_DIR / "playback.json"

DEFAULT_MESSAGES: dict[str, str] = {
    "not_found": "未找到可播原曲",
    "not_found_platform": "{platform}未找到可播",
    "please_choose": "请回{nums}选歌",
    "selection_timeout": "选歌超时已取消",
    "choice_unplayable": "选中曲无法播放",
    "blacklisted_user": "你已被拉黑",
    "blacklisted_song": "这首歌已拉黑",
}

DEFAULT_OVERLAY: dict[str, Any] = {
    "top": 77.6,
    "right": 71.1,
    "left": 6.7,
    "bottom": 5.0,
    "width": 22.2,
    "height": 17.4,
    "show_lyrics": True,
}

DEFAULTS: dict[str, Any] = {
    "room_id": 1506613,
    "host": "127.0.0.1",
    "port": 8765,
    "sources_priority": ["qq", "qishui", "kugou", "bili", "netease"],
    "selection_timeout_seconds": 30,
    "selection": {
        "timeout_action": "skip",
    },
    "anti_spam": {
        "max_pending_per_user": 2,
        "max_queue_size": 30,
        "cooldown_seconds": 60,
    },
    "commands": {
        "request_prefix": "点歌",
        "cancel": "撤回",
    },
    "welcome": {
        "enabled": True,
        "extra_templates": [],
    },
    "idle_playlist": {
        "enabled": False,
        "loop": True,
        "resume_delay_seconds": 5,
        "items": [],
    },
    "overlay": dict(DEFAULT_OVERLAY),
    "messages": dict(DEFAULT_MESSAGES),
    "console_theme": DEFAULT_THEME,
    "priority_queue": {"enabled": True},
    "blacklist": {"users": [], "songs": []},
}

VALID_SOURCES = ["qq", "qishui", "kugou", "bili", "netease"]
VALID_TIMEOUT_ACTIONS = ("play_first", "skip")


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:  # NaN
        return default
    return max(lo, min(hi, n))


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _normalize_idle_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()
    if kind not in {"query", "bili"}:
        if str(raw.get("bvid") or "").strip() or raw.get("aid"):
            kind = "bili"
        elif str(raw.get("query") or "").strip():
            kind = "query"
        else:
            return None
    item: dict[str, Any] = {
        "id": str(raw.get("id") or uuid.uuid4().hex[:10]),
        "kind": kind,
    }
    if kind == "bili":
        bvid = str(raw.get("bvid") or "").strip()
        aid = raw.get("aid") or 0
        try:
            aid = int(aid) if aid else 0
        except (TypeError, ValueError):
            aid = 0
        if not bvid and not aid:
            return None
        if bvid:
            item["bvid"] = bvid
        if aid:
            item["aid"] = aid
        item["title"] = str(raw.get("title") or bvid or f"av{aid}")
        item["artist"] = str(raw.get("artist") or "")
        item["cover_url"] = str(raw.get("cover_url") or "")
        item["url"] = str(raw.get("url") or (f"https://www.bilibili.com/video/{bvid}" if bvid else ""))
        try:
            item["duration"] = int(raw.get("duration") or 0)
        except (TypeError, ValueError):
            item["duration"] = 0
        for key in ("cid", "season_title", "season_index", "season_total"):
            if raw.get(key) in (None, ""):
                continue
            item[key] = raw[key]
    else:
        query = str(raw.get("query") or raw.get("title") or "").strip()
        if not query:
            return None
        item["query"] = query
        item["title"] = str(raw.get("title") or query)
        item["artist"] = str(raw.get("artist") or "")
        item["cover_url"] = str(raw.get("cover_url") or "")
    return item


def normalize_idle_items(items: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in items or []:
        item = _normalize_idle_item(raw)
        if item:
            out.append(item)
    return out


def _normalize_welcome(cfg: dict[str, Any]) -> dict[str, Any]:
    welcome = cfg.get("welcome") if isinstance(cfg.get("welcome"), dict) else {}
    extras: list[str] = []
    raw_extras = welcome.get("extra_templates") or []
    if isinstance(raw_extras, str):
        raw_extras = raw_extras.splitlines()
    if isinstance(raw_extras, list):
        for line in raw_extras:
            text = str(line or "").strip()
            if text:
                extras.append(text)
    return {
        "enabled": bool(welcome.get("enabled", True)),
        "extra_templates": extras,
    }


def _normalize_idle_playlist(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("idle_playlist") if isinstance(cfg.get("idle_playlist"), dict) else {}
    try:
        delay = int(raw.get("resume_delay_seconds", 5))
    except (TypeError, ValueError):
        delay = 5
    return {
        "enabled": bool(raw.get("enabled", False)),
        "loop": bool(raw.get("loop", True)),
        "resume_delay_seconds": max(0, min(120, delay)),
        "items": normalize_idle_items(raw.get("items") or []),
    }


def _fit_insets(a: float, b: float, min_size: float) -> tuple[float, float]:
    """Keep two opposite insets inside the canvas, leaving at least min_size."""
    a = max(0.0, min(a, 100.0 - min_size))
    b = max(0.0, min(b, 100.0 - min_size))
    overflow = a + b - (100.0 - min_size)
    if overflow > 0:
        if b >= a:
            b = max(0.0, b - overflow)
        else:
            a = max(0.0, a - overflow)
    return round(a, 1), round(b, 1)


def _normalize_overlay(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("overlay") if isinstance(cfg.get("overlay"), dict) else {}
    show = raw.get("show_lyrics", True)
    if isinstance(show, str):
        show = show.strip().lower() not in {"0", "false", "no", "off"}
    # Prefer 上/右/左/下 when they are consistent with, or instead of, width/height.
    # If both exist but disagree (an old box size was edited), keep left/bottom/width/height
    # so a previously saved card does not jump.
    has_insets = ("top" in raw) or ("right" in raw)
    has_size = ("width" in raw) or ("height" in raw)
    use_insets = has_insets
    if has_insets and has_size:
        left_tmp = _clamp_float(raw.get("left", DEFAULT_OVERLAY["left"]), 0, 100, DEFAULT_OVERLAY["left"])
        right_tmp = _clamp_float(raw.get("right", DEFAULT_OVERLAY["right"]), 0, 100, DEFAULT_OVERLAY["right"])
        top_tmp = _clamp_float(raw.get("top", DEFAULT_OVERLAY["top"]), 0, 100, DEFAULT_OVERLAY["top"])
        bottom_tmp = _clamp_float(raw.get("bottom", DEFAULT_OVERLAY["bottom"]), 0, 100, DEFAULT_OVERLAY["bottom"])
        width_tmp = _clamp_float(raw.get("width", DEFAULT_OVERLAY["width"]), 8, 80, DEFAULT_OVERLAY["width"])
        height_tmp = _clamp_float(raw.get("height", DEFAULT_OVERLAY["height"]), 6, 60, DEFAULT_OVERLAY["height"])
        if abs((left_tmp + right_tmp + width_tmp) - 100) > 0.15 or abs((top_tmp + bottom_tmp + height_tmp) - 100) > 0.15:
            use_insets = False
    if use_insets:
        left = _clamp_float(raw.get("left", DEFAULT_OVERLAY["left"]), 0, 100, DEFAULT_OVERLAY["left"])
        right = _clamp_float(raw.get("right", DEFAULT_OVERLAY["right"]), 0, 100, DEFAULT_OVERLAY["right"])
        top = _clamp_float(raw.get("top", DEFAULT_OVERLAY["top"]), 0, 100, DEFAULT_OVERLAY["top"])
        bottom = _clamp_float(raw.get("bottom", DEFAULT_OVERLAY["bottom"]), 0, 100, DEFAULT_OVERLAY["bottom"])
        left, right = _fit_insets(left, right, 8.0)
        top, bottom = _fit_insets(top, bottom, 6.0)
        width = round(100.0 - left - right, 1)
        height = round(100.0 - top - bottom, 1)
    else:
        left = round(_clamp_float(raw.get("left", DEFAULT_OVERLAY["left"]), 0, 80, DEFAULT_OVERLAY["left"]), 1)
        bottom = round(_clamp_float(raw.get("bottom", DEFAULT_OVERLAY["bottom"]), 0, 80, DEFAULT_OVERLAY["bottom"]), 1)
        width = round(_clamp_float(raw.get("width", DEFAULT_OVERLAY["width"]), 8, 80, DEFAULT_OVERLAY["width"]), 1)
        height = round(_clamp_float(raw.get("height", DEFAULT_OVERLAY["height"]), 6, 60, DEFAULT_OVERLAY["height"]), 1)
        top = round(100.0 - bottom - height, 1)
        right = round(100.0 - left - width, 1)
        if top < 0:
            height = round(height + top, 1)
            top = 0.0
        if right < 0:
            width = round(width + right, 1)
            right = 0.0
    return {
        "top": top,
        "right": right,
        "left": left,
        "bottom": bottom,
        "width": width,
        "height": height,
        "show_lyrics": bool(show),
    }


def _normalize_messages(cfg: dict[str, Any]) -> dict[str, str]:
    raw = cfg.get("messages") if isinstance(cfg.get("messages"), dict) else {}
    out: dict[str, str] = {}
    for key, default in DEFAULT_MESSAGES.items():
        text = str(raw.get(key) if raw.get(key) not in (None, "") else default).strip()
        out[key] = text or default
    return out


def _normalize_selection(cfg: dict[str, Any]) -> dict[str, str]:
    raw = cfg.get("selection") if isinstance(cfg.get("selection"), dict) else {}
    action = str(raw.get("timeout_action") or cfg.get("timeout_action") or "skip").strip().lower()
    if action not in VALID_TIMEOUT_ACTIONS:
        action = "skip"
    return {"timeout_action": action}



def _normalize_theme(cfg: dict[str, Any]) -> str:
    return normalize_theme(cfg.get("console_theme"))


def load_config() -> dict[str, Any]:
    data: dict[str, Any] = {}
    first_run = not CONFIG_PATH.exists()
    if not first_run:
        raw = CONFIG_PATH.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # 旧版记事本按 ANSI（GBK）另存的中文配置
            text = raw.decode("gbk", errors="replace")
        loaded = yaml.safe_load(text) or {}
        data = loaded if isinstance(loaded, dict) else {}
    cfg = _deep_merge(DEFAULTS, data)
    pri = [s for s in cfg.get("sources_priority") or [] if s in VALID_SOURCES]
    for s in VALID_SOURCES:
        if s not in pri:
            pri.append(s)
    cfg["sources_priority"] = pri
    cfg["room_id"] = int(cfg.get("room_id") or DEFAULTS["room_id"])
    cfg["port"] = int(cfg.get("port") or DEFAULTS["port"])
    cfg["selection_timeout_seconds"] = int(cfg.get("selection_timeout_seconds") or 30)
    anti = cfg["anti_spam"]
    anti["max_pending_per_user"] = int(anti.get("max_pending_per_user") or 2)
    anti["max_queue_size"] = int(anti.get("max_queue_size") or 30)
    anti["cooldown_seconds"] = int(anti.get("cooldown_seconds") or 60)
    cfg["welcome"] = _normalize_welcome(cfg)
    cfg["idle_playlist"] = _normalize_idle_playlist(cfg)
    cfg["overlay"] = _normalize_overlay(cfg)
    cfg["messages"] = _normalize_messages(cfg)
    cfg["selection"] = _normalize_selection(cfg)
    cfg["console_theme"] = _normalize_theme(cfg)
    cfg["priority_queue"] = normalize_priority_queue(cfg)
    cfg["blacklist"] = normalize_blacklist(cfg)
    if first_run and not CONFIG_PATH.exists():
        # 第一次运行：写一份默认 config.yaml（只在文件不存在时写，绝不覆盖已有配置）
        try:
            save_config(cfg)
        except Exception:
            pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    # 不把密钥写进 yaml
    clean = {
        "room_id": int(cfg["room_id"]),
        "host": cfg.get("host") or "127.0.0.1",
        "port": int(cfg.get("port") or 8765),
        "sources_priority": list(cfg["sources_priority"]),
        "selection_timeout_seconds": int(cfg["selection_timeout_seconds"]),
        "selection": _normalize_selection(cfg),
        "anti_spam": {
            "max_pending_per_user": int(cfg["anti_spam"]["max_pending_per_user"]),
            "max_queue_size": int(cfg["anti_spam"]["max_queue_size"]),
            "cooldown_seconds": int(cfg["anti_spam"]["cooldown_seconds"]),
        },
        "commands": cfg.get("commands") or DEFAULTS["commands"],
        "welcome": _normalize_welcome(cfg),
        "idle_playlist": _normalize_idle_playlist(cfg),
        "overlay": _normalize_overlay(cfg),
        "messages": _normalize_messages(cfg),
        "console_theme": _normalize_theme(cfg),
        "priority_queue": normalize_priority_queue(cfg),
        "blacklist": normalize_blacklist(cfg),
    }
    CONFIG_PATH.write_text(
        yaml.safe_dump(clean, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "cache").mkdir(exist_ok=True)
    (DATA_DIR / "cookies").mkdir(exist_ok=True)

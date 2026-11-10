"""Blacklist matching and VIP queue insertion helpers."""

from __future__ import annotations

from typing import Any, Iterable


VALID_THEMES = ("plain", "wine", "blush", "inkgold", "celadon")
DEFAULT_THEME = "plain"

# Lower number = higher priority. Regulars sit at 10.
TIER_GOVERNOR = 0  # 总督 guard_level 1
TIER_ADMIRAL = 1  # 提督 guard_level 2
TIER_CAPTAIN = 2  # 舰长 guard_level 3
TIER_FAN_MEDAL = 3
TIER_REGULAR = 10
TIER_IDLE = 20  # 默认歌单 / 合集续播，低于所有点歌

TIER_LABELS = {
    TIER_GOVERNOR: "总督",
    TIER_ADMIRAL: "提督",
    TIER_CAPTAIN: "舰长",
    TIER_FAN_MEDAL: "粉丝牌",
}


def normalize_theme(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in VALID_THEMES else DEFAULT_THEME


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_user_entry(raw: Any) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        uid = int(raw)
        if uid <= 0:
            return None
        return {"uid": uid, "name": ""}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            uid = int(text)
            return {"uid": uid, "name": ""} if uid > 0 else None
        return {"uid": 0, "name": text}
    if isinstance(raw, dict):
        uid = _as_int(raw.get("uid") or 0, 0)
        name = str(raw.get("name") or raw.get("uname") or "").strip()
        if uid <= 0 and not name:
            return None
        return {"uid": max(0, uid), "name": name}
    return None


def normalize_song_entry(raw: Any) -> str | None:
    text = str(raw or "").strip()
    return text or None


def normalize_blacklist(cfg: dict[str, Any] | None = None, raw: Any = None) -> dict[str, Any]:
    src = raw
    if src is None:
        src = (cfg or {}).get("blacklist") if isinstance((cfg or {}).get("blacklist"), dict) else {}
    if not isinstance(src, dict):
        src = {}
    users: list[dict[str, Any]] = []
    seen_users: set[tuple[int, str]] = set()
    for item in src.get("users") or []:
        entry = normalize_user_entry(item)
        if not entry:
            continue
        key = (entry["uid"], entry["name"].casefold())
        if key in seen_users:
            continue
        seen_users.add(key)
        users.append(entry)
    songs: list[str] = []
    seen_songs: set[str] = set()
    for item in src.get("songs") or []:
        entry = normalize_song_entry(item)
        if not entry:
            continue
        folded = entry.casefold()
        if folded in seen_songs:
            continue
        seen_songs.add(folded)
        songs.append(entry)
    return {"users": users, "songs": songs}


def normalize_priority_queue(cfg: dict[str, Any] | None = None, raw: Any = None) -> dict[str, Any]:
    src = raw
    if src is None:
        src = (cfg or {}).get("priority_queue") if isinstance((cfg or {}).get("priority_queue"), dict) else {}
    if not isinstance(src, dict):
        src = {}
    enabled = src.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in {"0", "false", "no", "off"}
    return {"enabled": bool(enabled)}


def user_is_blocked(blacklist: dict[str, Any] | None, uid: int, name: str) -> bool:
    users = (blacklist or {}).get("users") or []
    try:
        uid = int(uid or 0)
    except (TypeError, ValueError):
        uid = 0
    name_key = str(name or "").strip().casefold()
    for entry in users:
        if not isinstance(entry, dict):
            parsed = normalize_user_entry(entry)
            if not parsed:
                continue
            entry = parsed
        entry_uid = _as_int(entry.get("uid") or 0, 0)
        entry_name = str(entry.get("name") or "").strip().casefold()
        if entry_uid and uid and entry_uid == uid:
            return True
        if entry_name and name_key and entry_name == name_key:
            return True
    return False


def song_is_blocked(blacklist: dict[str, Any] | None, *texts: Any) -> bool:
    keywords = (blacklist or {}).get("songs") or []
    haystack = " ".join(str(t or "").strip() for t in texts if t).casefold()
    if not haystack:
        return False
    for raw in keywords:
        word = str(raw or "").strip().casefold()
        if word and word in haystack:
            return True
    return False


def priority_tier(
    guard_level: int = 0,
    medal_room: int = 0,
    room_ids: Iterable[int] | None = None,
    enabled: bool = True,
) -> int:
    if not enabled:
        return TIER_REGULAR
    level = _as_int(guard_level, 0)
    if level == 1:
        return TIER_GOVERNOR
    if level == 2:
        return TIER_ADMIRAL
    if level == 3:
        return TIER_CAPTAIN
    rooms = {int(r) for r in (room_ids or []) if r}
    medal = _as_int(medal_room, 0)
    if medal and medal in rooms:
        return TIER_FAN_MEDAL
    return TIER_REGULAR


def insert_index(existing_tiers: list[int], new_tier: int) -> int:
    """Insert before the first worse (higher) tier so FIFO is kept within a tier."""
    for i, tier in enumerate(existing_tiers):
        if int(tier) > int(new_tier):
            return i
    return len(existing_tiers)


def tier_label(tier: int) -> str:
    return TIER_LABELS.get(int(tier), "")

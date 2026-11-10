from __future__ import annotations

import json
from typing import Any

from app.settings import DATA_DIR, SECRETS_PATH, ensure_dirs


def load_secrets() -> dict[str, Any]:
    ensure_dirs()
    if not SECRETS_PATH.exists():
        return {}
    try:
        # utf-8-sig：用记事本另存过的文件可能带 BOM，普通 utf-8 读会解析失败
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8-sig")) or {}
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 读不懂时先留一份备份，避免下一次保存把原文件直接覆盖掉
        _backup_unreadable()
        return {}


def _backup_unreadable() -> None:
    import time

    try:
        bak = SECRETS_PATH.with_name(f"secrets.unreadable-{time.strftime('%Y%m%d-%H%M%S')}.json")
        if not any(SECRETS_PATH.parent.glob("secrets.unreadable-*.json")):
            bak.write_bytes(SECRETS_PATH.read_bytes())
    except OSError:
        pass


def save_secrets(data: dict[str, Any]) -> None:
    ensure_dirs()
    SECRETS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_platform(name: str) -> dict[str, Any]:
    return dict(load_secrets().get(name) or {})


def set_platform(name: str, payload: dict[str, Any] | None) -> None:
    data = load_secrets()
    if payload:
        data[name] = payload
    else:
        data.pop(name, None)
    save_secrets(data)


def cookie_header(platform: str) -> str:
    entry = get_platform(platform)
    cookies = entry.get("cookies") or {}
    if isinstance(cookies, str):
        return cookies
    parts = []
    for k, v in cookies.items():
        if v:
            parts.append(f"{k}={v}")
    return "; ".join(parts)

from __future__ import annotations

import json
from typing import Any

from app.settings import DATA_DIR, SECRETS_PATH, ensure_dirs


def load_secrets() -> dict[str, Any]:
    ensure_dirs()
    if not SECRETS_PATH.exists():
        return {}
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        return {}


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

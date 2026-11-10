from __future__ import annotations

from app.sources.base import SourceAdapter
from app.sources.bili import BiliAdapter
from app.sources.kugou import KugouAdapter
from app.sources.netease import NeteaseAdapter
from app.sources.qishui import QishuiAdapter
from app.sources.qq import QQAdapter

_ADAPTERS: dict[str, SourceAdapter] | None = None


def all_adapters() -> dict[str, SourceAdapter]:
    global _ADAPTERS
    if _ADAPTERS is None:
        inst = [QQAdapter(), QishuiAdapter(), KugouAdapter(), BiliAdapter(), NeteaseAdapter()]
        _ADAPTERS = {a.id: a for a in inst}
    return _ADAPTERS


def get_adapter(source_id: str) -> SourceAdapter | None:
    return all_adapters().get(source_id)

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class SourceStatus(str, Enum):
    AVAILABLE = "available"
    NEEDS_LOGIN = "needs_login"
    PLACEHOLDER = "placeholder"


@dataclass
class SearchResult:
    source: str
    source_id: str
    title: str
    artist: str
    duration: int  # seconds, 0 if unknown
    cover_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    flags: list[str] = field(default_factory=list)
    is_original_like: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def display_label(self) -> str:
        artist = self.artist.strip() or "未知"
        return f"{self.title} - {artist}"


@dataclass
class Playable:
    result: SearchResult
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    is_complete: bool = True
    lyric: str = ""  # raw LRC if any
    expire_hint_seconds: int = 0  # 0 = unknown

    def to_public_dict(self) -> dict[str, Any]:
        r = self.result
        return {
            "source": r.source,
            "source_id": r.source_id,
            "title": r.title,
            "artist": r.artist,
            "duration": r.duration,
            "cover_url": r.cover_url,
            "is_complete": self.is_complete,
            "has_lyric": bool(self.lyric),
        }


@dataclass
class QueueItem:
    id: str
    title: str
    artist: str
    source: str
    source_id: str
    duration: int
    cover_url: str
    requester_uid: int
    requester_name: str
    query: str
    added_at: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        extra = dict(self.extra or {})
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "source": self.source,
            "source_id": self.source_id,
            "duration": self.duration,
            "cover_url": self.cover_url,
            "requester_uid": self.requester_uid,
            "requester_name": self.requester_name,
            "query": self.query,
            "added_at": self.added_at,
            "extra": extra,
            "idle": bool(extra.get("idle")),
        }


@dataclass
class CurrentTrack:
    item: QueueItem
    position: float = 0.0
    paused: bool = False
    lyric: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = self.item.to_dict()
        d.update({"position": self.position, "paused": self.paused, "has_lyric": bool(self.lyric)})
        return d


@dataclass
class SelectionOption:
    index: int  # 1-based
    result: SearchResult

    def to_dict(self) -> dict[str, Any]:
        d = self.result.to_dict()
        d["index"] = self.index
        return d


@dataclass
class SelectionSession:
    id: str
    requester_uid: int
    requester_name: str
    query: str
    options: list[SelectionOption]
    deadline: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "requester_uid": self.requester_uid,
            "requester_name": self.requester_name,
            "query": self.query,
            "options": [o.to_dict() for o in self.options],
            "deadline": self.deadline,
            "source": self.source,
        }


class CommandKind(str, Enum):
    REQUEST = "request"
    CANCEL = "cancel"
    CHOICE = "choice"
    IGNORE = "ignore"


@dataclass
class ParsedCommand:
    kind: CommandKind
    query: str = ""
    choice: int = 0
    raw: str = ""

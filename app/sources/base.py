from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from app.models import Playable, SearchResult, SourceStatus


class SourceAdapter(ABC):
    id: str
    display_name: str
    status: SourceStatus
    note: str = ""

    def public_info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.display_name,
            "status": self.status.value if isinstance(self.status, SourceStatus) else self.status,
            "note": self.note,
            "logged_in": self.is_logged_in(),
        }

    def is_logged_in(self) -> bool:
        return False

    @abstractmethod
    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        """搜索。失败应抛出或返回空列表，不要让异常毁掉整个点歌流程（上层会捕获）。"""

    @abstractmethod
    async def resolve_playable(self, result: SearchResult) -> Optional[Playable]:
        """仅在能拿到完整可播（非 30 秒试听、非加密需解密）地址时返回。否则返回 None。"""

    async def fetch_lyric(self, result: SearchResult) -> str:
        return ""

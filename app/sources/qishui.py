from __future__ import annotations

from typing import Optional

from app.models import Playable, SearchResult, SourceStatus
from app.sources.base import SourceAdapter


class QishuiAdapter(SourceAdapter):
    """汽水音乐占位适配器。

    汽水/抖音音乐常见播放地址使用 spade_a 等加密方案。本项目明确不实现
    这类解密，也不走第三方「换源解锁」接口。因此当前无法在合规前提下
    稳定拿到未加密的完整播放链接：搜索与解析都会返回空，引擎会自动跳到
    下一个音源（默认顺序里下一个是酷狗概念版 / 哔哩哔哩）。
    """

    id = "qishui"
    display_name = "汽水音乐"
    status = SourceStatus.PLACEHOLDER
    note = (
        "占位：没有可用的未加密完整播放链接接口。"
        "不会解密 spade_a / AES 音频，也不会绕过会员。"
        "该源会被自动跳过，由后续音源兜底。"
    )

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        return []

    async def resolve_playable(self, result: SearchResult) -> Optional[Playable]:
        return None

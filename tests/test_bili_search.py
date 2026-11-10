import pytest

from app.sources.bili import BiliAdapter


@pytest.mark.asyncio
async def test_bili_search_returns_playable_audio():
    adapter = BiliAdapter()
    results = await adapter.search("周杰伦 晴天 官方音频", limit=8)
    assert results, "哔哩哔哩搜索应返回结果"
    assert any("晴天" in r.title for r in results)

    playable = None
    for item in results[:6]:
        playable = await adapter.resolve_playable(item)
        if playable and playable.url and playable.is_complete:
            break
    assert playable is not None, "应至少能解析出一条完整可播音轨"
    assert playable.url.startswith("http")
    assert playable.is_complete is True
    assert playable.headers.get("Referer")

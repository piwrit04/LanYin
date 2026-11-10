import asyncio
import time

import pytest

from app.engine import SongEngine
from app.hub import Hub
from app.models import SearchResult, SelectionOption, SelectionSession


class FakeApp:
    def __init__(self):
        self.config = {
            "room_id": 1506613,
            "sources_priority": ["bili"],
            "selection_timeout_seconds": 1,
            "anti_spam": {"max_pending_per_user": 2, "max_queue_size": 30, "cooldown_seconds": 0},
        }
        self.hub = Hub()
        self.real_room_id = 1506613
        self.notices = []

    def public_extra(self):
        return {"room_id": 1506613}


@pytest.mark.asyncio
async def test_selection_timeout_cancels(monkeypatch):
    app = FakeApp()
    engine = SongEngine(app)
    notices = []

    async def fake_notify(uname, body):
        notices.append((uname, body))

    monkeypatch.setattr(engine, "_notify", fake_notify)
    monkeypatch.setattr(engine, "push_state", lambda: asyncio.sleep(0))

    opts = [
        SelectionOption(1, SearchResult("bili", "a", "晴天 官方", "周杰伦", 260)),
        SelectionOption(2, SearchResult("bili", "b", "晴天 MV", "周杰伦", 280)),
    ]
    session = SelectionSession(
        id="abc123",
        requester_uid=42,
        requester_name="小明",
        query="晴天",
        options=opts,
        deadline=time.time() + 0.3,
        source="bili",
    )
    app.hub.selection = session
    await engine._selection_timeout(session.id, 0.3)
    assert app.hub.selection is None
    assert any("超时" in n[1] for n in notices)


@pytest.mark.asyncio
async def test_only_requester_number_is_accepted(monkeypatch):
    app = FakeApp()
    engine = SongEngine(app)
    enqueued = []

    async def fake_enqueue(result, uid, uname, query):
        from app.models import QueueItem

        item = QueueItem(
            id="x",
            title=result.title,
            artist=result.artist,
            source=result.source,
            source_id=result.source_id,
            duration=result.duration,
            cover_url="",
            requester_uid=uid,
            requester_name=uname,
            query=query,
            added_at=time.time(),
        )
        enqueued.append(item)
        return item

    monkeypatch.setattr(engine, "_enqueue", fake_enqueue)
    monkeypatch.setattr(engine, "_maybe_start", lambda: asyncio.sleep(0))
    monkeypatch.setattr(engine, "push_state", lambda: asyncio.sleep(0))
    monkeypatch.setattr(engine, "_notify", lambda *a, **k: asyncio.sleep(0))

    opts = [
        SelectionOption(1, SearchResult("bili", "a", "晴天 官方", "周杰伦", 260)),
        SelectionOption(2, SearchResult("bili", "b", "晴天 MV", "周杰伦", 280)),
    ]
    app.hub.selection = SelectionSession(
        id="s1",
        requester_uid=42,
        requester_name="小明",
        query="晴天",
        options=opts,
        deadline=time.time() + 30,
        source="bili",
    )

    # 别人发数字，不应选中
    await engine.try_choose(99, "路人", 1, from_danmaku=True)
    assert app.hub.selection is not None
    assert enqueued == []

    # 本人发无效序号
    await engine.try_choose(42, "小明", 9, from_danmaku=True)
    assert app.hub.selection is not None

    # 本人发 2
    await engine.try_choose(42, "小明", 2, from_danmaku=True)
    assert app.hub.selection is None
    assert len(enqueued) == 1
    assert enqueued[0].source_id == "b"


@pytest.mark.asyncio
async def test_selection_timeout_plays_first(monkeypatch):
    app = FakeApp()
    app.config["selection"] = {"timeout_action": "play_first"}
    engine = SongEngine(app)
    notices = []
    enqueued = []

    async def fake_notify(uname, body):
        notices.append((uname, body))

    async def fake_enqueue(result, uid, uname, query):
        from app.models import QueueItem

        item = QueueItem(
            id="auto1",
            title=result.title,
            artist=result.artist,
            source=result.source,
            source_id=result.source_id,
            duration=result.duration,
            cover_url="",
            requester_uid=uid,
            requester_name=uname,
            query=query,
            added_at=time.time(),
        )
        enqueued.append(item)
        return item

    monkeypatch.setattr(engine, "_notify", fake_notify)
    monkeypatch.setattr(engine, "_enqueue", fake_enqueue)
    monkeypatch.setattr(engine, "_maybe_start", lambda: asyncio.sleep(0))
    monkeypatch.setattr(engine, "push_state", lambda: asyncio.sleep(0))

    opts = [
        SelectionOption(1, SearchResult("bili", "a", "晴天 官方", "周杰伦", 260)),
        SelectionOption(2, SearchResult("bili", "b", "晴天 MV", "周杰伦", 280)),
    ]
    session = SelectionSession(
        id="abc123",
        requester_uid=42,
        requester_name="小明",
        query="晴天",
        options=opts,
        deadline=time.time() + 0.2,
        source="bili",
    )
    app.hub.selection = session
    await engine._selection_timeout(session.id, 0.05)
    assert app.hub.selection is None
    assert len(enqueued) == 1
    assert enqueued[0].source_id == "a"
    assert not any("取消" in n[1] for n in notices)

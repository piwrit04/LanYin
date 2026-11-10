import time

import pytest

from app.engine import SongEngine
from app.hub import Hub
from app.models import Playable, QueueItem, SearchResult
from app.queue_rules import TIER_CAPTAIN, TIER_GOVERNOR, TIER_REGULAR


class FakeApp:
    def __init__(self):
        self.config = {
            "room_id": 1506613,
            "sources_priority": ["bili"],
            "selection_timeout_seconds": 30,
            "anti_spam": {"max_pending_per_user": 8, "max_queue_size": 30, "cooldown_seconds": 0},
            "welcome": {"enabled": False},
            "idle_playlist": {"enabled": False, "loop": True, "resume_delay_seconds": 0, "items": []},
            "messages": {
                "blacklisted_user": "你已被拉黑",
                "blacklisted_song": "这首歌已拉黑",
            },
            "blacklist": {"users": [], "songs": []},
            "priority_queue": {"enabled": True},
            "console_theme": "wine",
        }
        self.hub = Hub()
        self.real_room_id = 1506613

    def public_extra(self):
        return {"room_id": 1506613, "settings": {"blacklist": self.config["blacklist"]}}


def _result(title="晴天", source_id="bv1", artist="周杰伦"):
    return SearchResult("bili", source_id, title, artist, 270)


def _playable(result=None, url="http://example/a.m4a"):
    result = result or _result()
    return Playable(result=result, url=url, is_complete=True)


@pytest.fixture
def engine(monkeypatch):
    app = FakeApp()
    eng = SongEngine(app)
    toasts = []
    notifies = []

    async def fake_send(cmd):
        return None

    async def fake_push():
        return None

    async def fake_toast_role(role, message):
        toasts.append((role, message))

    async def fake_notify(uname, body):
        notifies.append((uname, body))

    def fake_persist():
        return None

    monkeypatch.setattr(app.hub, "send_player_cmd", fake_send)
    monkeypatch.setattr(app.hub, "send_to_role", fake_toast_role)
    monkeypatch.setattr(eng, "push_state", fake_push)
    monkeypatch.setattr(eng, "_notify", fake_notify)
    monkeypatch.setattr(eng, "_persist_config", fake_persist)
    eng._test_toasts = toasts
    eng._test_notifies = notifies
    eng._test_app = app
    return eng


@pytest.mark.asyncio
async def test_blacklist_rejects_user_with_danmaku(engine):
    engine.cfg()["blacklist"]["users"] = [{"uid": 7, "name": "小黑"}]
    await engine.request_song(7, "小黑", "晴天")
    assert engine.hub().queue == []
    assert engine._test_notifies
    assert engine._test_notifies[-1][1] == "你已被拉黑"
    assert engine._test_toasts == []


@pytest.mark.asyncio
async def test_blacklist_rejects_song_keyword(engine):
    engine.cfg()["blacklist"]["songs"] = ["夜曲"]
    await engine.request_song(3, "观众", "夜曲 周杰伦")
    assert engine.hub().queue == []
    assert engine._test_notifies[-1][1] == "这首歌已拉黑"


@pytest.mark.asyncio
async def test_blacklist_rejects_resolved_title_at_enqueue(engine):
    engine.cfg()["blacklist"]["songs"] = ["七里香"]
    result = _result("七里香", "bv7")
    engine._cache_tmp(_playable(result))
    item = await engine._enqueue(result, 3, "观众", "七里")
    assert item is None
    assert engine.hub().queue == []
    assert any("七里香" in n[1] or n[1] == "这首歌已拉黑" for n in engine._test_notifies)


@pytest.mark.asyncio
async def test_priority_inserts_ahead_fifo_and_does_not_bump_current(engine):
    # already playing, must not be touched
    current = QueueItem(
        id="now",
        title="正在播",
        artist="A",
        source="bili",
        source_id="n",
        duration=200,
        cover_url="",
        requester_uid=1,
        requester_name="路人",
        query="正在播",
        added_at=time.time(),
        extra={"priority_tier": TIER_REGULAR},
    )
    engine.hub().current = type("Cur", (), {"item": current, "position": 12, "paused": False, "lyric": ""})()

    def enqueue(title, uid, uname, guard=0, medal=0, sid=None):
        result = _result(title, sid or title)
        engine._cache_tmp(_playable(result))
        engine._pending_priv[uid] = {"guard_level": guard, "medal_room": medal, "medal_name": "粉丝"}
        return engine._enqueue(result, uid, uname, title)

    a = await enqueue("路人甲", 11, "甲")
    b = await enqueue("路人乙", 12, "乙")
    c = await enqueue("舰长歌", 13, "舰", guard=3)
    d = await enqueue("总督歌", 14, "督", guard=1)
    e = await enqueue("另一舰长", 15, "舰2", guard=3)
    f = await enqueue("粉丝牌歌", 16, "粉", medal=1506613)
    titles = [i.title for i in engine.hub().queue]
    assert titles == ["总督歌", "舰长歌", "另一舰长", "粉丝牌歌", "路人甲", "路人乙"]
    assert a and b and c and d and e and f
    assert engine.hub().current.item.id == "now"
    assert engine.hub().current.item.title == "正在播"


@pytest.mark.asyncio
async def test_accepted_request_emits_overlay_toast(engine):
    result = _result("晴天", "bv1")
    engine._cache_tmp(_playable(result))
    item = await engine._enqueue(result, 8, "小明", "晴天")
    assert item is not None
    assert engine._test_toasts
    role, msg = engine._test_toasts[-1]
    assert role == "overlay"
    assert msg["type"] == "request_toast"
    assert msg["payload"]["uname"] == "小明"
    assert msg["payload"]["title"] == "晴天"


@pytest.mark.asyncio
async def test_blacklist_current_song_adds_title(engine):
    item = QueueItem(
        id="now",
        title="夜曲",
        artist="周杰伦",
        source="qq",
        source_id="q1",
        duration=200,
        cover_url="",
        requester_uid=1,
        requester_name="观众",
        query="夜曲",
        added_at=time.time(),
        extra={},
    )
    from app.models import CurrentTrack
    engine.hub().current = CurrentTrack(item=item)
    bl = await engine.blacklist_current_song()
    assert "夜曲" in bl["songs"]
    assert "夜曲" in engine.cfg()["blacklist"]["songs"]


@pytest.mark.asyncio
async def test_add_current_to_idle_uses_title_query(engine, monkeypatch):
    captured = []

    async def fake_add(text):
        captured.append(text)
        return {"kind": "query", "query": text, "title": text}

    monkeypatch.setattr(engine, "add_idle_item", fake_add)
    from app.models import CurrentTrack
    item = QueueItem(
        id="now",
        title="晴天",
        artist="周杰伦",
        source="qq",
        source_id="q1",
        duration=200,
        cover_url="",
        requester_uid=1,
        requester_name="观众",
        query="晴天",
        added_at=time.time(),
        extra={},
    )
    engine.hub().current = CurrentTrack(item=item)
    await engine.add_current_to_idle()
    assert captured == ["晴天 周杰伦"]

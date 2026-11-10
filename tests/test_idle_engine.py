import asyncio
import time

import pytest

from app.engine import SongEngine
from app.hub import Hub
from app.models import Playable, QueueItem, SearchResult


class FakeApp:
    def __init__(self):
        self.config = {
            "room_id": 1506613,
            "sources_priority": ["bili"],
            "selection_timeout_seconds": 30,
            "anti_spam": {"max_pending_per_user": 2, "max_queue_size": 30, "cooldown_seconds": 0},
            "welcome": {"enabled": True},
            "idle_playlist": {
                "enabled": True,
                "loop": True,
                "resume_delay_seconds": 0,
                "items": [{"id": "i1", "kind": "query", "query": "晴天 周杰伦", "title": "晴天 周杰伦"}],
            },
        }
        self.hub = Hub()
        self.real_room_id = 1506613

    def public_extra(self):
        return {"room_id": 1506613}


def _result(title="晴天", source_id="bv1"):
    return SearchResult("bili", source_id, title, "周杰伦", 270)


def _playable(result=None, url="http://example/a.m4a"):
    result = result or _result()
    return Playable(result=result, url=url, is_complete=True)


def _item(result=None, *, idle=False, uid=1, uname="观众", item_id="t1"):
    result = result or _result()
    return QueueItem(
        id=item_id,
        title=result.title,
        artist=result.artist,
        source=result.source,
        source_id=result.source_id,
        duration=result.duration,
        cover_url="",
        requester_uid=-1 if idle else uid,
        requester_name="默认歌单" if idle else uname,
        query=result.title,
        added_at=time.time(),
        extra={"idle": True} if idle else {},
    )


@pytest.fixture
def engine(monkeypatch):
    app = FakeApp()
    eng = SongEngine(app)
    commands = []

    async def fake_send(cmd):
        commands.append(cmd)

    async def fake_push():
        return None

    monkeypatch.setattr(app.hub, "send_player_cmd", fake_send)
    monkeypatch.setattr(eng, "push_state", fake_push)
    eng._test_cmds = commands
    eng._test_app = app
    return eng


@pytest.mark.asyncio
async def test_idle_starts_when_queue_empty(engine, monkeypatch):
    async def fake_resolve(spec):
        result = _result()
        playable = _playable(result)
        item = _item(result, idle=True, item_id="idle1")
        engine._playables[item.id] = playable
        return item, playable

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    assert engine.hub().queue == []
    assert engine.hub().current is None
    await engine.play_next()
    cur = engine.hub().current
    assert cur is not None
    assert cur.item.requester_name == "默认歌单"
    assert cur.item.extra.get("idle") is True
    assert cur.item.to_dict()["idle"] is True
    assert engine.hub().queue == []  # 默认曲不应进入点歌队列
    assert any(c.get("action") == "play" for c in engine._test_cmds)


@pytest.mark.asyncio
async def test_request_preempts_idle_then_resumes(engine, monkeypatch):
    async def fake_resolve(spec):
        result = _result("晴天", "idle-bv")
        playable = _playable(result)
        item = _item(result, idle=True, item_id="idle1")
        engine._playables[item.id] = playable
        return item, playable

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    await engine.play_next()
    assert engine.hub().current.item.extra.get("idle") is True

    req = _result("七里香", "req-bv")
    engine._cache_tmp(_playable(req, url="http://example/b.m4a"))

    async def fake_search(query):
        return req, [], "bili", []

    monkeypatch.setattr(engine, "_search_by_priority", fake_search)
    monkeypatch.setattr(engine, "_notify", lambda *a, **k: asyncio.sleep(0))

    engine.hub().current.position = 87
    await engine.request_song(42, "小明", "七里香")
    # 点歌到来时立刻打断默认歌单
    assert engine.hub().current is not None
    assert engine.hub().current.item.title == "七里香"
    assert engine.hub().current.item.requester_name == "小明"
    assert not engine.hub().current.item.extra.get("idle")
    assert engine.hub().queue == []
    assert engine._paused_idle is not None
    assert engine._paused_idle["item"].title == "晴天"
    assert engine._paused_idle["position"] == 87

    await engine.on_player_ended()
    # 点歌播完后从暂停处恢复默认歌单
    assert engine.hub().current is not None
    assert engine.hub().current.item.title == "晴天"
    assert engine.hub().current.item.extra.get("idle") is True
    assert engine.hub().current.position == 87
    play_cmds = [c for c in engine._test_cmds if c.get("action") == "play"]
    assert play_cmds[-1].get("position") == 87
    assert engine._paused_idle is None


@pytest.mark.asyncio
async def test_requests_have_priority_over_idle(engine, monkeypatch):
    idle_called = []

    async def fake_resolve(spec):
        idle_called.append(spec)
        result = _result()
        playable = _playable(result)
        item = _item(result, idle=True, item_id="idle1")
        engine._playables[item.id] = playable
        return item, playable

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    req = _result("七里香", "req-bv")
    playable = _playable(req, url="http://example/b.m4a")
    item = _item(req, idle=False, uid=7, uname="观众甲", item_id="req1")
    engine._playables[item.id] = playable
    engine.hub().queue.append(item)

    await engine.play_next()
    assert engine.hub().current.item.id == "req1"
    assert not engine.hub().current.item.extra.get("idle")
    assert idle_called == []


@pytest.mark.asyncio
async def test_idle_all_fail_stops_once(engine, monkeypatch):
    calls = []

    async def fail_resolve(spec):
        calls.append(spec)
        return None

    monkeypatch.setattr(engine, "_resolve_idle_item", fail_resolve)
    engine._test_app.config["idle_playlist"]["items"] = [
        {"id": "a", "kind": "query", "query": "A"},
        {"id": "b", "kind": "query", "query": "B"},
    ]
    await engine.play_next()
    assert engine.hub().current is None
    assert len(calls) == 2
    # 再调用不会因 loop=True 而无限递归
    await engine.play_next()
    assert engine.hub().current is None
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_clear_queue_keeps_saved_playlist(engine, monkeypatch):
    req = _result("七里香", "req-bv")
    item = _item(req, idle=False, item_id="req1")
    engine._playables[item.id] = _playable(req)
    engine.hub().queue.append(item)
    saved = list(engine.cfg()["idle_playlist"]["items"])
    await engine.clear_queue()
    assert engine.hub().queue == []
    assert engine.cfg()["idle_playlist"]["items"] == saved


@pytest.mark.asyncio
async def test_skip_last_request_resumes_idle(engine, monkeypatch):
    async def fake_resolve(spec):
        result = _result("晴天", "idle-bv")
        playable = _playable(result)
        item = _item(result, idle=True, item_id="idle1")
        engine._playables[item.id] = playable
        return item, playable

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    await engine.play_next()
    engine.hub().current.position = 30

    req = _result("七里香", "req-bv")
    engine._cache_tmp(_playable(req, url="http://example/b.m4a"))

    async def fake_search(query):
        return req, [], "bili", []

    monkeypatch.setattr(engine, "_search_by_priority", fake_search)
    monkeypatch.setattr(engine, "_notify", lambda *a, **k: asyncio.sleep(0))
    await engine.request_song(42, "小明", "七里香")
    assert engine.hub().current.item.title == "七里香"

    await engine.skip()
    assert engine.hub().current is not None
    assert engine.hub().current.item.title == "晴天"
    assert engine.hub().current.position == 30


@pytest.mark.asyncio
async def test_previous_restarts_when_position_over_3s(engine, monkeypatch):
    async def fake_resolve(spec):
        result = _result("晴天", "idle-bv")
        playable = _playable(result)
        item = _item(result, idle=True, item_id="idle1")
        engine._playables[item.id] = playable
        return item, playable

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    await engine.play_next()
    cur = engine.hub().current
    assert cur is not None
    cur.position = 12
    same_id = cur.item.id
    await engine.previous()
    cur = engine.hub().current
    assert cur is not None
    assert cur.item.id == same_id
    assert cur.item.title == "晴天"
    assert cur.position == 0
    assert all(h["item"].id != same_id for h in engine._history)


@pytest.mark.asyncio
async def test_previous_idle_item_and_no_hang(engine, monkeypatch):
    engine._test_app.config["idle_playlist"]["items"] = [
        {"id": "a", "kind": "query", "query": "晴天", "title": "晴天"},
        {"id": "b", "kind": "query", "query": "七里香", "title": "七里香"},
    ]

    async def fake_resolve(spec):
        q = str(spec.get("query") or spec.get("title") or "")
        if "七里香" in q:
            result = _result("七里香", "idle-b")
            item = _item(result, idle=True, item_id="idle-b")
        else:
            result = _result("晴天", "idle-a")
            item = _item(result, idle=True, item_id="idle-a")
        playable = _playable(result)
        engine._playables[item.id] = playable
        return item, playable

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    await engine.play_next()
    assert engine.hub().current.item.title == "晴天"
    nxt = engine.peek_next()
    assert nxt is not None
    assert "七里香" in (nxt.get("title") or "")
    await engine.on_player_ended()
    assert engine.hub().current is not None
    assert engine.hub().current.item.title == "七里香"
    engine.hub().current.position = 1
    await engine.previous()
    assert engine.hub().current is not None
    assert engine.hub().current.item.title == "晴天"
    assert engine.hub().current.position == 0
    # 再操作不应卡死
    engine.hub().current.position = 1
    await engine.previous()
    assert engine.hub().current is not None
    await engine.skip()
    assert engine.hub().current is not None

@pytest.mark.asyncio
async def test_replacing_collection_stops_old_follow(engine, monkeypatch):
    """去掉雾氧合集、换成音乐盲盒后，不应再续播雾氧。"""
    from app.models import CurrentTrack

    def noop_persist():
        return None

    monkeypatch.setattr(engine, "_persist_config", noop_persist)

    old = _result("《无法克制》雾氧Uo", "BV_wuyang")
    playing = _item(old, idle=True, item_id="playing-old")
    playing.extra = {
        "idle": True,
        "bvid": "BV_wuyang",
        "idle_follow_ep": {
            "bvid": "BV_wuyang",
            "title": "无法克制",
            "season_title": "雾氧Uo直播翻唱",
        },
    }
    engine._playables[playing.id] = _playable(old)
    engine.hub().current = CurrentTrack(item=playing, position=30, paused=False, lyric="")
    engine._idle_follow = [
        {"bvid": "BV_wuyang2", "title": "读心术", "artist": "雾氧Uo", "season_title": "雾氧Uo直播翻唱"},
    ]
    engine._idle_follow_origin = {
        "id": "old1",
        "bvid": "BV_wuyang0",
        "season_title": "雾氧Uo直播翻唱",
    }
    engine.cfg()["idle_playlist"]["items"] = [
        {
            "id": "new1",
            "kind": "bili",
            "bvid": "BV11wxM6PEez",
            "title": "音乐盲盒",
            "artist": "稻穗音乐",
            "season_title": "热门音乐合集",
            "season_index": 4,
            "season_total": 8,
        }
    ]

    async def fake_resolve(spec):
        bvid = str(spec.get("bvid") or "")
        if bvid == "BV11wxM6PEez" or spec.get("id") == "new1":
            result = _result("音乐盲盒", "BV11wxM6PEez")
            item = _item(result, idle=True, item_id="new-play")
            item.extra = {"idle": True, "bvid": "BV11wxM6PEez"}
            engine._playables[item.id] = _playable(result)
            return item, engine._playables[item.id]
        return None

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    await engine._sync_idle_playback()
    cur = engine.hub().current
    assert cur is not None
    assert cur.item.title == "音乐盲盒"
    assert engine._idle_follow is None
    nxt = engine.peek_next()
    assert nxt is not None
    assert "音乐盲盒" in (nxt.get("title") or "")


@pytest.mark.asyncio
async def test_remove_collection_stops_its_follow_and_current(engine, monkeypatch):
    from app.models import CurrentTrack

    def noop_persist():
        return None

    monkeypatch.setattr(engine, "_persist_config", noop_persist)
    engine.cfg()["idle_playlist"]["items"] = [
        {
            "id": "old1",
            "kind": "bili",
            "bvid": "BV_wuyang0",
            "title": "雾氧合集",
            "season_title": "雾氧Uo直播翻唱",
        }
    ]
    old = _result("《无法克制》雾氧Uo", "BV_wuyang")
    playing = _item(old, idle=True, item_id="playing-old")
    playing.extra = {
        "idle": True,
        "bvid": "BV_wuyang",
        "idle_follow_ep": {"bvid": "BV_wuyang", "title": "无法克制", "season_title": "雾氧Uo直播翻唱"},
    }
    engine._playables[playing.id] = _playable(old)
    engine.hub().current = CurrentTrack(item=playing, position=12, paused=False, lyric="")
    engine._idle_follow = [
        {"bvid": "BV_wuyang2", "title": "读心术", "season_title": "雾氧Uo直播翻唱"},
    ]
    engine._idle_follow_origin = {"id": "old1", "bvid": "BV_wuyang0", "season_title": "雾氧Uo直播翻唱"}

    await engine.remove_idle_item(item_id="old1")
    assert engine.cfg()["idle_playlist"]["items"] == []
    assert engine.hub().current is None
    assert engine._idle_follow is None
    assert any(c.get("action") == "stop" for c in engine._test_cmds)


@pytest.mark.asyncio
async def test_collection_follow_continues_for_remaining_item(engine, monkeypatch):
    from app.models import CurrentTrack

    engine.cfg()["idle_playlist"]["items"] = [
        {
            "id": "box1",
            "kind": "bili",
            "bvid": "BV11wxM6PEez",
            "title": "音乐盲盒",
            "season_title": "热门音乐合集",
            "season_index": 4,
            "season_total": 8,
        }
    ]
    cur_res = _result("音乐盲盒", "BV11wxM6PEez")
    cur_item = _item(cur_res, idle=True, item_id="box-now")
    cur_item.extra = {"idle": True, "bvid": "BV11wxM6PEez", "season_title": "热门音乐合集"}
    engine._playables[cur_item.id] = _playable(cur_res)
    engine.hub().current = CurrentTrack(item=cur_item, position=40, paused=False, lyric="")
    engine._idle_follow = [
        {"bvid": "BV_box5", "title": "音乐盲盒下集", "artist": "稻穗音乐", "season_title": "热门音乐合集"},
    ]
    engine._idle_follow_origin = {
        "id": "box1",
        "bvid": "BV11wxM6PEez",
        "season_title": "热门音乐合集",
    }

    await engine._sync_idle_playback()
    assert engine.hub().current is not None
    assert engine.hub().current.item.id == "box-now"
    assert engine._idle_follow
    nxt = engine.peek_next()
    assert nxt is not None
    assert "下集" in (nxt.get("title") or "")

    async def fake_resolve(spec):
        if str(spec.get("bvid") or "") == "BV_box5" or spec.get("_from_follow"):
            result = _result("音乐盲盒下集", "BV_box5")
            item = _item(result, idle=True, item_id="box-next")
            item.extra = {
                "idle": True,
                "bvid": "BV_box5",
                "idle_follow_ep": {"bvid": "BV_box5", "title": "音乐盲盒下集", "season_title": "热门音乐合集"},
            }
            engine._playables[item.id] = _playable(result)
            return item, engine._playables[item.id]
        return None

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    engine.hub().current = None
    assert await engine._play_idle_follow() is True
    assert engine.hub().current.item.title == "音乐盲盒下集"


@pytest.mark.asyncio
async def test_stale_paused_idle_dropped_after_playlist_change(engine, monkeypatch):
    old = _result("《无法克制》雾氧Uo", "BV_wuyang")
    parked = _item(old, idle=True, item_id="paused-old")
    parked.extra = {
        "idle": True,
        "bvid": "BV_wuyang",
        "idle_follow_ep": {"bvid": "BV_wuyang", "season_title": "雾氧Uo直播翻唱"},
    }
    engine._playables[parked.id] = _playable(old)
    engine._paused_idle = {"item": parked, "position": 87, "lyric": ""}
    engine._idle_follow = [{"bvid": "BV_wuyang2", "title": "读心术", "season_title": "雾氧Uo直播翻唱"}]
    engine._idle_follow_origin = {"id": "old1", "bvid": "BV_wuyang0", "season_title": "雾氧Uo直播翻唱"}
    engine.cfg()["idle_playlist"]["items"] = [
        {
            "id": "new1",
            "kind": "bili",
            "bvid": "BV11wxM6PEez",
            "title": "音乐盲盒",
            "season_title": "热门音乐合集",
        }
    ]

    async def fake_resolve(spec):
        result = _result("音乐盲盒", "BV11wxM6PEez")
        item = _item(result, idle=True, item_id="new-play")
        item.extra = {"idle": True, "bvid": "BV11wxM6PEez"}
        engine._playables[item.id] = _playable(result)
        return item, engine._playables[item.id]

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    assert await engine._resume_paused_idle() is True
    assert engine._paused_idle is None
    assert engine.hub().current is not None
    assert engine.hub().current.item.title == "音乐盲盒"
    assert engine._idle_follow is None


@pytest.mark.asyncio
async def test_remove_playing_collection_stops_immediately_keeps_requests(engine, monkeypatch):
    """移除默认歌单条目后立刻停止当前默认歌曲，并清空合集续播，点歌列表保留。"""
    from app.models import CurrentTrack

    def noop_persist():
        return None

    monkeypatch.setattr(engine, "_persist_config", noop_persist)
    engine.cfg()["idle_playlist"]["items"] = [
        {
            "id": "old1",
            "kind": "bili",
            "bvid": "BV_wuyang0",
            "title": "雾氧合集",
            "season_title": "雾氧Uo直播翻唱",
        }
    ]
    old = _result("《无法克制》雾氧Uo", "BV_wuyang")
    playing = _item(old, idle=True, item_id="playing-old")
    playing.extra = {
        "idle": True,
        "bvid": "BV_wuyang",
        "idle_follow_ep": {"bvid": "BV_wuyang", "title": "无法克制", "season_title": "雾氧Uo直播翻唱"},
    }
    engine._playables[playing.id] = _playable(old)
    engine.hub().current = CurrentTrack(item=playing, position=97, paused=False, lyric="")
    engine._idle_follow = [
        {"bvid": "BV_wuyang2", "title": "读心术", "season_title": "雾氧Uo直播翻唱"},
        {"bvid": "BV_wuyang3", "title": "普通朋友", "season_title": "雾氧Uo直播翻唱"},
    ]
    engine._idle_follow_origin = {"id": "old1", "bvid": "BV_wuyang0", "season_title": "雾氧Uo直播翻唱"}

    req = _result("七里香", "req-bv")
    queued = _item(req, idle=False, uid=7, uname="观众甲", item_id="req1")
    engine._playables[queued.id] = _playable(req, url="http://example/b.m4a")
    engine.hub().queue.append(queued)

    await engine.remove_idle_item(item_id="old1")
    assert engine.cfg()["idle_playlist"]["items"] == []
    assert engine._idle_follow is None
    # 正在播的默认歌被立刻停掉，改播点歌列表里的曲子
    assert engine.hub().current is not None
    assert engine.hub().current.item.id == "req1"
    assert engine.hub().current.item.title == "七里香"
    assert not engine.hub().current.item.extra.get("idle")


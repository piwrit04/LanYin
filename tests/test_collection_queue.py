import asyncio
import time

import pytest

from app.engine import SongEngine
from app.hub import Hub
from app.models import CurrentTrack, Playable, QueueItem, SearchResult
from app.queue_rules import TIER_IDLE, TIER_REGULAR
from app.sources.bili import collection_play_order, episode_start_index


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
                "items": [],
            },
        }
        self.hub = Hub()
        self.real_room_id = 1506613

    def public_extra(self):
        return {"room_id": 1506613}


def _result(title="晴天", source_id="bv1", **extra):
    return SearchResult("bili", source_id, title, "周杰伦", 270, extra=extra or {})


def _playable(result=None, url="http://example/a.m4a"):
    result = result or _result()
    return Playable(result=result, url=url, is_complete=True)


def _item(result=None, *, idle=False, uid=1, uname="观众", item_id="t1", extra=None):
    result = result or _result()
    base = {"idle": True} if idle else {}
    if extra:
        base.update(extra)
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
        extra=base,
    )


def _box_eps(n=8, title="音乐盲盒"):
    return [
        {
            "bvid": f"BV_box{i}",
            "aid": i,
            "cid": i,
            "title": f"{title} {i}",
            "artist": "稻穗音乐",
            "cover_url": "",
            "duration": 180,
            "season_title": title,
            "season_index": i,
            "season_total": n,
        }
        for i in range(1, n + 1)
    ]


def _box_spec(eps, start=4):
    ep = eps[start - 1]
    return {
        "id": "box1",
        "kind": "bili",
        "bvid": ep["bvid"],
        "cid": ep["cid"],
        "title": ep["title"],
        "artist": ep["artist"],
        "season_title": ep["season_title"],
        "season_index": ep["season_index"],
        "season_total": ep["season_total"],
    }


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
    monkeypatch.setattr(eng, "_persist_config", lambda: None)
    eng._test_cmds = commands
    eng._test_app = app
    return eng


def _titles(engine):
    cur = engine.hub().current
    now = [cur.item.title] if cur else []
    return now + [i.title for i in engine.hub().queue]


@pytest.mark.asyncio
async def test_add_8_ep_collection_lists_all_and_starts_at_added(engine, monkeypatch):
    eps = _box_eps()
    spec = _box_spec(eps, start=4)
    engine.cfg()["idle_playlist"]["items"] = [spec]

    async def fake_resolve_playable(adapter, result):
        return _playable(result)

    monkeypatch.setattr(engine, "_try_resolve", fake_resolve_playable)

    engine._enqueue_collection_episodes(spec, eps, start_index=3, loop=True, include_start=True)
    listed = _titles(engine)
    assert listed == [f"音乐盲盒 {i}" for i in (4, 5, 6, 7, 8, 1, 2, 3)]
    assert len(engine.hub().queue) == 8

    await engine.play_next()
    assert engine.hub().current is not None
    assert engine.hub().current.item.title == "音乐盲盒 4"
    assert engine.hub().current.item.requester_name == "默认歌单"
    assert engine.hub().current.item.extra.get("idle") is True
    assert [i.title for i in engine.hub().queue] == [f"音乐盲盒 {i}" for i in (5, 6, 7, 8, 1, 2, 3)]
    assert _titles(engine) == [f"音乐盲盒 {i}" for i in (4, 5, 6, 7, 8, 1, 2, 3)]


@pytest.mark.asyncio
async def test_remove_collection_clears_its_rows_keeps_request(engine, monkeypatch):
    eps = _box_eps()
    spec = _box_spec(eps, start=4)
    engine.cfg()["idle_playlist"]["items"] = [spec]

    async def fake_resolve_playable(adapter, result):
        return _playable(result, url="http://example/col.m4a")

    monkeypatch.setattr(engine, "_try_resolve", fake_resolve_playable)
    engine._enqueue_collection_episodes(spec, eps, start_index=3, loop=True, include_start=True)
    await engine.play_next()
    assert engine.hub().current.item.title == "音乐盲盒 4"

    req = _result("七里香", "req-bv")
    queued = _item(req, idle=False, uid=7, uname="观众甲", item_id="req1")
    engine._playables[queued.id] = _playable(req, url="http://example/b.m4a")
    engine._insert_queued(queued)

    await engine.remove_idle_item(item_id="box1")
    assert engine.cfg()["idle_playlist"]["items"] == []
    assert engine._idle_follow is None
    assert all(not i.extra.get("idle") for i in engine.hub().queue)
    assert engine.hub().current is not None
    assert engine.hub().current.item.id == "req1"
    assert engine.hub().current.item.title == "七里香"
    assert not engine.hub().current.item.extra.get("idle")


@pytest.mark.asyncio
async def test_reorder_queue_changes_upcoming_order(engine):
    eps = _box_eps()
    spec = _box_spec(eps, start=4)
    engine.cfg()["idle_playlist"]["items"] = [spec]
    engine._enqueue_collection_episodes(spec, eps, start_index=3, loop=True, include_start=True)
    # pretend first is already playing: drop it from queue
    first = engine.hub().queue.pop(0)
    engine.hub().current = CurrentTrack(item=first, position=10, paused=False, lyric="")
    upcoming = [i.id for i in engine.hub().queue]
    # 把最后一首（音乐盲盒 3）插到最前
    last = upcoming[-1]
    new_ids = [last] + upcoming[:-1]
    saved = list(engine.cfg()["idle_playlist"]["items"])
    await engine.reorder_queue(new_ids)
    assert [i.id for i in engine.hub().queue] == new_ids
    assert engine.hub().queue[0].title == "音乐盲盒 3"
    assert engine.hub().current.item.title == "音乐盲盒 4"
    assert engine.cfg()["idle_playlist"]["items"] == saved


@pytest.mark.asyncio
async def test_request_inserts_ahead_of_remaining_collection(engine, monkeypatch):
    eps = _box_eps()
    spec = _box_spec(eps, start=4)
    engine.cfg()["idle_playlist"]["items"] = [spec]
    engine._enqueue_collection_episodes(spec, eps, start_index=3, loop=True, include_start=True)
    first = engine.hub().queue.pop(0)
    engine._playables[first.id] = _playable(_result(first.title, first.source_id))
    engine.hub().current = CurrentTrack(item=first, position=40, paused=False, lyric="")

    req = _result("七里香", "req-bv")
    engine._cache_tmp(_playable(req, url="http://example/b.m4a"))

    async def fake_search(query):
        return req, [], "bili", []

    monkeypatch.setattr(engine, "_search_by_priority", fake_search)
    monkeypatch.setattr(engine, "_notify", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(engine, "_emit_request_toast", lambda *a, **k: asyncio.sleep(0))

    await engine.request_song(42, "小明", "七里香")
    assert engine.hub().current is not None
    assert engine.hub().current.item.title == "七里香"
    assert engine.hub().current.item.requester_name == "小明"
    assert not engine.hub().current.item.extra.get("idle")
    assert engine._paused_idle is not None
    assert engine._paused_idle["item"].title == "音乐盲盒 4"
    # 点歌插在合集待播之前，且没有把合集待播清掉
    assert [i.title for i in engine.hub().queue] == [f"音乐盲盒 {i}" for i in (5, 6, 7, 8, 1, 2, 3)]
    for item in engine.hub().queue:
        assert item.extra.get("idle") is True
        assert item.requester_name == "默认歌单"
        assert item.extra.get("priority_tier") == TIER_IDLE

    await engine.on_player_ended()
    # 点歌播完后从暂停处恢复当前合集集，再继续后面的列表
    assert engine.hub().current.item.title == "音乐盲盒 4"
    assert engine.hub().current.item.extra.get("idle") is True
    assert [i.title for i in engine.hub().queue] == [f"音乐盲盒 {i}" for i in (5, 6, 7, 8, 1, 2, 3)]


@pytest.mark.asyncio
async def test_second_collection_appends_and_remove_only_that_one(engine):
    first = _box_eps(8, "音乐盲盒")
    second = _box_eps(3, "雾氧合集")
    for i, ep in enumerate(second, start=1):
        ep["bvid"] = f"BV_uo{i}"
        ep["aid"] = 100 + i
        ep["cid"] = 100 + i
    spec_a = _box_spec(first, start=4)
    spec_b = {
        "id": "uo1",
        "kind": "bili",
        "bvid": second[0]["bvid"],
        "cid": second[0]["cid"],
        "title": second[0]["title"],
        "season_title": "雾氧合集",
        "season_index": 1,
        "season_total": 3,
    }
    engine.cfg()["idle_playlist"]["items"] = [spec_a, spec_b]
    engine._enqueue_collection_episodes(spec_a, first, start_index=3, loop=True, include_start=True)
    engine._enqueue_collection_episodes(spec_b, second, start_index=0, loop=True, include_start=True)
    titles = [i.title for i in engine.hub().queue]
    assert titles[:8] == [f"音乐盲盒 {i}" for i in (4, 5, 6, 7, 8, 1, 2, 3)]
    assert titles[8:] == ["雾氧合集 1", "雾氧合集 2", "雾氧合集 3"]

    engine._discard_queue_from_spec(spec_a)
    left = [i.title for i in engine.hub().queue]
    assert left == ["雾氧合集 1", "雾氧合集 2", "雾氧合集 3"]


@pytest.mark.asyncio
async def test_collection_rows_are_not_requests(engine):
    eps = _box_eps()
    spec = _box_spec(eps, start=4)
    engine._enqueue_collection_episodes(spec, eps, start_index=3, loop=True, include_start=True)
    for item in engine.hub().queue:
        assert item.extra.get("idle") is True
        assert item.requester_uid == -1
        assert item.requester_name == "默认歌单"
        assert item.extra.get("priority_tier") == TIER_IDLE
        assert item.extra.get("priority_tier") != TIER_REGULAR
        d = item.to_dict()
        assert d["idle"] is True


def test_play_order_helpers():
    eps = _box_eps()
    start = episode_start_index(eps, bvid="BV_box4")
    assert start == 3
    assert [e["title"] for e in collection_play_order(eps, start, loop=True)][0] == "音乐盲盒 4"

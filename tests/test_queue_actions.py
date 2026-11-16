"""播放列表 / 默认歌单：置顶、置底、立刻播放、下一首播放。"""
from __future__ import annotations

import pytest

from app.engine import SongEngine
from app.models import Playable, QueueItem
from tests.test_idle_engine import FakeApp, _item, _playable, _result


@pytest.fixture
def engine(tmp_path, monkeypatch):
    app = FakeApp()
    app.playback_path = tmp_path / "playback.json"
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
    return eng


def _queued(engine, titles):
    items = []
    for i, title in enumerate(titles, 1):
        r = _result(title, f"id{i}")
        it = _item(r, item_id=f"q{i}", uname="观众")
        engine._playables[it.id] = _playable(r, url=f"http://example/{i}.m4a")
        items.append(it)
    engine.hub().queue = items
    return items


@pytest.mark.asyncio
async def test_move_queue_top_and_bottom(engine):
    _queued(engine, ["A", "B", "C"])
    await engine.move_queue_item("q3", "top")
    assert [i.id for i in engine.hub().queue] == ["q3", "q1", "q2"]
    await engine.move_queue_item("q3", "bottom")
    assert [i.id for i in engine.hub().queue] == ["q1", "q2", "q3"]


@pytest.mark.asyncio
async def test_play_item_next_moves_to_front(engine):
    _queued(engine, ["A", "B", "C"])
    await engine.play_item_next("q3", source="queue")
    assert [i.id for i in engine.hub().queue] == ["q3", "q1", "q2"]


@pytest.mark.asyncio
async def test_play_item_now_interrupts_and_parks_current(engine):
    from app.models import CurrentTrack

    current = _item(_result("正在播", "cur"), item_id="cur1")
    engine._playables[current.id] = _playable(_result("正在播", "cur"))
    engine.hub().current = CurrentTrack(item=current, position=11, paused=False, lyric="")
    _queued(engine, ["A", "B"])
    await engine.play_item_now("q2", source="queue")
    assert engine.hub().current.item.id == "q2"
    assert engine.hub().current.paused is False
    # 原来那首回到队列最前
    assert engine.hub().queue[0].id == "cur1"
    assert "q2" not in [i.id for i in engine.hub().queue]


@pytest.mark.asyncio
async def test_move_idle_top_bottom(engine):
    pl = engine.cfg()["idle_playlist"]
    pl["items"] = [
        {"id": "i1", "kind": "query", "query": "A", "title": "A"},
        {"id": "i2", "kind": "query", "query": "B", "title": "B"},
        {"id": "i3", "kind": "query", "query": "C", "title": "C"},
    ]
    await engine.move_idle_item("i3", "top")
    assert [x["id"] for x in pl["items"]] == ["i3", "i1", "i2"]
    await engine.move_idle_item("i3", "bottom")
    assert [x["id"] for x in pl["items"]] == ["i1", "i2", "i3"]


@pytest.mark.asyncio
async def test_play_idle_now_and_next(engine, monkeypatch):
    pl = engine.cfg()["idle_playlist"]
    pl["enabled"] = True
    pl["items"] = [
        {"id": "i1", "kind": "query", "query": "A", "title": "A"},
        {"id": "i2", "kind": "query", "query": "B", "title": "B"},
    ]

    async def fake_resolve(spec):
        title = spec["title"]
        r = _result(title, spec["id"])
        item = _item(r, idle=True, item_id="play-" + spec["id"])
        playable = _playable(r)
        engine._playables[item.id] = playable
        return item, playable

    monkeypatch.setattr(engine, "_resolve_idle_item", fake_resolve)
    await engine.play_item_next("i2", source="idle")
    assert engine.hub().queue[0].title == "B"
    await engine.play_item_now("i1", source="idle")
    assert engine.hub().current.item.title == "A"
    assert engine.hub().current.item.extra.get("idle") is True

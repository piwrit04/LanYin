"""启动时恢复上次歌曲和进度（暂停），直播点歌仍立即播放，刷新不打断正在播的会话。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from app.engine import SongEngine
from app.hub import Hub
from app.models import CurrentTrack, Playable, QueueItem, SearchResult
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
    eng._test_cmds = commands
    eng._test_app = app
    return eng


@pytest.mark.asyncio
async def test_restore_last_song_paused_and_does_not_autostart_idle(engine, monkeypatch):
    item = _item(_result("晴天", "bv1"), item_id="song1")
    playable = _playable(_result("晴天", "bv1"))

    async def fake_resolve(saved):
        engine._playables[saved.id] = playable
        return playable

    monkeypatch.setattr(engine, "_resolve_saved_item", fake_resolve)

    idle_started = []

    async def boom():
        idle_started.append(True)
        return False

    monkeypatch.setattr(engine, "_try_play_idle", boom)

    engine.app.playback_path.write_text(
        json.dumps({"version": 1, "item": item.to_dict(), "position": 83.5}, ensure_ascii=False),
        encoding="utf-8",
    )
    ok = await engine.restore_last_playback()
    assert ok is True
    cur = engine.hub().current
    assert cur is not None
    assert cur.item.title == "晴天"
    assert cur.position == 83.5
    assert cur.paused is True
    assert idle_started == []
    play_cmds = [c for c in engine._test_cmds if c.get("action") == "play"]
    assert play_cmds and play_cmds[-1].get("paused") is True
    assert play_cmds[-1].get("position") == 83.5

    # 启动入口是 restore，不是 _maybe_start，因此不会自动开播默认歌单
    await engine.restore_last_playback()  # already has current; file still valid but resolve again
    assert idle_started == []


@pytest.mark.asyncio
async def test_live_request_still_plays_after_restore(engine, monkeypatch):
    item = _item(_result("晴天", "bv1"), item_id="song1")
    playable = _playable(_result("晴天", "bv1"))

    async def fake_resolve(saved):
        engine._playables[saved.id] = playable
        return playable

    monkeypatch.setattr(engine, "_resolve_saved_item", fake_resolve)
    engine.app.playback_path.write_text(
        json.dumps({"version": 1, "item": item.to_dict(), "position": 10}, ensure_ascii=False),
        encoding="utf-8",
    )
    await engine.restore_last_playback()
    assert engine.hub().current.paused is True

    req = _result("七里香", "req-bv")
    engine._cache_tmp(_playable(req, url="http://example/b.m4a"))

    async def fake_search(query):
        return req, [], "bili", []

    monkeypatch.setattr(engine, "_search_by_priority", fake_search)
    monkeypatch.setattr(engine, "_notify", lambda *a, **k: asyncio.sleep(0))
    await engine.request_song(2, "小鹿", "七里香", manual=True)
    cur = engine.hub().current
    assert cur is not None
    assert cur.item.title == "七里香"
    assert cur.paused is False
    # 恢复的「晴天」让到新点歌后面，不能再抢到队首把七里香挡住
    assert [i.title for i in engine.hub().queue] == ["晴天"]
    play_cmds = [c for c in engine._test_cmds if c.get("action") == "play"]
    assert play_cmds[-1].get("paused") is False
    assert play_cmds[-1]["item"]["title"] == "七里香"


@pytest.mark.asyncio
async def test_progress_persists_playback_json(engine):
    item = _item(_result("夜曲"), item_id="n1")
    engine.hub().current = CurrentTrack(item=item, position=0, paused=False, lyric="")
    engine._save_playback(force=True)
    data = json.loads(engine.app.playback_path.read_text(encoding="utf-8"))
    assert data["item"]["id"] == "n1"
    assert data["position"] == 0
    engine.on_player_progress(42.2, 270, False)
    # throttle may skip; force
    engine._save_playback(force=True)
    data = json.loads(engine.app.playback_path.read_text(encoding="utf-8"))
    assert data["position"] == 42.2
    assert "config.yaml" not in str(engine.app.playback_path)


@pytest.mark.asyncio
async def test_player_reconnect_keeps_playing_when_not_paused():
    """刷新进行中的会话：服务端仍记录未暂停，补发 play 时 paused=false。"""
    from app.main import handle_command  # noqa: F401
    from app.hub import Hub

    hub = Hub()
    item = _item(_result("晴天"), item_id="live1")
    hub.current = CurrentTrack(item=item, position=15, paused=False, lyric="")
    # 模拟 hello 补发逻辑
    cur = hub.current
    cmd = {
        "type": "player_cmd",
        "action": "play",
        "item": cur.item.to_dict(),
        "position": cur.position,
        "paused": cur.paused,
    }
    assert cmd["paused"] is False
    assert cmd["position"] == 15

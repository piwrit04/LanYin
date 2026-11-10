import os

os.environ["OBS_SONG_SKIP_DANMAKU"] = "1"

from fastapi.testclient import TestClient

from app.main import app


def test_console_and_overlay_pages():
    with TestClient(app) as client:
        for path in ("/", "/console", "/window", "/overlay", "/player"):
            resp = client.get(path)
            assert resp.status_code == 200, path
            assert "text/html" in resp.headers.get("content-type", "")
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        state = client.get("/api/state")
        assert state.status_code == 200
        body = state.json()
        assert body["room_id"] == 1506613
        assert "sources" in body
        assert "welcome" in body["settings"]
        assert "idle_playlist" in body["settings"]
        assert "items" in body["settings"]["idle_playlist"]
        assert body["settings"]["console_theme"] == "plain"
        assert "blacklist" in body["settings"]
        assert "priority_queue" in body["settings"]


def test_log_entries_have_unique_increasing_ids():
    """日志重复回归：snapshot 与单独的 log 广播会携带同一条日志，客户端靠 id 去重。"""
    import asyncio

    from app.hub import Hub

    async def run():
        hub = Hub()
        hub.log("info", "主播 点歌：晴天")
        hub.log("info", "主播 点歌：晴天")
        snap = hub.snapshot()
        await asyncio.sleep(0)
        return snap

    snap = asyncio.run(run())
    ids = [l["id"] for l in snap["logs"]]
    assert ids == sorted(ids) and len(set(ids)) == len(ids) == 2


def test_console_dedupes_log_broadcasts():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/console.js").read_text(encoding="utf-8")
    assert "function hasLogEntry" in js
    assert "if (!hasLogEntry(state.logs, msg.payload))" in js

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
        assert body["settings"]["console_theme"] == "wine"
        assert "blacklist" in body["settings"]
        assert "priority_queue" in body["settings"]

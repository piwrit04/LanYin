import os
from pathlib import Path

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


def test_console_v36_quiet_player_structure():
    """v36：大方形封面替代黑胶、整页光晕、单一状态点、底部日志条、播放失败提示、去掉「默认歌单」标签。"""
    root = Path(__file__).resolve().parents[1] / "app/static"
    html = (root / "console.html").read_text(encoding="utf-8")
    js = (root / "js/console.js").read_text(encoding="utf-8")
    css = (root / "css/console.css").read_text(encoding="utf-8")
    assert "console.css?v=45" in html and "console.js?v=45" in html
    assert "brand-meta" in html and "1.0.2" in html
    assert "about-foot" in html and "星澪" in html and "杨雅诗" not in html and "小糕栗" not in html
    assert "vinyl" not in html and 'class="cover-stage album"' in html and 'id="cover"' in html
    # 旧状态 pill 的 id 仍在（JS 依赖），但收进了一个状态浮层
    for pid in ("pill-danmaku", "pill-login", "pill-player", "pill-room"):
        assert f'id="{pid}"' in html
    assert 'id="status-summary"' in html and 'id="status-pop"' in html
    # 光晕是整页固定的一层，不在播放区卡片里，不用 filter/backdrop-filter
    assert html.index('id="cover-glow"') < html.index('class="app"')
    assert "backdrop-filter:" not in css
    assert "position: fixed;\n  inset: -40px;" in css
    # 日志：底部一条 + 可展开面板，#logs 保留
    assert 'id="log-strip"' in html and 'id="log-panel"' in html and 'id="logs"' in html
    assert "function setLogOpen" in js and "function renderLogStrip" in js
    # 播放失败提示：自动消失、最多 3 条
    assert "function showToast" in js and "const TOAST_MAX = 2" in js and 'id="toasts"' in html
    # 列表不再出现「默认歌单」标签
    assert "tag-idle\">默认歌单" not in js
    # 空状态示例是「例如」后的示例文字
    assert "例如" in html and "例如" in js


def test_console_v38_playlist_actions_and_tag():
    root = Path(__file__).resolve().parents[1] / "app/static"
    js = (root / "js/console.js").read_text(encoding="utf-8")
    css = (root / "css/console.css").read_text(encoding="utf-8")
    assert "上移" not in js and "下移" not in js
    assert "置顶" in js and "置底" in js
    assert "立刻播放" in js and "下一首播放" in js
    assert 'data-move="top"' in js and 'data-move="bottom"' in js
    assert 'data-play="now"' in js and 'data-play="next"' in js
    assert "function bindRowActions" in js
    assert "function applyOutputMute" in js
    # 「点歌」仍在时长左侧的固定标记列，不插进标题行
    assert '<div class="q-line"><span class="q-title">${esc(item.title)}</span>${item.artist ? `<span class="q-artist">${esc(item.artist)}</span>` : ""}</div>' in js
    assert '<span class="q-tags">${tag}</span>' in js
    assert js.index('<span class="q-tags">${tag}</span>') > js.index('class="q-line"')
    assert "标记列" in css
    assert "minmax(0, 1fr) 96px 148px" in css
    assert ".q-line .q-tags" not in css


def test_console_v39_room_link_cookie_help_and_save_toasts():
    """v39：直播间链接跟房间号走、Cookie 用网络面板教程、保存/清除有成功失败提示。"""
    root = Path(__file__).resolve().parents[1] / "app/static"
    html = (root / "console.html").read_text(encoding="utf-8")
    js = (root / "js/console.js").read_text(encoding="utf-8")
    css = (root / "css/console.css").read_text(encoding="utf-8")
    assert 'id="bili-room-link"' in html
    assert "live.bilibili.com/1506613" not in html
    assert "function syncBiliRoomLink" in js
    assert 'link.href = "https://live.bilibili.com/" + id' in js
    assert 'roomIdInput.addEventListener("input", syncBiliRoomLink)' in js
    assert 'roomIdInput.addEventListener("change", syncBiliRoomLink)' in js
    assert "syncBiliRoomLink();" in js

    start = html.index('class="login-help"')
    end = html.index("</details>", start)
    help_html = html[start:end]
    assert "网络" in help_html and "Network" in help_html
    assert "请求标头" in help_html or "请求头" in help_html
    assert "MUSIC_U" in help_html and "qm_keyst" in help_html and "KuGoo" in help_html
    # 教程步骤不再让人去应用程序面板复制（示意图里未选中的标签不算教程正文）
    steps_start = help_html.index("<ol")
    steps = help_html[steps_start:]
    assert "应用程序" not in steps and "Application" not in steps
    assert "devtools-chromium.svg" in help_html and "devtools-firefox.svg" in help_html
    help_dir = root / "img/help"
    assert (help_dir / "devtools-chromium.svg").is_file()
    assert (help_dir / "devtools-firefox.svg").is_file()
    cr = (help_dir / "devtools-chromium.svg").read_text(encoding="utf-8")
    ff = (help_dir / "devtools-firefox.svg").read_text(encoding="utf-8")
    assert "MUSIC_U=…" in cr and "请求标头" in cr and "网络" in cr
    assert "qm_keyst=…" in ff and "请求头" in ff and "网络" in ff

    assert 'id="toasts"' in html
    assert "position: fixed" in css
    assert "toast-success" in css and 'level === "success"' in js
    assert "Cookie 已保存" in js and "Cookie 保存失败" in js
    assert "Cookie 已清除" in js and "Cookie 清除失败" in js
    assert "设置已保存" in js and "设置保存失败" in js
    assert "文案已保存" in js and "文案保存失败" in js
    assert "function sendAndAck" in js and "function postCookies" in js
    assert "请先粘贴 Cookie" in js
    assert "btnSave.onclick = async" in js
    assert "function optionalNumber" in js
    assert "function sourcePriorityIds" in js
    assert "function commitSourceOrder" in js
    assert "sourcesDragging" in js
    assert "scheduleOverlaySave" in js
    assert '{ overlay: readOverlayForm() }' in js


def test_update_settings_websocket_ack(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    calls = []

    async def fake_update(incoming):
        calls.append(incoming)
        if str(incoming.get("room_id")) == "bad":
            raise ValueError("房间号无效")

    monkeypatch.setattr(main, "update_settings", fake_update)

    def wait_ack(ws, request_id, limit=12):
        for _ in range(limit):
            msg = ws.receive_json()
            if msg.get("type") == "ack" and msg.get("request_id") == request_id:
                return msg
        raise AssertionError(f"no ack for {request_id}")

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({
                "type": "command",
                "action": "update_settings",
                "request_id": "ok1",
                "settings": {"selection_timeout_seconds": 15},
            })
            msg = wait_ack(ws, "ok1")
            assert msg["ok"] is True
            assert calls and calls[0]["selection_timeout_seconds"] == 15
            ws.send_json({
                "type": "command",
                "action": "update_settings",
                "request_id": "bad1",
                "settings": {"room_id": "bad"},
            })
            msg = wait_ack(ws, "bad1")
            assert msg["ok"] is False
            assert "房间号无效" in msg["error"]


def test_cookie_api_reports_ok_and_parse_error(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    stored = {}

    def fake_set(platform, entry):
        if entry:
            stored[platform] = entry
        else:
            stored.pop(platform, None)

    monkeypatch.setattr(main, "set_platform", fake_set)
    monkeypatch.setattr(main, "get_platform", lambda p: dict(stored.get(p) or {}))

    async def noop_push():
        return None

    monkeypatch.setattr(main.state.engine, "push_state", noop_push)

    with TestClient(main.app) as client:
        ok = client.post("/api/cookies/netease", json={"cookies": "MUSIC_U=demo"})
        assert ok.status_code == 200 and ok.json()["ok"] is True
        assert stored["netease"]["cookies"]["MUSIC_U"] == "demo"
        bad = client.post("/api/cookies/netease", json={"cookies": "not-a-cookie"})
        assert bad.status_code == 400 and bad.json()["ok"] is False
        assert "无法解析" in bad.json()["error"]
        cleared = client.post("/api/cookies/netease", json={"cookies": ""})
        assert cleared.status_code == 200 and cleared.json()["ok"] is True
        assert "netease" not in stored


def _isolated_config(tmp_path, monkeypatch):
    """Point save_config at a temp file so tests never rewrite the real config.yaml."""
    from app import main, settings

    path = tmp_path / "config.yaml"
    monkeypatch.setattr(settings, "CONFIG_PATH", path)
    # Start from current in-memory defaults / loaded values, but persist only to tmp.
    cfg = dict(main.state.config)
    settings.save_config(cfg)
    main.state.config = settings.load_config()
    return settings, main


def test_update_settings_ack_does_not_wait_for_slow_side_effects(tmp_path, monkeypatch):
    import asyncio
    import time

    from fastapi.testclient import TestClient

    settings, main = _isolated_config(tmp_path, monkeypatch)
    started = {"danmaku": False, "maybe": False}
    release = asyncio.Event()

    async def slow_danmaku():
        started["danmaku"] = True
        await release.wait()

    async def slow_maybe_start():
        started["maybe"] = True
        await release.wait()

    def wait_ack(ws, request_id, limit=20):
        for _ in range(limit):
            msg = ws.receive_json()
            if msg.get("type") == "ack" and msg.get("request_id") == request_id:
                return msg
        raise AssertionError(f"no ack for {request_id}")

    original_room = int(main.state.config["room_id"])
    new_room = original_room + 17
    with TestClient(main.app) as client:
        # Patch only after startup, so lifespan is not blocked by the slow stubs.
        monkeypatch.setattr(main, "start_danmaku", slow_danmaku)
        monkeypatch.setattr(main.state.engine, "_maybe_start", slow_maybe_start)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            t0 = time.monotonic()
            ws.send_json({
                "type": "command",
                "action": "update_settings",
                "request_id": "slow1",
                "settings": {"room_id": new_room, "selection_timeout_seconds": 18},
            })
            msg = wait_ack(ws, "slow1")
            elapsed = time.monotonic() - t0
            assert msg["ok"] is True
            assert elapsed < 2, f"ack blocked on side effects: {elapsed:.2f}s"
            assert int(main.state.config["room_id"]) == new_room
            assert int(settings.load_config()["room_id"]) == new_room
            assert int(main.state.config["selection_timeout_seconds"]) == 18
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not started["danmaku"]:
                time.sleep(0.05)
            # start_danmaku is awaited before _maybe_start, so only danmaku
            # has begun while it is still blocked.
            assert started["danmaku"] is True
            assert started["maybe"] is False
            # A later save that does not change the room should still ack
            # even if _maybe_start hangs.
            t1 = time.monotonic()
            ws.send_json({
                "type": "command",
                "action": "update_settings",
                "request_id": "slow2",
                "settings": {"selection_timeout_seconds": 22},
            })
            msg2 = wait_ack(ws, "slow2")
            assert msg2["ok"] is True
            assert time.monotonic() - t1 < 2
            assert int(main.state.config["selection_timeout_seconds"]) == 22
            # The first side-effect task is still blocked in start_danmaku,
            # so this second _maybe_start runs on a new task.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not started["maybe"]:
                time.sleep(0.05)
            assert started["maybe"] is True
            release.set()


def test_sources_priority_reorder_persists(tmp_path, monkeypatch):
    settings, main = _isolated_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main.state.hub, "log", lambda *a, **k: None)
    new_order = ["netease", "bili", "qq", "qishui", "kugou"]
    main.apply_and_save_settings({"sources_priority": new_order})
    assert list(main.state.config["sources_priority"]) == new_order
    assert list(settings.load_config()["sources_priority"]) == new_order
    assert list(main.state.public_extra()["settings"]["sources_priority"]) == new_order


def test_update_settings_empty_numeric_fields_do_not_wipe(tmp_path, monkeypatch):
    settings, main = _isolated_config(tmp_path, monkeypatch)
    cfg = main.state.config
    cfg["room_id"] = 1506613
    cfg["selection_timeout_seconds"] = 30
    cfg["anti_spam"]["max_pending_per_user"] = 2
    cfg["anti_spam"]["max_queue_size"] = 30
    cfg["anti_spam"]["cooldown_seconds"] = 60
    settings.save_config(cfg)
    main.state.config = settings.load_config()
    monkeypatch.setattr(main.state.hub, "log", lambda *a, **k: None)

    # Empty / invalid values must not raise or overwrite existing numbers.
    incoming = {
        "room_id": None,
        "selection_timeout_seconds": "",
        "anti_spam": {
            "max_pending_per_user": None,
            "max_queue_size": "abc",
            "cooldown_seconds": float("nan"),
        },
        "selection": {"timeout_action": "skip"},
    }
    apply = main.apply_and_save_settings
    apply(incoming)
    saved = settings.load_config()
    assert saved["room_id"] == 1506613
    assert saved["selection_timeout_seconds"] == 30
    assert saved["anti_spam"]["max_pending_per_user"] == 2
    assert saved["anti_spam"]["max_queue_size"] == 30
    assert saved["anti_spam"]["cooldown_seconds"] == 60

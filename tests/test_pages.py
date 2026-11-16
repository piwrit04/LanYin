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
    assert "console.css?v=38" in html and "console.js?v=38" in html
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

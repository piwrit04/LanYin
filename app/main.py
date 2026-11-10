from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.danmaku.client import BiliDanmakuClient
from app.danmaku import qrlogin, sender as danmaku_sender
from app.engine import SongEngine
from app.hub import Hub
from app.http_util import DEFAULT_UA
from app.secrets_store import get_platform, set_platform
from app.settings import DATA_DIR, ensure_dirs, load_config, save_config
from app.sources.registry import all_adapters

STATIC_DIR = Path(__file__).resolve().parent / "static"


class AppState:
    def __init__(self) -> None:
        ensure_dirs()
        self.config = load_config()
        self.hub = Hub()
        self.engine = SongEngine(self)
        self.danmaku: Optional[BiliDanmakuClient] = None
        self.real_room_id: int = int(self.config["room_id"])

    def public_extra(self) -> dict[str, Any]:
        sources = []
        pri = self.config.get("sources_priority") or []
        adapters = all_adapters()
        for sid in pri:
            ad = adapters.get(sid)
            if ad:
                sources.append(ad.public_info())
        return {
            "room_id": int(self.config["room_id"]),
            "real_room_id": int(self.real_room_id or self.config["room_id"]),
            "bili_logged_in": danmaku_sender.is_logged_in(),
            "can_send_danmaku": danmaku_sender.is_logged_in(),
            "sources": sources,
            "settings": {
                "sources_priority": list(pri),
                "selection_timeout_seconds": int(self.config["selection_timeout_seconds"]),
                "selection": dict(self.config.get("selection") or {"timeout_action": "skip"}),
                "anti_spam": dict(self.config["anti_spam"]),
                "host": self.config.get("host") or "127.0.0.1",
                "port": int(self.config.get("port") or 8765),
                "welcome": dict(self.config.get("welcome") or {"enabled": True, "extra_templates": []}),
                "idle_playlist": dict(self.config.get("idle_playlist") or {"enabled": False, "loop": True, "resume_delay_seconds": 5, "items": []}),
                "overlay": dict(self.config.get("overlay") or {}),
                "messages": dict(self.config.get("messages") or {}),
                "console_theme": self.config.get("console_theme") or "plain",
                "priority_queue": dict(self.config.get("priority_queue") or {"enabled": True}),
                "blacklist": dict(self.config.get("blacklist") or {"users": [], "songs": []}),
            },
            "next": self.engine.peek_next() if getattr(self, "engine", None) else None,
            "version": __version__,
        }


state = AppState()


def _on_danmaku_status(level: str, message: str) -> None:
    state.hub.danmaku_connected = "已连接" in message
    state.hub.log(level, message)
    asyncio.create_task(state.engine.push_state())


async def start_danmaku() -> None:
    import os
    if os.environ.get("OBS_SONG_SKIP_DANMAKU") == "1":
        state.hub.log("info", "已跳过弹幕连接（测试模式）")
        return
    room_id = int(state.config["room_id"])
    if state.danmaku:
        await state.danmaku.stop()
    client = BiliDanmakuClient(
        room_id,
        on_danmaku=state.engine.on_danmaku,
        on_status=_on_danmaku_status,
        on_enter=state.engine.on_enter,
    )
    state.danmaku = client
    await client.start()
    try:
        state.real_room_id = await client.resolve_room()
    except Exception:
        state.real_room_id = room_id


def _write_pid() -> None:
    try:
        ensure_dirs()
        import os
        (DATA_DIR / "server.pid").write_text(str(os.getpid()), encoding="utf-8")
        (DATA_DIR / "server.ready").write_text("1", encoding="utf-8")
    except Exception:
        pass


def _clear_pid() -> None:
    # 只清理自己写的标记。否则第二个实例绑端口失败退出时，会把正在运行的那个实例的
    # server.pid / server.ready 删掉，OBS 脚本就以为澜音没在跑、也停不掉它。
    import os
    try:
        owner = (DATA_DIR / "server.pid").read_text(encoding="utf-8").strip()
    except Exception:
        owner = ""
    if owner and owner != str(os.getpid()):
        return
    for name in ("server.pid", "server.ready", "launch.pid"):
        try:
            path = DATA_DIR / name
            if path.exists():
                path.unlink()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    _write_pid()
    state.config = load_config()
    state.hub.log("info", f"澜音启动，默认房间号 {state.config['room_id']}")
    await start_danmaku()
    await state.engine._maybe_start()
    try:
        yield
    finally:
        if state.danmaku:
            await state.danmaku.stop()
        _clear_pid()


app = FastAPI(title="澜音", version=__version__, lifespan=lifespan)
NO_STORE_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC_DIR / name, headers=NO_STORE_HEADERS)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return _page("console.html")


@app.get("/console")
async def console_page():
    return _page("console.html")


@app.get("/window")
async def console_window_page():
    return _page("console.html")


@app.get("/overlay")
async def overlay_page():
    return _page("overlay.html")


@app.get("/player")
async def player_page():
    return _page("player.html")


@app.get("/api/state")
async def api_state():
    return state.hub.snapshot(state.public_extra())


@app.get("/api/health")
async def api_health():
    return {"ok": True, "app": "lanyin", "version": __version__, "room_id": state.config["room_id"]}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    role = "unknown"
    try:
        await ws.send_json({"type": "state", "payload": state.hub.snapshot(state.public_extra())})
        while True:
            data = await ws.receive_json()
            typ = data.get("type")
            if typ == "hello":
                role = str(data.get("role") or "unknown")
                await state.hub.register(ws, role)
                if role in {"player", "console", "console_window"}:
                    if role == "player":
                        state.hub.player_seen_at = __import__("time").time()
                    # 若已有当前曲，补发一次播放指令
                    cur = state.hub.current
                    if cur:
                        await ws.send_json(
                            {
                                "type": "player_cmd",
                                "action": "play",
                                "item": cur.item.to_dict(),
                                "stream_url": f"/api/stream/{cur.item.id}",
                                "lyric": cur.lyric or "",
                                "position": cur.position,
                                "paused": cur.paused,
                            }
                        )
                await ws.send_json({"type": "state", "payload": state.hub.snapshot(state.public_extra())})
            elif typ == "command":
                await handle_command(data)
            elif typ == "player":
                await handle_player_event(data)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await state.hub.unregister(ws)


async def handle_command(data: dict[str, Any]) -> None:
    action = data.get("action")
    if action == "pause":
        await state.engine.pause()
    elif action == "resume":
        await state.engine.resume()
    elif action == "skip":
        await state.engine.skip()
    elif action == "previous":
        await state.engine.previous()
    elif action == "delete":
        await state.engine.delete_item(str(data.get("item_id") or ""))
    elif action == "clear":
        await state.engine.clear_queue()
    elif action == "queue_reorder":
        ids = data.get("ids") or []
        if isinstance(ids, list):
            await state.engine.reorder_queue([str(i) for i in ids])
    elif action == "choose":
        await state.engine.try_choose(
            uid=0,
            uname="主播",
            choice=int(data.get("choice") or 0),
            from_danmaku=False,
            force=True,
        )
    elif action == "cancel_selection":
        if state.hub.selection:
            await state.engine._cancel_selection_task()
            state.hub.selection = None
            state.hub.log("info", "主播取消了选歌")
            await state.engine.push_state()
    elif action == "request":
        query = str(data.get("query") or "").strip()
        if query:
            await state.engine.request_song(0, "主播", query, manual=True)
    elif action == "update_settings":
        await update_settings(data.get("settings") or {})
    elif action == "reconnect_danmaku":
        await start_danmaku()
        await state.engine.push_state()
    elif action == "idle_add":
        text_in = str(data.get("text") or data.get("query") or "").strip()
        if text_in:
            try:
                await state.engine.add_idle_item(text_in)
            except ValueError as exc:
                state.hub.log("warn", str(exc))
                await state.engine.push_state()
    elif action == "idle_remove":
        try:
            idx = data.get("index")
            await state.engine.remove_idle_item(
                item_id=str(data.get("id") or data.get("item_id") or ""),
                index=int(idx) if idx is not None and str(idx) != "" else None,
            )
        except ValueError as exc:
            state.hub.log("warn", str(exc))
            await state.engine.push_state()
    elif action == "idle_reorder":
        ids = data.get("ids") or []
        if isinstance(ids, list):
            await state.engine.reorder_idle_items([str(i) for i in ids])
    elif action == "idle_add_current":
        try:
            await state.engine.add_current_to_idle()
        except ValueError as exc:
            state.hub.log("warn", str(exc))
            await state.engine.push_state()
    elif action == "blacklist_current":
        try:
            await state.engine.blacklist_current_song()
        except ValueError as exc:
            state.hub.log("warn", str(exc))
            await state.engine.push_state()
    elif action == "blacklist_song":
        try:
            await state.engine.add_blacklisted_song(str(data.get("title") or data.get("text") or ""))
        except ValueError as exc:
            state.hub.log("warn", str(exc))
            await state.engine.push_state()
    elif action == "unblacklist_song":
        try:
            await state.engine.remove_blacklisted_song(str(data.get("title") or data.get("text") or ""))
        except ValueError as exc:
            state.hub.log("warn", str(exc))
            await state.engine.push_state()
    elif action == "blacklist_user":
        try:
            await state.engine.add_blacklisted_user(
                uid=int(data.get("uid") or 0),
                name=str(data.get("name") or data.get("uname") or data.get("text") or ""),
            )
        except (TypeError, ValueError) as exc:
            state.hub.log("warn", str(exc))
            await state.engine.push_state()
    elif action == "unblacklist_user":
        try:
            await state.engine.remove_blacklisted_user(
                uid=int(data.get("uid") or 0),
                name=str(data.get("name") or data.get("uname") or data.get("text") or ""),
            )
        except (TypeError, ValueError) as exc:
            state.hub.log("warn", str(exc))
            await state.engine.push_state()


async def handle_player_event(data: dict[str, Any]) -> None:
    ev = data.get("event")
    if ev == "progress":
        state.hub.player_seen_at = __import__("time").time()
        state.engine.on_player_progress(
            float(data.get("position") or 0),
            float(data.get("duration") or 0),
            bool(data.get("paused")),
        )
        # 进度节流广播，避免刷屏
        await state.hub.broadcast(
            {
                "type": "progress",
                "payload": {
                    "position": float(data.get("position") or 0),
                    "duration": float(data.get("duration") or 0),
                    "paused": bool(data.get("paused")),
                    "lyric_line": data.get("lyric_line") or "",
                },
            }
        )
    elif ev == "ended":
        await state.engine.on_player_ended()
    elif ev == "ready":
        state.hub.player_seen_at = __import__("time").time()
        await state.engine.push_state()
    elif ev == "visualizer":
        raw = data.get("bins") or []
        if not isinstance(raw, list):
            return
        bins = []
        for x in raw[:48]:
            try:
                bins.append(max(0, min(100, int(x))))
            except (TypeError, ValueError):
                bins.append(0)
        await state.hub.send_to_role(
            "overlay",
            {"type": "visualizer", "bins": bins, "paused": bool(data.get("paused"))},
        )
    elif ev == "error":
        state.hub.log("error", f"播放器报错：{data.get('message') or '未知错误'}")
        await state.engine.skip()


async def update_settings(incoming: dict[str, Any]) -> None:
    cfg = state.config
    old_room = int(cfg["room_id"])
    if "room_id" in incoming and incoming["room_id"]:
        cfg["room_id"] = int(incoming["room_id"])
    if "selection_timeout_seconds" in incoming:
        cfg["selection_timeout_seconds"] = max(10, int(incoming["selection_timeout_seconds"]))
    if "sources_priority" in incoming and incoming["sources_priority"]:
        cfg["sources_priority"] = list(incoming["sources_priority"])
    if "anti_spam" in incoming and isinstance(incoming["anti_spam"], dict):
        cfg["anti_spam"].update(incoming["anti_spam"])
    if "selection" in incoming and isinstance(incoming["selection"], dict):
        cfg.setdefault("selection", {"timeout_action": "skip"})
        action = str(incoming["selection"].get("timeout_action") or "").strip().lower()
        if action in ("play_first", "skip"):
            cfg["selection"]["timeout_action"] = action
    if "timeout_action" in incoming:
        action = str(incoming.get("timeout_action") or "").strip().lower()
        if action in ("play_first", "skip"):
            cfg.setdefault("selection", {"timeout_action": "skip"})
            cfg["selection"]["timeout_action"] = action
    if "welcome" in incoming and isinstance(incoming["welcome"], dict):
        cfg.setdefault("welcome", {"enabled": True, "extra_templates": []})
        if "enabled" in incoming["welcome"]:
            cfg["welcome"]["enabled"] = bool(incoming["welcome"]["enabled"])
        if "extra_templates" in incoming["welcome"]:
            raw = incoming["welcome"]["extra_templates"]
            if isinstance(raw, str):
                raw = raw.splitlines()
            if isinstance(raw, list):
                cfg["welcome"]["extra_templates"] = [str(x).strip() for x in raw if str(x).strip()]
    if "overlay" in incoming and isinstance(incoming["overlay"], dict):
        cfg.setdefault("overlay", {})
        cfg["overlay"].update(incoming["overlay"])
    if "messages" in incoming and isinstance(incoming["messages"], dict):
        cfg.setdefault("messages", {})
        for key, value in incoming["messages"].items():
            if value is None:
                continue
            text_v = str(value).strip()
            if text_v:
                cfg["messages"][key] = text_v
    if "idle_playlist" in incoming and isinstance(incoming["idle_playlist"], dict):
        cur = cfg.setdefault("idle_playlist", {"enabled": False, "loop": True, "resume_delay_seconds": 5, "items": []})
        ip = incoming["idle_playlist"]
        if "enabled" in ip:
            cur["enabled"] = bool(ip["enabled"])
        if "loop" in ip:
            cur["loop"] = bool(ip["loop"])
        if "resume_delay_seconds" in ip:
            try:
                cur["resume_delay_seconds"] = max(0, min(120, int(ip["resume_delay_seconds"])))
            except (TypeError, ValueError):
                pass
        if "items" in ip and isinstance(ip["items"], list):
            from app.settings import normalize_idle_items

            cur["items"] = normalize_idle_items(ip["items"])
    if "console_theme" in incoming:
        from app.queue_rules import normalize_theme

        cfg["console_theme"] = normalize_theme(incoming.get("console_theme"))
    if "priority_queue" in incoming and isinstance(incoming["priority_queue"], dict):
        from app.queue_rules import normalize_priority_queue

        cur_pq = cfg.setdefault("priority_queue", {"enabled": True})
        merged = dict(cur_pq)
        merged.update(incoming["priority_queue"])
        cfg["priority_queue"] = normalize_priority_queue(raw=merged)
    if "blacklist" in incoming and isinstance(incoming["blacklist"], dict):
        from app.queue_rules import normalize_blacklist

        cur_bl = cfg.setdefault("blacklist", {"users": [], "songs": []})
        merged_bl = {
            "users": incoming["blacklist"]["users"] if "users" in incoming["blacklist"] else cur_bl.get("users") or [],
            "songs": incoming["blacklist"]["songs"] if "songs" in incoming["blacklist"] else cur_bl.get("songs") or [],
        }
        cfg["blacklist"] = normalize_blacklist(raw=merged_bl)
    save_config(cfg)
    state.config = load_config()
    state.hub.log("info", "已保存设置")
    if int(state.config["room_id"]) != old_room:
        state.hub.log("info", f"房间号已改为 {state.config['room_id']}，正在重新连接弹幕")
        await start_danmaku()
    # 打开默认歌单且当前空闲时，开始播放
    await state.engine._maybe_start()
    await state.engine.push_state()


@app.post("/api/settings")
async def api_settings(request: Request):
    body = await request.json()
    await update_settings(body)
    return {"ok": True, "settings": state.public_extra()["settings"]}


@app.post("/api/request")
async def api_request(request: Request):
    body = await request.json()
    query = str(body.get("query") or "").strip()
    if not query:
        return JSONResponse({"ok": False, "error": "请填写歌名"}, status_code=400)
    await state.engine.request_song(0, "主播", query, manual=True)
    return {"ok": True}


@app.post("/api/idle_playlist/add")
async def api_idle_add(request: Request):
    body = await request.json()
    text_in = str(body.get("text") or body.get("query") or "").strip()
    if not text_in:
        return JSONResponse({"ok": False, "error": "请填写歌名或哔哩哔哩视频链接"}, status_code=400)
    try:
        item = await state.engine.add_idle_item(text_in)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "item": item, "idle_playlist": state.config.get("idle_playlist")}


@app.post("/api/idle_playlist/remove")
async def api_idle_remove(request: Request):
    body = await request.json()
    try:
        idx = body.get("index")
        await state.engine.remove_idle_item(
            item_id=str(body.get("id") or body.get("item_id") or ""),
            index=int(idx) if idx is not None and str(idx) != "" else None,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "idle_playlist": state.config.get("idle_playlist")}


@app.post("/api/idle_playlist/reorder")
async def api_idle_reorder(request: Request):
    body = await request.json()
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        return JSONResponse({"ok": False, "error": "缺少排序列表"}, status_code=400)
    await state.engine.reorder_idle_items([str(i) for i in ids])
    return {"ok": True, "idle_playlist": state.config.get("idle_playlist")}


@app.post("/api/queue/reorder")
async def api_queue_reorder(request: Request):
    body = await request.json()
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        return JSONResponse({"ok": False, "error": "缺少排序列表"}, status_code=400)
    await state.engine.reorder_queue([str(i) for i in ids])
    return {"ok": True}


@app.post("/api/console_window")
async def api_console_window():
    """弹出独立控制台窗口（本机 Edge/Chrome 应用窗口）。"""
    import os
    import subprocess
    import sys

    script = Path(__file__).resolve().parent.parent / "open_console_window.py"
    if not script.exists():
        return JSONResponse({"ok": False, "error": "找不到窗口启动文件"}, status_code=500)
    kwargs: dict[str, Any] = {"cwd": str(script.parent), "close_fds": True}
    if os.name == "nt":
        flags = 0
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0)
        flags |= 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
        kwargs["creationflags"] = flags
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    subprocess.Popen([sys.executable, str(script)], **kwargs)
    return {"ok": True}


@app.post("/api/simulate_danmaku")
async def api_simulate(request: Request):
    """本机测试用：模拟一条弹幕，不经过直播间。"""
    body = await request.json()
    event = {
        "uid": int(body.get("uid") or 10001),
        "uname": str(body.get("uname") or "测试观众"),
        "text": str(body.get("text") or ""),
        "ts": __import__("time").time(),
    }
    await state.engine.on_danmaku(event)
    return {"ok": True}


@app.get("/api/bili/qr/start")
async def bili_qr_start():
    try:
        data = await qrlogin.start_login()
        return {"ok": True, **data}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/bili/qr/poll")
async def bili_qr_poll(qrcode_key: str):
    try:
        data = await qrlogin.poll_login(qrcode_key)
        if data.get("status") == "ok":
            await state.engine.push_state()
        return {"ok": True, **data}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/bili/logout")
async def bili_logout():
    await qrlogin.logout()
    state.hub.log("info", "已退出 B 站登录")
    await state.engine.push_state()
    return {"ok": True}


@app.post("/api/cookies/{platform}")
async def save_cookies(platform: str, request: Request):
    if platform not in {"bilibili", "qq", "netease", "kugou", "qishui"}:
        return JSONResponse({"ok": False, "error": "不支持的平台"}, status_code=400)
    body = await request.json()
    raw = str(body.get("cookies") or "").strip()
    if not raw:
        set_platform(platform, None)
        state.hub.log("info", f"已清除 {platform} 登录信息")
        await state.engine.push_state()
        return {"ok": True}
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        cookies[k.strip()] = v.strip()
    if not cookies:
        return JSONResponse({"ok": False, "error": "无法解析 Cookie"}, status_code=400)
    entry = get_platform(platform)
    entry["cookies"] = cookies
    set_platform(platform, entry)
    state.hub.log("info", f"已保存 {platform} 登录信息（仅本机）")
    await state.engine.push_state()
    return {"ok": True}


@app.get("/api/stream/{item_id}")
async def stream_audio(item_id: str, request: Request):
    playable = state.engine.get_playable(item_id)
    if not playable or not playable.url:
        # 当前曲也可能在 current 里
        cur = state.hub.current
        if cur and cur.item.id == item_id:
            playable = state.engine.get_playable(item_id)
        if not playable or not playable.url:
            return JSONResponse({"error": "找不到可播地址"}, status_code=404)

    headers = dict(playable.headers or {})
    headers.setdefault("User-Agent", DEFAULT_UA)
    if rng := request.headers.get("range"):
        headers["Range"] = rng

    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True)
    try:
        req = client.build_request("GET", playable.url, headers=headers)
        resp = await client.send(req, stream=True)
    except Exception as exc:
        await client.aclose()
        return JSONResponse({"error": f"拉取音源失败：{exc}"}, status_code=502)

    if resp.status_code >= 400:
        await resp.aclose()
        await client.aclose()
        return JSONResponse({"error": f"音源返回 HTTP {resp.status_code}"}, status_code=resp.status_code)

    out_headers = {}
    for key in ("content-type", "content-length", "content-range", "accept-ranges"):
        if key in resp.headers:
            out_headers[key] = resp.headers[key]
    out_headers.setdefault("accept-ranges", "bytes")
    out_headers["cache-control"] = "no-store"

    async def body():
        try:
            async for chunk in resp.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(body(), status_code=resp.status_code, headers=out_headers, media_type=out_headers.get("content-type") or "audio/mp4")


_COVER_HOSTS = (
    "hdslb.com",
    "bilibili.com",
    "bilivideo.com",
    "gtimg.cn",
    "qq.com",
    "music.126.net",
    "126.net",
    "kugou.com",
    "kgimg.com",
    "douyinpic.com",
    "byteimg.com",
    "douyinvod.com",
)


def _cover_host_ok(host: str) -> bool:
    host = (host or "").lower()
    return any(host == s or host.endswith("." + s) for s in _COVER_HOSTS)


@app.get("/api/cover")
async def cover_proxy(u: str):
    """转发封面，避免哔哩哔哩等图床因缺少 Referer 在 OBS/本机页面里加载失败。"""
    raw = (u or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not _cover_host_ok(parsed.hostname or ""):
        return JSONResponse({"error": "封面地址不被允许"}, status_code=400)
    host = (parsed.hostname or "").lower()
    if host.endswith("hdslb.com") or "bili" in host:
        referer = "https://www.bilibili.com/"
    elif host.endswith("qq.com") or host.endswith("gtimg.cn"):
        referer = "https://y.qq.com/"
    elif "126.net" in host:
        referer = "https://music.163.com/"
    elif "kugou" in host or host.endswith("kgimg.com"):
        referer = "https://www.kugou.com/"
    else:
        referer = f"{parsed.scheme}://{parsed.hostname}/"
    headers = {"User-Agent": DEFAULT_UA, "Referer": referer}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(raw, headers=headers)
    except Exception as exc:
        return JSONResponse({"error": f"封面拉取失败：{exc}"}, status_code=502)
    if resp.status_code >= 400 or not resp.content:
        return JSONResponse({"error": f"封面返回 HTTP {resp.status_code}"}, status_code=resp.status_code or 502)
    media = resp.headers.get("content-type") or "image/jpeg"
    if not media.startswith("image/") and "octet-stream" not in media:
        # 有些图床不给准确类型，仍按图片返回
        media = "image/jpeg"
    return StreamingResponse(
        iter([resp.content]),
        media_type=media,
        headers={"cache-control": "public, max-age=86400"},
    )


def run() -> None:
    import os
    import sys
    import uvicorn
    from datetime import datetime

    ensure_dirs()
    log_path = DATA_DIR / "startup.log"
    log_f = None
    try:
        log_f = log_path.open("a", encoding="utf-8")
        log_f.write(f"\n==== {datetime.now().isoformat(timespec='seconds')} start pid={os.getpid()} ====\n")
        log_f.flush()
    except Exception:
        log_f = None
    # pythonw has no console; keep a log so hidden starts can be diagnosed
    if log_f is not None:
        try:
            has_tty = sys.stdout is not None and sys.stdout.isatty()
        except Exception:
            has_tty = False
        if not has_tty:
            sys.stdout = log_f
            sys.stderr = log_f

    cfg = load_config()
    from app.portcfg import probe

    port = int(cfg.get("port") or 8765)
    status = probe(port, str(cfg.get("host") or "127.0.0.1"))
    if status == "lanyin":
        # 本地版和 OBS 脚本版同时启动时，后来的那个直接复用已在运行的澜音
        print(f"澜音已经在运行（端口 {port}），不再重复启动。", flush=True)
        raise SystemExit(0)
    if status == "other":
        print(
            f"端口 {port} 已被其他程序占用，澜音无法启动。"
            f"请用记事本打开 config.yaml，把 port: {port} 改成别的数字（例如 18765）后再启动。",
            flush=True,
        )
        raise SystemExit(2)
    uvicorn.run(
        "app.main:app",
        host=str(cfg.get("host") or "127.0.0.1"),
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run()

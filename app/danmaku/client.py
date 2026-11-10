from __future__ import annotations

import asyncio
import json
import struct
import time
from typing import Any, Callable, Optional

import brotli
import httpx
import websockets

from app.http_util import DEFAULT_UA
from app.secrets_store import cookie_header
from app.danmaku.interact_pb import decode_interact_pb, find_pb_b64

ROOM_INIT = "https://api.live.bilibili.com/room/v1/Room/room_init"
DANMU_INFO = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo"
DANMU_CONF = "https://api.live.bilibili.com/room/v1/Danmu/getConf"

# 操作码
OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_SEND_SMS = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

WS_DEFAULT = "wss://broadcastlv.chat.bilibili.com/sub"


class BiliDanmakuClient:
    """监听公开直播间弹幕。通常只需房间号；有登录 Cookie 时会一并带上。"""

    def __init__(
        self,
        room_id: int,
        on_danmaku: Callable[[dict[str, Any]], Any],
        on_status: Optional[Callable[[str, str], Any]] = None,
        on_enter: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.room_id = int(room_id)
        self.real_room_id = int(room_id)
        self.on_danmaku = on_danmaku
        self.on_status = on_status
        self.on_enter = on_enter
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.connected = False

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": DEFAULT_UA, "Referer": "https://live.bilibili.com/"}
        ck = cookie_header("bilibili")
        if ck:
            headers["Cookie"] = ck
        return headers

    async def start(self) -> None:
        await self.stop()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="bili-danmaku")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.connected = False

    async def restart(self, room_id: int) -> None:
        self.room_id = int(room_id)
        await self.start()

    def _emit_status(self, level: str, message: str) -> None:
        if self.on_status:
            try:
                self.on_status(level, message)
            except Exception:
                pass

    async def _run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self._emit_status("warn", f"弹幕连接断开，{backoff} 秒后重试：{exc}")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)

    async def resolve_room(self) -> int:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                ROOM_INIT, params={"id": self.room_id}, headers=self._headers()
            )
            payload = resp.json()
            data = payload.get("data") or {}
            real = int(data.get("room_id") or self.room_id)
            self.real_room_id = real
            return real

    async def _get_danmu_info(self) -> dict[str, Any]:
        """获取弹幕服务器与 token。新接口可能需要额外校验，失败时回退到 getConf。"""
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            try:
                resp = await client.get(
                    DANMU_INFO,
                    params={"id": self.real_room_id, "type": 0},
                    headers=self._headers(),
                )
                payload = resp.json()
                data = payload.get("data") or {}
                if payload.get("code") == 0 and (data.get("token") or data.get("host_list") or data.get("host_server_list")):
                    return data
            except Exception:
                pass
            resp = await client.get(
                DANMU_CONF,
                params={"room_id": self.real_room_id, "platform": "pc", "player": "web"},
                headers=self._headers(),
            )
            payload = resp.json()
            if payload.get("code") != 0:
                raise RuntimeError(payload.get("message") or payload.get("msg") or "无法获取弹幕服务器")
            return payload.get("data") or {}

    async def _connect_once(self) -> None:
        real = await self.resolve_room()
        info = await self._get_danmu_info()
        token = info.get("token") or ""
        hosts = info.get("host_list") or info.get("host_server_list") or []
        urls = []
        for h in hosts:
            host = h.get("host")
            if not host:
                continue
            wss_port = h.get("wss_port") or 443
            urls.append(f"wss://{host}:{wss_port}/sub")
        if info.get("host") and not urls:
            port = info.get("wss_port") or 443
            urls.append(f"wss://{info['host']}:{port}/sub")
        if not urls:
            urls = [WS_DEFAULT]

        uid = _cookie_uid()
        auth = {
            "uid": uid,
            "roomid": real,
            "protover": 3,
            "platform": "web",
            "type": 2,
            "key": token,
        }

        last_error = None
        for url in urls:
            try:
                await self._run_socket(url, auth, real)
                last_error = None
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error

    async def _run_socket(self, url: str, auth: dict[str, Any], real: int) -> None:
        extra_headers = {
            "User-Agent": DEFAULT_UA,
            "Origin": "https://live.bilibili.com",
            "Referer": f"https://live.bilibili.com/{real}",
        }
        async with websockets.connect(
            url,
            additional_headers=extra_headers,
            max_size=8 * 1024 * 1024,
            ping_interval=None,
            open_timeout=15,
        ) as ws:
            await ws.send(_pack(OP_AUTH, json.dumps(auth, separators=(",", ":"))))
            hb = asyncio.create_task(self._heartbeat(ws))
            authed = False
            try:
                while not self._stop.is_set():
                    raw = await ws.recv()
                    if isinstance(raw, str):
                        raw = raw.encode("utf-8")
                    for op, body in _unpack(raw):
                        if op == OP_AUTH_REPLY:
                            authed = True
                            self.connected = True
                            self._emit_status("info", f"已连接直播间弹幕（房间 {real}）")
                            continue
                        if op == OP_HEARTBEAT_REPLY:
                            continue
                        if op == OP_SEND_SMS:
                            if not authed:
                                authed = True
                                self.connected = True
                                self._emit_status("info", f"已连接直播间弹幕（房间 {real}）")
                            self._handle_body(body)
            finally:
                hb.cancel()
                self.connected = False

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await ws.send(_pack(OP_HEARTBEAT, "[object Object]"))
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            return
        except Exception:
            return

    def _handle_body(self, body: bytes) -> None:
        try:
            text = body.decode("utf-8", errors="ignore")
            if not text:
                return
            payload = json.loads(text)
        except Exception:
            return
        cmd = payload.get("cmd") or ""
        if cmd.startswith("DANMU_MSG"):
            info = payload.get("info") or []
            try:
                msg = info[1]
                uid = int(info[2][0])
                uname = str(info[2][1])
            except Exception:
                return
            meta = parse_danmaku_meta(info)
            event = {
                "uid": uid,
                "uname": uname,
                "text": str(msg),
                "ts": time.time(),
                "raw_cmd": cmd,
                "guard_level": meta["guard_level"],
                "medal_room": meta["medal_room"],
                "medal_name": meta["medal_name"],
                "medal_level": meta["medal_level"],
            }
            try:
                result = self.on_danmaku(event)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                pass
            return
        entered = parse_enter_event(payload)
        if entered and self.on_enter:
            uid, uname = entered
            try:
                result = self.on_enter(uid, uname)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                pass




def parse_danmaku_meta(info: list[Any] | None) -> dict[str, Any]:
    """Extract guard level and worn fan medal from a DANMU_MSG info list."""
    guard_level = 0
    medal_room = 0
    medal_name = ""
    medal_level = 0
    if not isinstance(info, list):
        return {
            "guard_level": 0,
            "medal_room": 0,
            "medal_name": "",
            "medal_level": 0,
        }
    try:
        guard_level = int(info[7] or 0)
    except (TypeError, ValueError, IndexError):
        guard_level = 0
    if guard_level not in (0, 1, 2, 3):
        guard_level = 0
    try:
        medal = info[3] if len(info) > 3 else None
        if isinstance(medal, (list, tuple)) and medal:
            try:
                medal_level = int(medal[0] or 0)
            except (TypeError, ValueError, IndexError):
                medal_level = 0
            try:
                medal_name = str(medal[1] or "").strip()
            except IndexError:
                medal_name = ""
            try:
                medal_room = int(medal[3] or 0)
            except (TypeError, ValueError, IndexError):
                medal_room = 0
    except Exception:
        pass
    return {
        "guard_level": guard_level,
        "medal_room": medal_room,
        "medal_name": medal_name,
        "medal_level": medal_level,
    }


def parse_enter_event(payload: dict[str, Any]) -> Optional[tuple[int, str]]:
    """从直播间进房相关命令里取出 (uid, uname)。非进房事件返回 None。"""
    if not isinstance(payload, dict):
        return None
    cmd = str(payload.get("cmd") or "")
    base = cmd.split(":")[0].split("-")[0]
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    if base.startswith("INTERACT_WORD"):
        msg_type = _first_present(data, "msg_type")
        # 有些 V2 把字段放在 raw / extra 里
        if msg_type is None:
            for nest in ("raw", "extra", "info"):
                inner = data.get(nest)
                if isinstance(inner, dict) and "msg_type" in inner:
                    msg_type = inner.get("msg_type")
                    if not data.get("uname") and not (isinstance(data.get("uinfo"), dict)):
                        # 允许从嵌套对象取名字
                        data = {**inner, **{k: v for k, v in data.items() if v not in (None, "", {}, [])}}
                    break
        uid, uname = _uid_uname_from_data(data)
        # 2025 年起 INTERACT_WORD_V2 常用 data.pb（base64 protobuf），JSON 里没有昵称
        if not uname or msg_type is None:
            pb_b64 = find_pb_b64(data)
            decoded = decode_interact_pb(pb_b64) if pb_b64 else None
            if decoded:
                d_uid, d_name, d_type = decoded
                if not uid:
                    uid = d_uid
                if not uname:
                    uname = d_name
                if msg_type is None:
                    msg_type = d_type
        if msg_type is not None:
            try:
                if int(msg_type) != 1:
                    return None
            except (TypeError, ValueError):
                return None
        if uname:
            return uid, uname
        return None

    if base in {"WELCOME", "WELCOME_GUARD"}:
        uid, uname = _uid_uname_from_data(data)
        if uname:
            return uid, uname
        return None

    return None


def _first_present(data: dict[str, Any], *keys: str):
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
    return None


def _uid_uname_from_data(data: dict[str, Any]) -> tuple[int, str]:
    uname = str(
        data.get("uname")
        or data.get("username")
        or data.get("name")
        or ""
    ).strip()
    uid_raw = data.get("uid") or data.get("mid") or 0
    uinfo = data.get("uinfo") if isinstance(data.get("uinfo"), dict) else {}
    base = uinfo.get("base") if isinstance(uinfo.get("base"), dict) else {}
    if not uname:
        uname = str(base.get("name") or base.get("uname") or uinfo.get("name") or "").strip()
    if not uid_raw:
        uid_raw = uinfo.get("uid") or base.get("mid") or base.get("uid") or 0
    try:
        uid = int(uid_raw or 0)
    except (TypeError, ValueError):
        uid = 0
    return uid, uname


def _cookie_uid() -> int:

    ck = cookie_header("bilibili")
    for part in ck.split(";"):
        part = part.strip()
        if part.startswith("DedeUserID=") and "DedeUserID__ckMd5" not in part:
            v = part.split("=", 1)[-1].strip()
            if v.isdigit():
                return int(v)
    return 0


def _pack(op: int, body: str, protover: int = 1) -> bytes:
    payload = body.encode("utf-8")
    header = struct.pack(">IHHII", 16 + len(payload), 16, protover, op, 1)
    return header + payload


def _unpack(data: bytes) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    offset = 0
    n = len(data)
    while offset + 16 <= n:
        packet_len, header_len, protover, op, _seq = struct.unpack_from(">IHHII", data, offset)
        if packet_len < 16 or offset + packet_len > n:
            break
        body = data[offset + header_len : offset + packet_len]
        offset += packet_len
        if protover == 2:
            try:
                out.extend(_unpack(__import__("zlib").decompress(body)))
            except Exception:
                continue
        elif protover == 3:
            try:
                out.extend(_unpack(brotli.decompress(body)))
            except Exception:
                continue
        else:
            out.append((op, body))
    return out

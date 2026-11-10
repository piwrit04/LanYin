from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Optional

from fastapi import WebSocket

from app.models import CurrentTrack, QueueItem, SelectionSession


class Hub:
    """WebSocket 广播与内存状态。"""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.roles: dict[WebSocket, str] = {}
        self.logs: deque[dict[str, Any]] = deque(maxlen=200)
        self.queue: list[QueueItem] = []
        self.current: Optional[CurrentTrack] = None
        self.selection: Optional[SelectionSession] = None
        self.danmaku_connected = False
        self.player_seen_at: float = 0.0
        self._log_seq = 0
        self._lock = asyncio.Lock()

    def dedicated_player_connected(self) -> bool:
        return any(self.roles.get(ws) == "player" for ws in self.clients)

    def console_window_connected(self) -> bool:
        return any(self.roles.get(ws) == "console_window" for ws in self.clients)

    def player_connected(self) -> bool:
        if any(self.roles.get(ws) in {"player", "console", "console_window"} for ws in self.clients):
            return True
        return time.time() - self.player_seen_at < 8

    def log(self, level: str, message: str) -> None:
        # 每条日志带单调递增的 id。log() 只是排队广播，紧跟着的 push_state()
        # 往往先发出（快照里已含这条），客户端据 id 去重，避免同一行出现两次。
        self._log_seq += 1
        entry = {"id": self._log_seq, "level": level, "message": message, "ts": time.time()}
        self.logs.append(entry)
        asyncio.create_task(self.broadcast({"type": "log", "payload": entry}))

    def snapshot(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data = {
            "queue": [i.to_dict() for i in self.queue],
            "current": self.current.to_dict() if self.current else None,
            "selection": self.selection.to_dict() if self.selection else None,
            "danmaku_connected": self.danmaku_connected,
            "player_connected": self.player_connected(),
            "dedicated_player_connected": self.dedicated_player_connected(),
            "console_window_connected": self.console_window_connected(),
            "logs": list(self.logs)[-80:],
        }
        if extra:
            data.update(extra)
        return data

    async def register(self, ws: WebSocket, role: str) -> None:
        self.clients.add(ws)
        self.roles[ws] = role or "unknown"
        if role == "player":
            self.player_seen_at = time.time()

    async def unregister(self, ws: WebSocket) -> None:
        self.clients.discard(ws)
        self.roles.pop(ws, None)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(ws)

    async def send_player_cmd(self, message: dict[str, Any]) -> None:
        if self.dedicated_player_connected():
            await self.send_to_role("player", message)
            return
        if self.console_window_connected():
            await self.send_to_role("console_window", message)
            return
        await self.send_to_role("console", message)

    async def send_to_role(self, role: str, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws, r in list(self.roles.items()):
            if r != role:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(ws)

    async def broadcast_state(self, extra: dict[str, Any] | None = None) -> None:
        await self.broadcast({"type": "state", "payload": self.snapshot(extra)})

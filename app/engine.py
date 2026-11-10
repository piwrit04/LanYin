from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional

from app import ranking
from app.bili_links import parse_bili_input, resolve_bili_ref
from app.danmaku import sender as danmaku_sender
from app.models import (
    CommandKind,
    CurrentTrack,
    Playable,
    QueueItem,
    SearchResult,
    SelectionOption,
    SelectionSession,
)
from app.parser import extract_requested_source, fit_danmaku, parse_danmaku
from app.queue_rules import (
    TIER_FAN_MEDAL,
    TIER_IDLE,
    insert_index,
    priority_tier,
    song_is_blocked,
    user_is_blocked,
)
from app.sources.bili import collection_play_order, episode_start_index
from app.settings import DEFAULT_MESSAGES, load_config, normalize_idle_items, save_config
from app.sources.registry import all_adapters, get_adapter
from app.welcome import WelcomeGate, compose_welcome


class SongEngine:
    def __init__(self, app_state: Any) -> None:
        self.app = app_state
        self._search_lock = asyncio.Lock()
        self._selection_task: asyncio.Task | None = None
        self._last_request_at: dict[int, float] = {}
        self._user_last_item: dict[int, str] = {}
        self._playables: dict[str, Playable] = {}
        self._busy_users: set[int] = set()
        self._idle_index = 0
        self._paused_idle: dict[str, Any] | None = None
        self._idle_follow: list[dict[str, Any]] | None = None
        self._idle_follow_origin: dict[str, Any] | None = None
        self._idle_delay_task: asyncio.Task | None = None
        self._welcome_gate = WelcomeGate()
        self._welcome_task: asyncio.Task | None = None
        self._welcome_login_hinted = False
        self._history: list[dict[str, Any]] = []
        self._outgoing_for_history: dict[str, Any] | None = None
        self._history_max = 30
        self._pending_priv: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------------ helpers
    def cfg(self) -> dict[str, Any]:
        return self.app.config

    def hub(self):
        return self.app.hub

    def log(self, level: str, message: str) -> None:
        self.hub().log(level, message)

    async def push_state(self) -> None:
        await self.hub().broadcast_state(self.app.public_extra())

    def get_playable(self, item_id: str) -> Optional[Playable]:
        return self._playables.get(item_id)

    # ------------------------------------------------------------------ danmaku
    async def on_danmaku(self, event: dict[str, Any]) -> None:
        uid = int(event.get("uid") or 0)
        uname = str(event.get("uname") or "观众")
        text = str(event.get("text") or "")
        expect = bool(
            self.hub().selection and self.hub().selection.requester_uid == uid
        )
        cmd = parse_danmaku(text, expect_choice=expect)
        if cmd.kind == CommandKind.IGNORE:
            return
        if cmd.kind == CommandKind.CHOICE:
            await self.try_choose(uid, uname, cmd.choice, from_danmaku=True)
            return
        if cmd.kind == CommandKind.CANCEL:
            await self.user_cancel(uid, uname)
            return
        if cmd.kind == CommandKind.REQUEST:
            await self.request_song(
                uid,
                uname,
                cmd.query,
                guard_level=int(event.get("guard_level") or 0),
                medal_room=int(event.get("medal_room") or 0),
                medal_name=str(event.get("medal_name") or ""),
            )


    async def on_enter(self, uid: int, uname: str) -> None:
        """有人进入直播间。已登录且开启欢迎时，排队发送一句问候。"""
        name = str(uname or "").strip()
        if not name:
            return
        welcome_cfg = (self.cfg().get("welcome") or {})
        if not welcome_cfg.get("enabled", True):
            return
        try:
            uid = int(uid or 0)
        except (TypeError, ValueError):
            uid = 0
        broadcaster = danmaku_sender.logged_in_uid()
        if uid and broadcaster and uid == broadcaster:
            return
        if not danmaku_sender.is_logged_in():
            if not self._welcome_login_hinted:
                self._welcome_login_hinted = True
                self.log("info", "有观众进入直播间。登录 B 站后才能自动发欢迎弹幕（请在控制台扫码）。")
            return
        if not self._welcome_gate.accept(uid, name):
            return
        self.log("info", f"检测到 {name} 进入直播间，准备发送欢迎弹幕")
        if self._welcome_task is None or self._welcome_task.done():
            self._welcome_task = asyncio.create_task(self._welcome_worker())

    async def _welcome_worker(self) -> None:
        while self._welcome_gate.pending_count():
            wait = self._welcome_gate.seconds_until_ready()
            if wait > 0:
                await asyncio.sleep(wait)
            item = self._welcome_gate.pop()
            if not item:
                break
            _uid, name = item
            if not danmaku_sender.is_logged_in():
                continue
            if not (self.cfg().get("welcome") or {}).get("enabled", True):
                continue
            extras = (self.cfg().get("welcome") or {}).get("extra_templates") or []
            msg, key = compose_welcome(name, last_key=self._welcome_gate.last_key, last_keys=list(self._welcome_gate.last_keys), extra_templates=extras)
            room = int(self.app.real_room_id or self.cfg()["room_id"])
            try:
                await danmaku_sender.send_danmaku(room, msg)
                self._welcome_gate.mark_sent(key)
                self.log("info", f"已发欢迎弹幕：{msg}")
            except Exception as exc:
                self.log("warn", f"欢迎弹幕发送失败：{exc}")

    async def request_song(
        self,
        uid: int,
        uname: str,
        query: str,
        *,
        manual: bool = False,
        guard_level: int = 0,
        medal_room: int = 0,
        medal_name: str = "",
    ) -> None:

        if uid in self._busy_users and not manual:
            self.log("info", f"{uname} 正在处理上一条点歌，已忽略重复请求")
            return
        blocked, reason_key, reason = self._blacklist_reject(uid, uname, query)
        if blocked:
            self.log("warn", reason)
            if not manual:
                await self._notify(uname, self._message(reason_key))
            await self.push_state()
            return
        ok, reason = self._check_spam(uid, uname, manual=manual)
        if not ok:
            self.log("warn", reason)
            await self._notify(uname, reason_short(reason))
            await self.push_state()
            return

        self._pending_priv[int(uid or 0)] = {
            "guard_level": int(guard_level or 0),
            "medal_room": int(medal_room or 0),
            "medal_name": str(medal_name or ""),
        }
        self._busy_users.add(uid)
        self.log("info", f"{uname} 点歌：{query}")
        await self.push_state()
        try:
            picked, choices, source_used, notes = await self._search_by_priority(query)
            for n in notes:
                self.log("info", n)

            if picked is not None:
                item = await self._enqueue(picked, uid, uname, query)
                if item:
                    self._last_request_at[uid] = time.time()
                    self.log("info", f"已为 {uname} 加入：{item.title} - {item.artist}（{source_name(item.source)}）")
                    await self._maybe_start()
                await self.push_state()
                return

            if choices:
                await self._start_selection(uid, uname, query, choices, source_used or choices[0].source)
                return

            self.log("warn", f"没有找到可完整播放的「{query}」")
            _, source_filter = extract_requested_source(query)
            if source_filter:
                await self._notify(uname, self._message("not_found_platform", platform=_source_short(source_filter)))
            else:
                await self._notify(uname, self._message("not_found"))
            await self.push_state()
        except Exception as exc:
            self.log("error", f"点歌处理失败：{exc}")
            await self.push_state()
        finally:
            self._busy_users.discard(uid)

    def _check_spam(self, uid: int, uname: str, *, manual: bool) -> tuple[bool, str]:
        if manual:
            return True, ""
        anti = self.cfg()["anti_spam"]
        cooldown = int(anti.get("cooldown_seconds") or 0)
        last = self._last_request_at.get(uid) or 0
        if cooldown and last and time.time() - last < cooldown:
            remain = int(cooldown - (time.time() - last))
            return False, f"{uname} 点歌太频繁，请 {remain} 秒后再试"
        pending = [i for i in self.hub().queue if i.requester_uid == uid]
        max_pending = int(anti.get("max_pending_per_user") or 2)
        if len(pending) >= max_pending:
            return False, f"{uname} 已有 {len(pending)} 首待播，请等播完再点"
        max_queue = int(anti.get("max_queue_size") or 30)
        inflight = len(self.hub().queue) + (1 if self.hub().current else 0)
        if len(self.hub().queue) >= max_queue:
            return False, f"歌单已满（{max_queue}），请稍后再点"
        return True, ""

    async def _search_by_priority(
        self, query: str
    ) -> tuple[Optional[SearchResult], list[SearchResult], Optional[str], list[str]]:
        """按优先级逐个音源：先并行搜，再按顺序解析可播性。

        若点歌词末尾指定了平台（如「晴天 酷狗」），则只搜该音源。
        """
        search_query, source_filter = extract_requested_source(query)
        search_query = search_query or query
        adapters = all_adapters()
        notes: list[str] = []
        if source_filter:
            if source_filter not in adapters:
                notes.append(f"不认识的音源：{source_filter}")
                return None, [], source_filter, notes
            priority = [source_filter]
            notes.append(f"按指定音源搜索：{source_name(source_filter)}（{search_query}）")
        else:
            priority = list(self.cfg().get("sources_priority") or [])

        async def search_one(sid: str) -> tuple[str, list[SearchResult], str]:
            adapter = adapters.get(sid)
            if not adapter:
                return sid, [], "未知音源"
            try:
                results = await asyncio.wait_for(adapter.search(search_query, limit=8), timeout=10)
                return sid, results or [], ""
            except Exception as exc:
                return sid, [], f"{adapter.display_name} 搜索失败：{exc}"

        gathered = await asyncio.gather(*[search_one(sid) for sid in priority], return_exceptions=True)
        search_map: dict[str, list[SearchResult]] = {}
        for item in gathered:
            if isinstance(item, Exception):
                notes.append(f"音源搜索异常：{item}")
                continue
            sid, results, err = item
            if err:
                notes.append(err)
            search_map[sid] = results

        playable_map: dict[str, list[SearchResult]] = {}
        for sid in priority:
            adapter = adapters.get(sid)
            if not adapter:
                continue
            results = search_map.get(sid) or []
            if not results:
                if adapter.status.value == "placeholder":
                    notes.append(f"{adapter.display_name}：当前为占位，已跳过")
                elif source_filter:
                    notes.append(f"{adapter.display_name}：没有搜索结果")
                else:
                    notes.append(f"{adapter.display_name}：没有搜索结果，试下一个音源")
                continue

            ranked = ranking.rank_results(results, search_query)
            candidates = [c for c in ranked[:6] if c.score >= 20]
            resolved = await asyncio.gather(
                *[self._try_resolve(adapter, cand) for cand in candidates],
                return_exceptions=True,
            )
            playable_ranked: list[SearchResult] = []
            for item in resolved:
                if isinstance(item, Exception) or not item:
                    continue
                playable_ranked.append(item.result)
                self._cache_tmp(item)
            playable_ranked.sort(key=lambda r: r.score, reverse=True)
            if not playable_ranked:
                if source_filter:
                    notes.append(
                        f"{adapter.display_name}：搜到 {len(results)} 条，但没有完整可播地址（可能是会员/试听）"
                    )
                else:
                    notes.append(
                        f"{adapter.display_name}：搜到 {len(results)} 条，但没有完整可播地址（可能是会员/试听），试下一个音源"
                    )
                continue
            playable_map[sid] = playable_ranked

        auto, choices, sid = ranking.select_from_priority(priority, playable_map)
        return auto, choices, sid, notes

    def _tmp_key(self, result: SearchResult) -> str:
        return f"{result.source}:{result.source_id}"

    def _cache_tmp(self, playable: Playable) -> None:
        # 暂存，等入队时转到 item.id
        self._playables[self._tmp_key(playable.result)] = playable

    async def _try_resolve(self, adapter, result: SearchResult) -> Optional[Playable]:
        try:
            playable = await asyncio.wait_for(adapter.resolve_playable(result), timeout=10)
        except Exception:
            return None
        if not playable or not playable.url or not playable.is_complete:
            return None
        if not playable.lyric:
            try:
                playable.lyric = await adapter.fetch_lyric(result)
            except Exception:
                pass
        return playable

    async def _enqueue(self, result: SearchResult, uid: int, uname: str, query: str) -> Optional[QueueItem]:
        playable = self._playables.get(self._tmp_key(result))
        if not playable:
            adapter = get_adapter(result.source)
            if not adapter:
                return None
            playable = await self._try_resolve(adapter, result)
            if not playable:
                self.log("warn", "入队前无法获取完整播放地址，已取消")
                return None
        if song_is_blocked(self.cfg().get("blacklist") or {}, result.title, result.artist, query):
            reason = self._message("blacklisted_song")
            self.log("warn", f"「{result.title}」在黑名单中，已拒绝 {uname} 的点歌")
            await self._notify(uname, reason)
            return None
        extra = dict(result.extra or {})
        priv = self._pending_priv.get(int(uid or 0)) or {}
        guard_level = int(priv.get("guard_level") or 0)
        medal_room = int(priv.get("medal_room") or 0)
        medal_name = str(priv.get("medal_name") or "")
        pq = self.cfg().get("priority_queue") if isinstance(self.cfg().get("priority_queue"), dict) else {}
        enabled = pq.get("enabled", True)
        tier = priority_tier(guard_level, medal_room, self._priority_rooms(), enabled=bool(enabled))
        extra["priority_tier"] = tier
        if guard_level:
            extra["guard_level"] = guard_level
        if tier == TIER_FAN_MEDAL:
            extra["fan_medal"] = True
            if medal_name:
                extra["medal_name"] = medal_name
        item = QueueItem(
            id=uuid.uuid4().hex[:12],
            title=result.title,
            artist=result.artist,
            source=result.source,
            source_id=result.source_id,
            duration=result.duration,
            cover_url=result.cover_url,
            requester_uid=uid,
            requester_name=uname,
            query=query,
            added_at=time.time(),
            extra=extra,
        )
        self._playables[item.id] = playable
        self._playables.pop(self._tmp_key(result), None)
        self._insert_queued(item)
        self._user_last_item[uid] = item.id
        await self._emit_request_toast(item)
        return item

    # ------------------------------------------------------------------ selection
    async def _start_selection(
        self,
        uid: int,
        uname: str,
        query: str,
        choices: list[SearchResult],
        source: str,
    ) -> None:
        timeout = int(self.cfg().get("selection_timeout_seconds") or 30)
        session = SelectionSession(
            id=uuid.uuid4().hex[:10],
            requester_uid=uid,
            requester_name=uname,
            query=query,
            options=[SelectionOption(index=i + 1, result=c) for i, c in enumerate(choices)],
            deadline=time.time() + timeout,
            source=source,
        )
        # 若已有选择会话，先取消
        await self._cancel_selection_task()
        self.hub().selection = session
        n = len(choices)
        nums = "/".join(str(i + 1) for i in range(n))
        self.log("info", f"「{query}」有多个候选，等待 {uname} 在 {timeout} 秒内回复 {nums}")
        await self._notify(uname, self._message("please_choose", nums=nums))
        await self.push_state()
        self._selection_task = asyncio.create_task(self._selection_timeout(session.id, timeout))

    async def _selection_timeout(self, session_id: str, timeout: float) -> None:
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        sel = self.hub().selection
        if not sel or sel.id != session_id:
            return
        uname = sel.requester_name
        action = str(((self.cfg().get("selection") or {}).get("timeout_action")) or "skip").strip().lower()
        options = list(sel.options or [])
        self.hub().selection = None
        if action == "play_first" and options:
            first = options[0]
            item = await self._enqueue(first.result, sel.requester_uid, sel.requester_name, sel.query)
            if item:
                self._last_request_at[sel.requester_uid] = time.time()
                self.log("info", f"{uname} 选歌已超时，已自动播放第一个结果：{item.title} - {item.artist}")
                await self._maybe_start()
            else:
                self.log("warn", f"{uname} 选歌已超时，第一个结果无法播放，已跳过")
                await self._notify(uname, self._message("choice_unplayable"))
            await self.push_state()
            return
        self.log("warn", f"{uname} 选歌已超时，已取消")
        await self._notify(uname, self._message("selection_timeout"))
        await self.push_state()

    async def _cancel_selection_task(self) -> None:
        task = self._selection_task
        self._selection_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def try_choose(self, uid: int, uname: str, choice: int, *, from_danmaku: bool, force: bool = False) -> None:
        sel = self.hub().selection
        if not sel:
            return
        if not force and sel.requester_uid != uid:
            if from_danmaku:
                return
            # 控制台主播代选
            force = True
        if time.time() > sel.deadline and not force:
            return
        option = next((o for o in sel.options if o.index == choice), None)
        if not option:
            if from_danmaku:
                self.log("info", f"{uname} 回复了无效序号 {choice}")
            return
        await self._cancel_selection_task()
        self.hub().selection = None
        item = await self._enqueue(option.result, sel.requester_uid, sel.requester_name, sel.query)
        if item:
            self._last_request_at[sel.requester_uid] = time.time()
            who = "主播代选" if force and uid != sel.requester_uid else uname
            self.log("info", f"{who} 选择了 {choice}：{item.title} - {item.artist}")
            await self._maybe_start()
        else:
            self.log("warn", "选中的曲目无法播放，已取消")
            await self._notify(sel.requester_name, self._message("choice_unplayable"))
        await self.push_state()

    async def user_cancel(self, uid: int, uname: str) -> None:
        sel = self.hub().selection
        if sel and sel.requester_uid == uid:
            await self._cancel_selection_task()
            self.hub().selection = None
            self.log("info", f"{uname} 撤回了本次选歌")
            await self.push_state()
            return
        last_id = self._user_last_item.get(uid)
        if not last_id:
            self.log("info", f"{uname} 没有可撤回的点歌")
            return
        if self.hub().current and self.hub().current.item.id == last_id:
            self.log("info", f"{uname} 想撤回的歌正在播放，请主播在控制台跳过")
            return
        before = len(self.hub().queue)
        self.hub().queue = [i for i in self.hub().queue if not (i.id == last_id and i.requester_uid == uid)]
        if len(self.hub().queue) < before:
            self._user_last_item.pop(uid, None)
            self._playables.pop(last_id, None)
            self.log("info", f"{uname} 已撤回自己最近一首待播")
        else:
            self.log("info", f"{uname} 没有可撤回的待播歌曲")
        await self.push_state()

    # ------------------------------------------------------------------ player / queue control
    def _is_idle_item(self, item: QueueItem | None) -> bool:
        return bool(item and (item.extra or {}).get("idle"))

    def _has_queued_request(self) -> bool:
        return any(not self._is_idle_item(i) for i in self.hub().queue)

    def _has_idle_to_continue(self) -> bool:
        cfg = self.cfg().get("idle_playlist") or {}
        if cfg.get("enabled") and (cfg.get("items") or self._paused_idle or self._idle_follow):
            return True
        if self._paused_idle:
            return True
        return any(self._is_idle_item(i) for i in self.hub().queue)

    async def _maybe_start(self) -> None:
        if self._has_queued_request():
            self._cancel_idle_delay()
        await self._preempt_idle_for_requests()
        if self.hub().current:
            return
        await self.play_next()

    async def _preempt_idle_for_requests(self) -> None:
        """有人点歌时立刻暂停默认歌单，改播点歌队列。"""
        cur = self.hub().current
        if not cur or not self._is_idle_item(cur.item):
            return
        if not self._has_queued_request():
            return
        self._paused_idle = {
            "item": cur.item,
            "position": float(cur.position or 0),
            "lyric": cur.lyric or "",
        }
        pos = int(cur.position or 0)
        self.log("info", f"收到点歌，已暂停默认歌单：{cur.item.title}（{pos // 60:02d}:{pos % 60:02d}）")
        self.hub().current = None
        await self.play_next()

    async def _resume_paused_idle(self) -> bool:
        parked = self._paused_idle
        if not parked:
            return False
        cfg = self.cfg().get("idle_playlist") or {}
        if not cfg.get("enabled"):
            self._clear_paused_idle()
            return False
        item: QueueItem = parked["item"]
        if not self._idle_track_belongs(item):
            self._clear_paused_idle()
            if self.hub().queue:
                return False
            return await self._try_play_idle()
        position = float(parked.get("position") or 0)
        duration = int(item.duration or 0)
        # 已经接近结尾就直接播下一首，避免恢复后马上结束
        if duration and position >= max(duration - 2, duration * 0.98):
            self._clear_paused_idle()
            if self.hub().queue:
                return False
            return await self._try_play_idle()
        playable = self._playables.get(item.id)
        if not playable or not playable.url:
            result = SearchResult(
                source=item.source,
                source_id=item.source_id,
                title=item.title,
                artist=item.artist,
                duration=item.duration,
                cover_url=item.cover_url,
                extra=item.extra,
            )
            adapter = get_adapter(item.source)
            if adapter:
                playable = await self._try_resolve(adapter, result)
            if playable:
                self._playables[item.id] = playable
        if not playable:
            self.log("warn", f"暂停的默认歌单「{item.title}」无法继续，已跳到下一首")
            self._clear_paused_idle()
            if self.hub().queue:
                return False
            return await self._try_play_idle()
        lyric = parked.get("lyric") or playable.lyric or ""
        if not playable.lyric:
            playable.lyric = lyric
        self._paused_idle = None
        await self._begin_playback(item, playable, position=position)
        return True

    def _clear_paused_idle(self) -> None:
        parked = self._paused_idle
        self._paused_idle = None
        if parked:
            item = parked.get("item")
            if item is not None:
                self._playables.pop(item.id, None)

    def _resume_delay_seconds(self) -> float:
        raw = (self.cfg().get("idle_playlist") or {}).get("resume_delay_seconds", 5)
        try:
            return max(0.0, min(120.0, float(raw)))
        except (TypeError, ValueError):
            return 5.0

    def _cancel_idle_delay(self) -> None:
        task = self._idle_delay_task
        self._idle_delay_task = None
        if task and not task.done():
            task.cancel()

    def _clear_idle_follow(self) -> None:
        self._idle_follow = None
        self._idle_follow_origin = None

    def _idle_playlist_items(self) -> list[dict[str, Any]]:
        return [x for x in ((self.cfg().get("idle_playlist") or {}).get("items") or []) if isinstance(x, dict)]

    def _spec_bvid(self, spec: dict[str, Any]) -> str:
        return str(spec.get("bvid") or "").strip()

    def _item_bvid(self, item: QueueItem) -> str:
        extra = item.extra or {}
        return str(extra.get("bvid") or item.source_id or "").strip()

    def _idle_follow_is_current(self) -> bool:
        """合集续播只对还留在默认歌单里的那条有效。"""
        follow = list(self._idle_follow or [])
        if not follow:
            return False
        items = self._idle_playlist_items()
        if not items:
            return False
        origin = self._idle_follow_origin or {}
        origin_id = str(origin.get("id") or "")
        origin_bvid = str(origin.get("bvid") or "").strip()
        origin_season = str(origin.get("season_title") or "").strip()
        follow_bvids = {str(ep.get("bvid") or "").strip() for ep in follow}
        follow_bvids.discard("")
        follow_seasons = {str(ep.get("season_title") or "").strip() for ep in follow}
        follow_seasons.discard("")
        for spec in items:
            if origin_id and str(spec.get("id") or "") == origin_id:
                return True
            sb = self._spec_bvid(spec)
            if origin_bvid and sb == origin_bvid:
                return True
            st = str(spec.get("season_title") or "").strip()
            if st and (st == origin_season or st in follow_seasons):
                return True
            if sb and sb in follow_bvids:
                return True
        return False

    def _idle_track_belongs(self, item: QueueItem | None) -> bool:
        """正在播或已暂停的默认歌曲，是否仍属于当前默认歌单（含仍有效的合集续播）。"""
        if item is None or not self._is_idle_item(item):
            return False
        items = self._idle_playlist_items()
        if not items:
            return False
        extra = item.extra or {}
        bvid = self._item_bvid(item)
        query = str(item.query or "").strip()
        title = str(item.title or "").strip()
        ep = extra.get("idle_follow_ep") if isinstance(extra.get("idle_follow_ep"), dict) else None
        seasons: set[str] = set()
        if ep and ep.get("season_title"):
            seasons.add(str(ep.get("season_title") or "").strip())
        if extra.get("season_title"):
            seasons.add(str(extra.get("season_title") or "").strip())
        origin = self._idle_follow_origin or {}
        if origin.get("season_title"):
            seasons.add(str(origin.get("season_title") or "").strip())
        seasons.discard("")
        spec_id = str(extra.get("idle_spec_id") or "")
        for spec in items:
            if spec_id and str(spec.get("id") or "") == spec_id:
                return True
            kind = str(spec.get("kind") or "query")
            if kind == "bili" or spec.get("bvid"):
                sb = self._spec_bvid(spec)
                if sb and bvid and sb == bvid:
                    return True
                if ep and sb and sb == str(ep.get("bvid") or "").strip():
                    return True
                if origin.get("id") and str(spec.get("id") or "") == str(origin.get("id")):
                    return True
                if origin.get("bvid") and sb == str(origin.get("bvid") or "").strip():
                    return True
                st = str(spec.get("season_title") or "").strip()
                if st and st in seasons:
                    return True
            else:
                q = str(spec.get("query") or spec.get("title") or "").strip()
                stored_q = str(extra.get("idle_query") or "").strip()
                if q and q in {query, title, stored_q}:
                    return True
        return False

    def _idle_tied_to_spec(self, item: QueueItem | None, spec: dict[str, Any] | None) -> bool:
        """这首默认歌曲是否来自某一条默认歌单（含该条的合集续播）。"""
        if item is None or not spec or not self._is_idle_item(item):
            return False
        extra = item.extra or {}
        spec_id = str(spec.get("id") or "")
        item_spec = str(extra.get("idle_spec_id") or "")
        if spec_id and item_spec:
            return item_spec == spec_id
        origin = self._idle_follow_origin or {}
        if spec_id and str(origin.get("id") or "") == spec_id:
            return True
        sb = self._spec_bvid(spec)
        bvid = self._item_bvid(item)
        if sb and bvid and sb == bvid:
            return True
        if sb and str(origin.get("bvid") or "").strip() == sb:
            return True
        ep = extra.get("idle_follow_ep") if isinstance(extra.get("idle_follow_ep"), dict) else None
        if ep and sb and sb == str(ep.get("bvid") or "").strip():
            return True
        st = str(spec.get("season_title") or "").strip()
        if st:
            seasons = {
                str(origin.get("season_title") or "").strip(),
                str(extra.get("season_title") or "").strip(),
            }
            if ep:
                seasons.add(str(ep.get("season_title") or "").strip())
            seasons.discard("")
            if st in seasons:
                return True
        q = str(spec.get("query") or spec.get("title") or "").strip()
        if q and q in {
            str(item.query or "").strip(),
            str(item.title or "").strip(),
            str(extra.get("idle_query") or "").strip(),
        }:
            return True
        return False

    def _discard_follow_from_spec(self, spec: dict[str, Any] | None) -> None:
        if not self._idle_follow:
            return
        if not self._idle_playlist_items():
            self._clear_idle_follow()
            return
        if spec and self._idle_follow_is_current() is False:
            self._clear_idle_follow()
            return
        origin = self._idle_follow_origin or {}
        if not spec:
            return
        spec_id = str(spec.get("id") or "")
        if spec_id and str(origin.get("id") or "") == spec_id:
            self._clear_idle_follow()
            return
        sb = self._spec_bvid(spec)
        if sb and str(origin.get("bvid") or "").strip() == sb:
            self._clear_idle_follow()
            return
        st = str(spec.get("season_title") or "").strip()
        if st and st == str(origin.get("season_title") or "").strip():
            self._clear_idle_follow()
            return
        follow_seasons = {str(ep.get("season_title") or "").strip() for ep in (self._idle_follow or [])}
        if st and st in follow_seasons:
            self._clear_idle_follow()

    def _episode_matches(self, ep: dict[str, Any] | None, bvid: str, cid: int = 0) -> bool:
        if not ep:
            return False
        eb = str(ep.get("bvid") or "").strip()
        if bvid and eb and eb != bvid:
            return False
        if cid:
            try:
                return int(ep.get("cid") or 0) == int(cid)
            except (TypeError, ValueError):
                return False
        return bool(eb == bvid) if bvid else False

    def _discard_queue_from_spec(self, spec: dict[str, Any] | None) -> None:
        if not spec:
            return
        kept: list[QueueItem] = []
        for item in self.hub().queue:
            if self._idle_tied_to_spec(item, spec):
                self._playables.pop(item.id, None)
                continue
            kept.append(item)
        self.hub().queue = kept

    def _prune_stale_idle_queue(self) -> None:
        kept: list[QueueItem] = []
        for item in self.hub().queue:
            if not self._is_idle_item(item):
                kept.append(item)
                continue
            if self._idle_track_belongs(item):
                kept.append(item)
            else:
                self._playables.pop(item.id, None)
        self.hub().queue = kept

    def _advance_idle_index_past_spec(self, spec_id: str) -> None:
        sid = str(spec_id or "")
        if not sid:
            return
        items = self._idle_playlist_items()
        for i, spec in enumerate(items):
            if str(spec.get("id") or "") == sid:
                self._idle_index = i + 1
                return

    def _on_started_queued_idle(self, item: QueueItem) -> None:
        if not self._is_idle_item(item):
            return
        extra = item.extra or {}
        spec_id = str(extra.get("idle_spec_id") or "")
        if spec_id:
            self._advance_idle_index_past_spec(spec_id)
        follow = list(self._idle_follow or [])
        if not follow:
            return
        bvid = self._item_bvid(item)
        try:
            cid = int(extra.get("cid") or 0)
        except (TypeError, ValueError):
            cid = 0
        remain = [ep for ep in follow if not self._episode_matches(ep, bvid, cid)]
        self._idle_follow = remain or None
        if not remain:
            still = any(
                self._is_idle_item(i) and str((i.extra or {}).get("idle_spec_id") or "") == spec_id
                for i in self.hub().queue
            )
            if not still:
                self._clear_idle_follow()

    def _collection_queue_item(self, ep: dict[str, Any], spec: dict[str, Any]) -> QueueItem:
        bvid = str(ep.get("bvid") or "")
        try:
            duration = int(ep.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        clean_ep = {k: v for k, v in ep.items() if not str(k).startswith("_")}
        return QueueItem(
            id=uuid.uuid4().hex[:12],
            title=str(ep.get("title") or bvid),
            artist=str(ep.get("artist") or ""),
            source="bili",
            source_id=bvid,
            duration=duration,
            cover_url=str(ep.get("cover_url") or ""),
            requester_uid=-1,
            requester_name="默认歌单",
            query=bvid,
            added_at=time.time(),
            extra={
                "idle": True,
                "idle_spec_id": str(spec.get("id") or ""),
                "bvid": bvid,
                "aid": ep.get("aid") or 0,
                "cid": ep.get("cid") or 0,
                "season_title": ep.get("season_title") or spec.get("season_title") or "",
                "season_index": ep.get("season_index"),
                "season_total": ep.get("season_total"),
                "priority_tier": TIER_IDLE,
                "idle_follow_ep": clean_ep,
                "collection": True,
            },
        )

    def _enqueue_collection_episodes(
        self,
        spec: dict[str, Any],
        episodes: list[dict[str, Any]],
        *,
        start_index: int = 0,
        loop: bool = True,
        include_start: bool = True,
    ) -> None:
        if not episodes:
            return
        ordered = collection_play_order(episodes, start_index, loop=loop)
        if not include_start and ordered:
            ordered = ordered[1:]

        def _is_same_ep(ep: dict[str, Any], item: QueueItem | None) -> bool:
            if not item:
                return False
            bvid = self._item_bvid(item)
            try:
                cid = int((item.extra or {}).get("cid") or 0)
            except (TypeError, ValueError):
                cid = 0
            return self._episode_matches(ep, bvid, cid)

        skip_items: list[QueueItem] = []
        cur = self.hub().current
        if cur and self._is_idle_item(cur.item) and self._idle_tied_to_spec(cur.item, spec):
            skip_items.append(cur.item)
        parked = self._paused_idle or {}
        parked_item = parked.get("item")
        if parked_item and self._is_idle_item(parked_item) and self._idle_tied_to_spec(parked_item, spec):
            skip_items.append(parked_item)
        if skip_items:
            ordered = [ep for ep in ordered if not any(_is_same_ep(ep, it) for it in skip_items)]

        new_items = [self._collection_queue_item(ep, spec) for ep in ordered]
        q = list(self.hub().queue)
        first_idx = next((i for i, it in enumerate(q) if self._idle_tied_to_spec(it, spec)), None)
        kept = [it for it in q if not self._idle_tied_to_spec(it, spec)]
        insert_at = first_idx if first_idx is not None else len(kept)
        insert_at = max(0, min(insert_at, len(kept)))
        self.hub().queue = kept[:insert_at] + new_items + kept[insert_at:]

    async def _materialize_collection_for_spec(
        self,
        spec: dict[str, Any],
        result: SearchResult | None = None,
        *,
        include_start: bool = True,
    ) -> None:
        eps: list[dict[str, Any]] = []
        extra = (result.extra if result else None) or {}
        cached = extra.get("_collection_episodes")
        if isinstance(cached, list) and cached:
            eps = list(cached)
        if len(eps) < 2 and result:
            adapter = get_adapter("bili")
            if adapter and hasattr(adapter, "list_collection_episodes"):
                try:
                    eps = list(await adapter.list_collection_episodes(result) or [])
                except Exception as exc:
                    self.log("warn", f"读取哔哩哔哩合集失败：{exc}")
                    return
        if len(eps) < 2:
            return
        try:
            start_cid = int(spec.get("cid") or extra.get("cid") or 0)
        except (TypeError, ValueError):
            start_cid = 0
        start = episode_start_index(
            eps,
            bvid=str(spec.get("bvid") or extra.get("bvid") or (result.source_id if result else "") or "").strip(),
            cid=start_cid,
        )
        loop = bool((self.cfg().get("idle_playlist") or {}).get("loop", True))
        self._enqueue_collection_episodes(
            spec, eps, start_index=start, loop=loop, include_start=include_start
        )

    async def _sync_idle_playback(self) -> None:
        """歌单被换掉后，丢掉旧合集续播，并切到当前默认歌单。"""
        self._prune_stale_idle_queue()
        if not self._idle_playlist_items() or not self._idle_follow_is_current():
            self._clear_idle_follow()
        parked = self._paused_idle
        if parked and parked.get("item") and (
            not self._idle_playlist_items() or not self._idle_track_belongs(parked["item"])
        ):
            self._clear_paused_idle()
        cur = self.hub().current
        if not cur or not self._is_idle_item(cur.item) or (
            self._idle_playlist_items() and self._idle_track_belongs(cur.item)
        ):
            return
        self.log("info", f"默认歌单已更换，停止播放旧曲子：{cur.item.title}")
        self._stash_outgoing(cur)
        self._playables.pop(cur.item.id, None)
        self.hub().current = None
        if self.hub().queue:
            await self.play_next()
            return
        cfg = self.cfg().get("idle_playlist") or {}
        if cfg.get("enabled") and self._idle_playlist_items():
            self._idle_index = 0
            if await self._try_play_idle():
                return
        await self.hub().send_player_cmd({"type": "player_cmd", "action": "stop"})
        await self.push_state()

    def _schedule_idle_after(self, delay: float) -> None:
        self._cancel_idle_delay()
        sec = int(delay) if delay == int(delay) else delay
        self.log("info", f"{sec} 秒后回到默认歌单")

        async def later() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            self._idle_delay_task = None
            if self.hub().current:
                return
            if self._has_queued_request():
                await self.play_next()
                return
            if await self._resume_paused_idle():
                return
            if self.hub().queue:
                await self.play_next()
                return
            if await self._try_play_idle():
                return
            await self.push_state()

        self._idle_delay_task = asyncio.create_task(later())

    async def play_next(self, *, idle_delay: bool = False) -> None:
        if self._has_queued_request():
            self._cancel_idle_delay()
        if not self._has_queued_request():
            if idle_delay:
                delay = self._resume_delay_seconds()
                if delay > 0 and self._has_idle_to_continue():
                    self.hub().current = None
                    self._commit_outgoing_history(None)
                    await self.hub().send_player_cmd({"type": "player_cmd", "action": "stop"})
                    await self.push_state()
                    self._schedule_idle_after(delay)
                    return
            if await self._resume_paused_idle():
                return
            if not self.hub().queue:
                if await self._try_play_idle():
                    return
                self.hub().current = None
                self._commit_outgoing_history(None)
                await self.hub().send_player_cmd({"type": "player_cmd", "action": "stop"})
                await self.push_state()
                return
        item = self.hub().queue.pop(0)
        playable = self._playables.get(item.id)
        if not playable or not playable.url:
            adapter = get_adapter(item.source)
            result = SearchResult(
                source=item.source,
                source_id=item.source_id,
                title=item.title,
                artist=item.artist,
                duration=item.duration,
                cover_url=item.cover_url,
                extra=item.extra,
            )
            if adapter:
                playable = await self._try_resolve(adapter, result)
            if playable:
                self._playables[item.id] = playable
        if not playable:
            self.log("warn", f"无法播放「{item.title}」，已跳过")
            await self.play_next()
            return
        # 如果歌词缺失再补一次
        if not playable.lyric:
            adapter = get_adapter(item.source)
            if adapter:
                try:
                    playable.lyric = await adapter.fetch_lyric(playable.result)
                except Exception:
                    pass
        self._on_started_queued_idle(item)
        await self._begin_playback(item, playable, position=0)


    async def _try_play_idle(self) -> bool:
        """队列为空时尝试播放默认歌单的下一首。单次调用最多遍历一遍列表，避免死循环。"""
        cfg = self.cfg().get("idle_playlist") or {}
        if not cfg.get("enabled"):
            self._clear_idle_follow()
            return False
        if await self._play_idle_follow():
            return True
        items = [x for x in (cfg.get("items") or []) if isinstance(x, dict)]
        if not items:
            return False
        loop = bool(cfg.get("loop", True))
        n = len(items)
        if not loop and self._idle_index >= n:
            return False
        start = self._idle_index % n if loop else self._idle_index
        for step in range(n):
            idx = (start + step) % n if loop else (start + step)
            if not loop and idx >= n:
                self._idle_index = n
                break
            spec = items[idx]
            self._idle_index = idx + 1
            resolved = await self._resolve_idle_item(spec)
            if not resolved:
                title = spec.get("title") or spec.get("query") or spec.get("bvid") or f"第{idx + 1}首"
                self.log("warn", f"默认歌单「{title}」暂时无法播放，已跳过")
                continue
            item, playable = resolved
            item.extra = {
                **(item.extra or {}),
                "idle_index": idx,
                "idle_spec_id": str(spec.get("id") or (item.extra or {}).get("idle_spec_id") or ""),
            }
            if not playable.lyric:
                adapter = get_adapter(item.source)
                if adapter:
                    try:
                        playable.lyric = await adapter.fetch_lyric(playable.result)
                    except Exception:
                        pass
            await self._begin_playback(item, playable, position=0)
            return True
        self.log("warn", "默认歌单里的曲子暂时都无法播放，已停止")
        return False

    async def _resolve_idle_item(self, spec: dict) -> tuple[QueueItem, Playable] | None:
        kind = str(spec.get("kind") or "query")
        result: SearchResult | None = None
        playable: Playable | None = None
        query = ""
        if kind == "bili":
            adapter = get_adapter("bili")
            if not adapter:
                return None
            bvid = str(spec.get("bvid") or "").strip()
            aid = 0
            cid = 0
            try:
                aid = int(spec.get("aid") or 0)
            except (TypeError, ValueError):
                aid = 0
            try:
                cid = int(spec.get("cid") or 0)
            except (TypeError, ValueError):
                cid = 0
            try:
                if bvid and hasattr(adapter, "from_bvid"):
                    result = await adapter.from_bvid(bvid)
                elif aid and hasattr(adapter, "from_video"):
                    result = await adapter.from_video(aid=aid)
            except Exception as exc:
                self.log("warn", f"默认歌单解析哔哩哔哩视频失败：{exc}")
                result = None
            if result is None and (bvid or aid):
                result = SearchResult(
                    source="bili",
                    source_id=bvid or f"av{aid}",
                    title=str(spec.get("title") or bvid or f"av{aid}"),
                    artist=str(spec.get("artist") or ""),
                    duration=int(spec.get("duration") or 0),
                    cover_url=str(spec.get("cover_url") or ""),
                    extra={"bvid": bvid, "aid": aid or None, "cid": cid or None},
                )
            if result:
                if cid:
                    result.extra = {**(result.extra or {}), "cid": cid}
                if spec.get("title"):
                    result.title = str(spec.get("title") or result.title)
                if spec.get("artist"):
                    result.artist = str(spec.get("artist") or result.artist)
                if spec.get("cover_url"):
                    result.cover_url = str(spec.get("cover_url") or result.cover_url)
                playable = await self._try_resolve(adapter, result)
            query = str(spec.get("url") or bvid or (f"av{aid}" if aid else ""))
        else:
            query = str(spec.get("query") or "").strip()
            if not query:
                return None
            picked, choices, _sid, notes = await self._search_by_priority(query)
            for n in notes:
                self.log("info", n)
            result = picked or (choices[0] if choices else None)
            if result:
                playable = self._playables.get(self._tmp_key(result))
                if not playable:
                    adapter = get_adapter(result.source)
                    if adapter:
                        playable = await self._try_resolve(adapter, result)
        if not result or not playable or not playable.url:
            return None
        item = QueueItem(
            id=uuid.uuid4().hex[:12],
            title=result.title,
            artist=result.artist,
            source=result.source,
            source_id=result.source_id,
            duration=result.duration,
            cover_url=result.cover_url,
            requester_uid=-1,
            requester_name="默认歌单",
            query=query,
            added_at=time.time(),
            extra={
                **(result.extra or {}),
                "idle": True,
                "idle_spec_id": str(spec.get("id") or ""),
                "idle_query": str(spec.get("query") or query or ""),
            },
        )
        self._playables[item.id] = playable
        self._playables.pop(self._tmp_key(result), None)
        if kind == "bili" and not spec.get("_from_follow"):
            await self._prepare_idle_follow(result, origin=spec)
        return item, playable

    async def _prepare_idle_follow(self, result: SearchResult, *, origin: dict[str, Any] | None = None) -> None:
        adapter = get_adapter("bili")
        self._clear_idle_follow()
        if not adapter:
            return
        extra = result.extra or {}
        origin = origin or {}
        all_eps: list[dict[str, Any]] = []
        cached = extra.get("_collection_episodes")
        if isinstance(cached, list) and len(cached) >= 2:
            all_eps = list(cached)
        else:
            try:
                if hasattr(adapter, "list_collection_episodes"):
                    all_eps = list(await adapter.list_collection_episodes(result) or [])
                elif hasattr(adapter, "list_follow_episodes"):
                    rest_only = list(await adapter.list_follow_episodes(result) or [])
                    all_eps = []
                    if rest_only:
                        # 旧适配器只给后面的集，至少把这些列入播放列表
                        all_eps = rest_only
            except Exception as exc:
                self.log("warn", f"读取哔哩哔哩合集失败：{exc}")
                return
        if len(all_eps) < 2 and not all_eps:
            return
        if len(all_eps) < 2:
            return
        try:
            start_cid = int(origin.get("cid") or extra.get("cid") or 0)
        except (TypeError, ValueError):
            start_cid = 0
        start = episode_start_index(
            all_eps,
            bvid=str(origin.get("bvid") or result.source_id or extra.get("bvid") or "").strip(),
            cid=start_cid,
        )
        loop = bool((self.cfg().get("idle_playlist") or {}).get("loop", True))
        ordered = collection_play_order(all_eps, start, loop=loop)
        rest = ordered[1:] if ordered else []
        self._idle_follow = rest
        self._idle_follow_origin = {
            "id": str(origin.get("id") or ""),
            "bvid": str(origin.get("bvid") or result.source_id or extra.get("bvid") or "").strip(),
            "season_title": str(
                origin.get("season_title")
                or extra.get("season_title")
                or all_eps[0].get("season_title")
                or ""
            ).strip(),
        }
        if origin:
            self._enqueue_collection_episodes(
                origin, all_eps, start_index=start, loop=loop, include_start=False
            )
        title = self._idle_follow_origin.get("season_title") or "合集"
        total = len(all_eps)
        self.log(
            "info",
            f"已将合集「{title}」全部 {total} 集列入播放列表，将从第 {start + 1} 集开始",
        )

    async def _play_idle_follow(self) -> bool:
        if not self._idle_follow_is_current():
            self._clear_idle_follow()
            return False
        follow = list(self._idle_follow or [])
        while follow:
            ep = follow.pop(0)
            self._idle_follow = follow
            spec = {
                "kind": "bili",
                "bvid": ep.get("bvid") or "",
                "aid": ep.get("aid") or 0,
                "cid": ep.get("cid") or 0,
                "title": ep.get("title") or "",
                "artist": ep.get("artist") or "",
                "cover_url": ep.get("cover_url") or "",
                "duration": ep.get("duration") or 0,
                "_from_follow": True,
            }
            resolved = await self._resolve_idle_item(spec)
            if not resolved:
                title = ep.get("title") or ep.get("bvid") or "合集下一集"
                self.log("warn", f"合集「{title}」暂时无法播放，已跳过")
                continue
            item, playable = resolved
            item.extra = {**(item.extra or {}), "idle_follow_ep": ep}
            if not playable.lyric:
                adapter = get_adapter(item.source)
                if adapter:
                    try:
                        playable.lyric = await adapter.fetch_lyric(playable.result)
                    except Exception:
                        pass
            await self._begin_playback(item, playable, position=0)
            return True
        self._idle_follow = None
        return False

    def _idle_cfg(self) -> dict:
        return self.cfg().setdefault(
            "idle_playlist",
            {"enabled": False, "loop": True, "items": []},
        )

    def _persist_config(self) -> None:
        save_config(self.cfg())
        self.app.config = load_config()

    async def add_idle_item(self, text: str) -> dict:
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("请填写歌名或哔哩哔哩视频链接")
        playlist = self._idle_cfg()
        parsed = parse_bili_input(raw)
        if parsed.get("kind") in {"bvid", "aid", "short"}:
            ref = await resolve_bili_ref(raw)
            if not ref:
                raise ValueError("无法识别这个哔哩哔哩链接")
            adapter = get_adapter("bili")
            result = None
            if adapter and hasattr(adapter, "from_video"):
                try:
                    result = await adapter.from_video(
                        bvid=str(ref.get("bvid") or ""),
                        aid=int(ref["aid"]) if ref.get("aid") else None,
                    )
                except Exception as exc:
                    raise ValueError(f"没有找到这个哔哩哔哩视频：{exc}") from exc
            if not result:
                raise ValueError("没有找到这个哔哩哔哩视频")
            extra = result.extra or {}
            item = {
                "id": uuid.uuid4().hex[:10],
                "kind": "bili",
                "bvid": result.source_id,
                "aid": extra.get("aid") or 0,
                "title": result.title,
                "artist": result.artist,
                "cover_url": result.cover_url,
                "url": f"https://www.bilibili.com/video/{result.source_id}",
                "duration": result.duration,
            }
            if extra.get("cid"):
                item["cid"] = extra["cid"]
            if extra.get("season_total"):
                item["season_title"] = extra.get("season_title") or ""
                item["season_index"] = extra.get("season_index")
                item["season_total"] = extra.get("season_total")
        else:
            item = {
                "id": uuid.uuid4().hex[:10],
                "kind": "query",
                "query": raw,
                "title": raw,
                "artist": "",
                "cover_url": "",
            }
        items = normalize_idle_items(list(playlist.get("items") or []) + [item])
        playlist["items"] = items
        self._persist_config()
        label = item.get("title") or item.get("query") or item.get("bvid")
        self.log("info", f"已加入默认歌单：{label}")
        total = item.get("season_total") or 0
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = 0
        if total > 1:
            st = item.get("season_title") or "合集"
            idx = item.get("season_index") or 1
            self.log("info", f"该视频在合集「{st}」第 {idx}/{total} 集，已把全部 {total} 集列入播放列表")
            playing = self.hub().current
            include_start = not (
                playing and self._is_idle_item(playing.item) and self._idle_tied_to_spec(playing.item, item)
            )
            await self._materialize_collection_for_spec(item, result=result, include_start=include_start)
        await self._sync_idle_playback()
        await self._maybe_start()
        await self.push_state()
        return item

    async def remove_idle_item(self, item_id: str = "", index: int | None = None) -> None:
        playlist = self._idle_cfg()
        items = list(playlist.get("items") or [])
        removed = None
        if item_id:
            for i, it in enumerate(items):
                if str(it.get("id")) == str(item_id):
                    removed = items.pop(i)
                    break
        elif index is not None:
            idx = int(index)
            if 0 <= idx < len(items):
                removed = items.pop(idx)
        if not removed:
            raise ValueError("没有找到要删除的歌曲")
        playlist["items"] = items
        if self._idle_index >= len(items):
            self._idle_index = 0
        self._persist_config()
        self.log("info", f"已从默认歌单移除：{removed.get('title') or removed.get('query') or ''}")
        # 移除后立刻丢掉该条的合集续播和播放列表里的待播行；点歌列表不动。
        self._discard_follow_from_spec(removed)
        self._discard_queue_from_spec(removed)
        parked = self._paused_idle
        if parked and parked.get("item") and (
            not items or self._idle_tied_to_spec(parked["item"], removed) or not self._idle_track_belongs(parked["item"])
        ):
            self._clear_paused_idle()
        cur = self.hub().current
        if cur and self._is_idle_item(cur.item) and (
            not items or self._idle_tied_to_spec(cur.item, removed) or not self._idle_track_belongs(cur.item)
        ):
            self.log("info", f"已停止播放：{cur.item.title}")
            self._stash_outgoing(cur)
            self._playables.pop(cur.item.id, None)
            self.hub().current = None
            if self.hub().queue:
                await self.play_next()
            elif items and (self.cfg().get("idle_playlist") or {}).get("enabled"):
                self._idle_index = 0
                if not await self._try_play_idle():
                    await self.hub().send_player_cmd({"type": "player_cmd", "action": "stop"})
            else:
                await self.hub().send_player_cmd({"type": "player_cmd", "action": "stop"})
        else:
            await self._sync_idle_playback()
        await self.push_state()

    async def reorder_idle_items(self, ids: list[str]) -> None:
        playlist = self._idle_cfg()
        items = list(playlist.get("items") or [])
        by_id = {str(it.get("id")): it for it in items}
        new_list = [by_id[i] for i in ids if i in by_id]
        # 把未出现的原项追加在后，避免误删
        seen = set(ids)
        for it in items:
            if str(it.get("id")) not in seen:
                new_list.append(it)
        playlist["items"] = new_list
        self._persist_config()
        await self.push_state()

    async def _begin_playback(self, item: QueueItem, playable: Playable, *, position: float = 0) -> None:
        self._commit_outgoing_history(item)
        pos = max(0.0, float(position or 0))
        lyric = playable.lyric or ""
        self._playables[item.id] = playable
        self.hub().current = CurrentTrack(item=item, position=pos, paused=False, lyric=lyric)
        if self._is_idle_item(item) and pos > 1:
            self.log("info", f"继续播放默认歌单：{item.title} - {item.artist}")
        elif self._is_idle_item(item):
            self.log("info", f"开始播放默认歌单：{item.title} - {item.artist}")
        else:
            self.log("info", f"开始播放：{item.title} - {item.artist}")
        cmd = {
            "type": "player_cmd",
            "action": "play",
            "item": item.to_dict(),
            "stream_url": f"/api/stream/{item.id}",
            "lyric": lyric,
        }
        if pos > 0.2:
            cmd["position"] = pos
        await self.hub().send_player_cmd(cmd)
        await self.push_state()

    async def skip(self) -> None:
        cur = self.hub().current
        was_idle = bool(cur and self._is_idle_item(cur.item))
        if cur:
            self.log("info", f"已跳过：{cur.item.title}")
            self._stash_outgoing(cur)
            self._playables.pop(cur.item.id, None)
            if self._is_idle_item(cur.item) and self._paused_idle and self._paused_idle.get("item") is cur.item:
                self._clear_paused_idle()
        self.hub().current = None
        await self.play_next(idle_delay=not was_idle)

    async def pause(self) -> None:
        if self.hub().current:
            self.hub().current.paused = True
            await self.hub().send_player_cmd({"type": "player_cmd", "action": "pause"})
            await self.push_state()

    async def resume(self) -> None:
        if self.hub().current:
            self.hub().current.paused = False
            await self.hub().send_player_cmd({"type": "player_cmd", "action": "resume"})
            await self.push_state()

    async def delete_item(self, item_id: str) -> None:
        if self.hub().current and self.hub().current.item.id == item_id:
            await self.skip()
            return
        self.hub().queue = [i for i in self.hub().queue if i.id != item_id]
        self._playables.pop(item_id, None)
        self.log("info", "已从队列删除一首歌")
        await self.push_state()

    async def clear_queue(self) -> None:
        kept = [i for i in self.hub().queue if self._is_idle_item(i)]
        removed = len(self.hub().queue) - len(kept)
        self.hub().queue = kept
        if removed:
            self.log("info", "已清空待播点歌（默认歌单合集条目保留，当前曲不受影响）")
        else:
            self.log("info", "已清空待播队列（当前曲不受影响）")
        await self.push_state()

    async def reorder_queue(self, ids: list[str]) -> None:
        """重排即将播放的条目（点歌 + 合集待播），不改默认歌单配置。"""
        q = list(self.hub().queue)
        by_id = {i.id: i for i in q}
        new_list = [by_id[i] for i in ids if i in by_id]
        seen = set(ids)
        for it in q:
            if it.id not in seen:
                new_list.append(it)
        self.hub().queue = new_list
        await self.push_state()

    def on_player_progress(self, position: float, duration: float, paused: bool) -> None:
        cur = self.hub().current
        if not cur:
            return
        cur.position = float(position or 0)
        cur.paused = bool(paused)
        if duration and not cur.item.duration:
            cur.item.duration = int(duration)

    async def on_player_ended(self) -> None:
        cur = self.hub().current
        was_idle = bool(cur and self._is_idle_item(cur.item))
        if cur:
            self._stash_outgoing(cur)
            self._playables.pop(cur.item.id, None)
        self.hub().current = None
        await self.play_next(idle_delay=not was_idle)

    def _message(self, key: str, **kwargs: Any) -> str:
        raw = self.cfg().get("messages") if isinstance(self.cfg().get("messages"), dict) else {}
        tmpl = str((raw or {}).get(key) or DEFAULT_MESSAGES.get(key) or "")
        for name, value in kwargs.items():
            tmpl = tmpl.replace("{" + name + "}", str(value))
        return tmpl

    def _stash_outgoing(self, cur: CurrentTrack | None) -> None:
        if not cur or not cur.item:
            return
        playable = self._playables.get(cur.item.id)
        self._outgoing_for_history = {
            "item": cur.item,
            "playable": playable,
            "lyric": cur.lyric or (playable.lyric if playable else "") or "",
        }

    def _commit_outgoing_history(self, new_item: QueueItem | None) -> None:
        outgoing = self._outgoing_for_history
        self._outgoing_for_history = None
        if new_item is not None:
            self._history = [h for h in self._history if getattr(h.get("item"), "id", None) != new_item.id]
        if not outgoing or not outgoing.get("item"):
            return
        old_item = outgoing["item"]
        if new_item is not None and old_item.id == new_item.id:
            return
        self._history.append(outgoing)
        while len(self._history) > self._history_max:
            evicted = self._history.pop(0)
            ev_item = evicted.get("item")
            if not ev_item:
                continue
            ev_id = ev_item.id
            still_needed = (
                (self.hub().current and self.hub().current.item.id == ev_id)
                or any(i.id == ev_id for i in self.hub().queue)
                or (self._paused_idle and self._paused_idle.get("item") and self._paused_idle["item"].id == ev_id)
            )
            if not still_needed:
                self._playables.pop(ev_id, None)

    def _rewind_idle_pointer(self, item: QueueItem) -> None:
        extra = item.extra or {}
        ep = extra.get("idle_follow_ep")
        if ep:
            follow = list(self._idle_follow or [])
            follow.insert(0, ep)
            self._idle_follow = follow
            return
        if "idle_index" in extra:
            try:
                self._idle_index = int(extra["idle_index"])
            except (TypeError, ValueError):
                pass

    def _item_brief(self, item: Any) -> dict[str, Any]:
        if isinstance(item, QueueItem):
            return {
                "title": item.title,
                "artist": item.artist,
                "source": item.source,
                "cover_url": item.cover_url,
                "requester_name": item.requester_name,
            }
        if isinstance(item, dict):
            title = item.get("title") or item.get("query") or item.get("bvid") or ""
            return {
                "title": title,
                "artist": item.get("artist") or "",
                "source": item.get("source") or ("bili" if item.get("bvid") or item.get("aid") else ""),
                "cover_url": item.get("cover_url") or "",
                "requester_name": item.get("requester_name") or "默认歌单",
            }
        return {"title": "", "artist": "", "source": "", "cover_url": "", "requester_name": ""}

    def peek_next(self) -> dict[str, Any] | None:
        if self.hub().queue:
            return self._item_brief(self.hub().queue[0])
        follow = self._idle_follow or []
        if follow and self._idle_follow_is_current():
            return self._item_brief(follow[0])
        if follow and not self._idle_follow_is_current():
            self._clear_idle_follow()
        cfg = self.cfg().get("idle_playlist") or {}
        if not cfg.get("enabled"):
            return None
        items = [x for x in (cfg.get("items") or []) if isinstance(x, dict)]
        if not items:
            return None
        loop = bool(cfg.get("loop", True))
        n = len(items)
        idx = self._idle_index
        if loop:
            return self._item_brief(items[idx % n])
        if idx >= n:
            return None
        return self._item_brief(items[idx])

    async def previous(self) -> None:
        cur = self.hub().current
        if cur and float(cur.position or 0) > 3:
            playable = self._playables.get(cur.item.id)
            if playable and playable.url:
                self.log("info", f"重新播放：{cur.item.title} - {cur.item.artist}")
                await self._begin_playback(cur.item, playable, position=0)
                return
        if not self._history:
            if cur:
                self.log("info", "没有上一首")
                playable = self._playables.get(cur.item.id)
                if playable and playable.url:
                    await self._begin_playback(cur.item, playable, position=0)
                    return
            else:
                self.log("info", "没有上一首")
            await self.push_state()
            return

        rec = self._history.pop()
        prev_item: QueueItem = rec["item"]
        prev_playable: Playable | None = rec.get("playable") or self._playables.get(prev_item.id)
        if cur:
            if self._is_idle_item(cur.item):
                self._rewind_idle_pointer(cur.item)
                self._stash_outgoing(cur)
            else:
                self.hub().queue.insert(0, cur.item)
                self._outgoing_for_history = None
        self.hub().current = None
        self._cancel_idle_delay()
        if self._paused_idle and self._paused_idle.get("item") and self._paused_idle["item"].id == prev_item.id:
            self._paused_idle = None
        if not prev_playable or not prev_playable.url:
            adapter = get_adapter(prev_item.source)
            result = SearchResult(
                source=prev_item.source,
                source_id=prev_item.source_id,
                title=prev_item.title,
                artist=prev_item.artist,
                duration=prev_item.duration,
                cover_url=prev_item.cover_url,
                extra=prev_item.extra,
            )
            if adapter:
                prev_playable = await self._try_resolve(adapter, result)
        if not prev_playable or not prev_playable.url:
            self.log("warn", f"上一首「{prev_item.title}」无法播放")
            if cur:
                # put things back as best we can
                self._outgoing_for_history = None
            await self._maybe_start()
            await self.push_state()
            return
        if rec.get("lyric") and not prev_playable.lyric:
            prev_playable.lyric = rec["lyric"]
        self._playables[prev_item.id] = prev_playable
        self.log("info", f"上一首：{prev_item.title} - {prev_item.artist}")
        await self._begin_playback(prev_item, prev_playable, position=0)

    async def _notify(self, uname: str, short_body: str) -> None:
        """尽量发一条短弹幕；未登录则只在控制台提示。"""
        if not danmaku_sender.is_logged_in():
            self.log("warn", "尚未登录 B 站，无法自动发送弹幕提醒（请在控制台扫码登录）。覆盖层已展示完整信息。")
            return
        room = int(self.app.real_room_id or self.cfg()["room_id"])
        msg = fit_danmaku(uname, short_body, limit=20)
        try:
            await danmaku_sender.send_danmaku(room, msg)
            self.log("info", f"已发弹幕：{msg}")
        except Exception as exc:
            self.log("warn", f"弹幕发送失败：{exc}")



    def _priority_rooms(self) -> list[int]:
        rooms: list[int] = []
        for raw in (self.cfg().get("room_id"), getattr(self.app, "real_room_id", 0)):
            try:
                n = int(raw or 0)
            except (TypeError, ValueError):
                n = 0
            if n and n not in rooms:
                rooms.append(n)
        return rooms

    def _blacklist_reject(self, uid: int, uname: str, *texts: str) -> tuple[bool, str, str]:
        bl = self.cfg().get("blacklist") or {}
        if user_is_blocked(bl, uid, uname):
            return True, "blacklisted_user", f"{uname} 在黑名单中，已拒绝点歌"
        if song_is_blocked(bl, *texts):
            label = next((str(t) for t in texts if t), "")
            return True, "blacklisted_song", f"「{label}」在黑名单中，已拒绝 {uname} 的点歌"
        return False, "", ""

    def _insert_queued(self, item: QueueItem) -> None:
        q = self.hub().queue
        tier = int((item.extra or {}).get("priority_tier", 10))
        idx = insert_index([int((i.extra or {}).get("priority_tier", 10)) for i in q], tier)
        q.insert(idx, item)

    async def _emit_request_toast(self, item: QueueItem) -> None:
        if self._is_idle_item(item):
            return
        try:
            await self.hub().send_to_role(
                "overlay",
                {
                    "type": "request_toast",
                    "payload": {
                        "uname": item.requester_name,
                        "title": item.title,
                        "artist": item.artist,
                    },
                },
            )
        except Exception:
            pass

    async def add_current_to_idle(self) -> dict:
        cur = self.hub().current
        if not cur:
            raise ValueError("当前没有正在播放的歌")
        item = cur.item
        if item.source == "bili" and item.source_id:
            sid = str(item.source_id)
            extra = item.extra or {}
            text = str(extra.get("url") or "").strip()
            if not text:
                text = sid if sid.startswith("BV") or sid.lower().startswith("av") else sid
            return await self.add_idle_item(text)
        query = " ".join(x for x in [item.title, item.artist] if x).strip()
        if not query:
            raise ValueError("当前歌曲没有可添加的歌名")
        return await self.add_idle_item(query)

    async def blacklist_current_song(self) -> dict[str, Any]:
        cur = self.hub().current
        if not cur:
            raise ValueError("当前没有正在播放的歌")
        title = str(cur.item.title or "").strip()
        if not title:
            raise ValueError("当前歌曲没有歌名，无法拉黑")
        return await self.add_blacklisted_song(title)

    async def add_blacklisted_song(self, title: str) -> dict[str, Any]:
        from app.queue_rules import normalize_blacklist, normalize_song_entry

        text = normalize_song_entry(title)
        if not text:
            raise ValueError("请填写要拉黑的歌名或关键词")
        cfg = self.cfg()
        bl = normalize_blacklist(cfg)
        existing = [str(s).casefold() for s in bl.get("songs") or []]
        if text.casefold() not in existing:
            bl["songs"] = list(bl.get("songs") or []) + [text]
        cfg["blacklist"] = bl
        self._persist_config()
        self.log("info", f"已拉黑歌曲：{text}")
        await self.push_state()
        return bl

    async def remove_blacklisted_song(self, title: str) -> dict[str, Any]:
        from app.queue_rules import normalize_blacklist

        needle = str(title or "").strip().casefold()
        cfg = self.cfg()
        bl = normalize_blacklist(cfg)
        bl["songs"] = [s for s in (bl.get("songs") or []) if str(s).strip().casefold() != needle]
        cfg["blacklist"] = bl
        self._persist_config()
        self.log("info", f"已取消拉黑歌曲：{title}")
        await self.push_state()
        return bl

    async def add_blacklisted_user(self, uid: int = 0, name: str = "") -> dict[str, Any]:
        from app.queue_rules import normalize_blacklist, normalize_user_entry, user_is_blocked

        entry = normalize_user_entry({"uid": uid, "name": name}) or normalize_user_entry(name) or normalize_user_entry(uid)
        if not entry:
            raise ValueError("请填写要拉黑的用户 UID 或昵称")
        cfg = self.cfg()
        bl = normalize_blacklist(cfg)
        if not user_is_blocked(bl, entry.get("uid") or 0, entry.get("name") or ""):
            bl["users"] = list(bl.get("users") or []) + [entry]
        cfg["blacklist"] = bl
        self._persist_config()
        label = entry.get("name") or entry.get("uid")
        self.log("info", f"已拉黑用户：{label}")
        await self.push_state()
        return bl

    async def remove_blacklisted_user(self, uid: int = 0, name: str = "") -> dict[str, Any]:
        from app.queue_rules import normalize_blacklist

        cfg = self.cfg()
        bl = normalize_blacklist(cfg)
        want_uid = 0
        try:
            want_uid = int(uid or 0)
        except (TypeError, ValueError):
            want_uid = 0
        want_name = str(name or "").strip().casefold()
        kept = []
        for entry in bl.get("users") or []:
            e_uid = int(entry.get("uid") or 0) if isinstance(entry, dict) else 0
            e_name = str((entry or {}).get("name") or "").strip().casefold() if isinstance(entry, dict) else ""
            if want_uid and e_uid == want_uid:
                continue
            if want_name and e_name == want_name:
                continue
            kept.append(entry)
        bl["users"] = kept
        cfg["blacklist"] = bl
        self._persist_config()
        self.log("info", "已从黑名单移除用户")
        await self.push_state()
        return bl


def source_name(sid: str) -> str:
    adapter = get_adapter(sid)
    return adapter.display_name if adapter else sid


def _source_short(sid: str) -> str:
    return {
        "qq": "QQ",
        "qishui": "汽水",
        "kugou": "酷狗",
        "bili": "B站",
        "netease": "网易云",
    }.get(sid, source_name(sid))


def reason_short(reason: str) -> str:
    if "太频繁" in reason:
        return "点歌太频繁稍后"
    if "待播" in reason:
        return "待播已满请稍候"
    if "歌单已满" in reason:
        return "歌单已满请稍候"
    return "暂时无法点歌"

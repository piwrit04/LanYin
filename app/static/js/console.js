const SOURCE_LABEL = { qq: "QQ 音乐", qishui: "汽水音乐", kugou: "酷狗概念版", bili: "哔哩哔哩", netease: "网易云音乐" };
const STATUS_LABEL = { available: "可用", needs_login: "需登录", placeholder: "占位" };
const isStandaloneWindow = location.pathname === "/window"
  || new URLSearchParams(location.search).has("window")
  || (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
if (isStandaloneWindow) document.body.classList.add("is-standalone");

let ws;
let state = {};
let qrTimer = null;

const audio = document.getElementById("console-audio");
const VOLUME_KEY = "obs-song-volume";
const THEME_IDS = { wine: "酒夜", blush: "粉白", inkgold: "墨金", celadon: "青瓷" };
const TIER_LABELS = { 0: "总督", 1: "提督", 2: "舰长", 3: "粉丝牌" };
let audioBlocked = false;
let currentAudioId = "";
let idleBusy = false;
let lyricLines = [];
let overlaySaveTimer = null;

function loadVolume() {
  const v = Number(localStorage.getItem(VOLUME_KEY));
  return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0.8;
}

function applyVolume(v) {
  const vol = Math.min(1, Math.max(0, Number(v)));
  if (audio) audio.volume = vol;
  localStorage.setItem(VOLUME_KEY, String(vol));
  const slider = document.getElementById("volume");
  const label = document.getElementById("volume-val");
  if (slider && document.activeElement !== slider) slider.value = String(Math.round(vol * 100));
  if (label) label.textContent = Math.round(vol * 100) + "%";
  const icon = document.getElementById("vol-icon");
  if (icon) {
    icon.classList.remove("vol-high", "vol-low", "vol-mute");
    icon.classList.add(vol <= 0.001 ? "vol-mute" : vol < 0.4 ? "vol-low" : "vol-high");
  }
}

function setPlayButton(paused, disabled) {
  const btn = document.getElementById("btn-play");
  if (!btn) return;
  btn.disabled = !!disabled;
  const playing = !disabled && !paused;
  btn.classList.toggle("is-playing", playing);
  const label = playing ? "暂停" : "播放";
  btn.title = label;
  btn.setAttribute("aria-label", label);
}

function sendPlayer(payload) {
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: "player", ...payload }));
  }
}

function usingExternalPlayer() {
  if (state.dedicated_player_connected) return true;
  if (state.console_window_connected && !isStandaloneWindow) return true;
  return false;
}

function handlePlayerCmd(msg) {
  if (!audio) return;
  if (usingExternalPlayer()) {
    audio.pause();
    return;
  }
  if (msg.action === "play") {
    const item = msg.item || {};
    currentAudioId = item.id || "";
    lyricLines = parseLrc(msg.lyric || "");
    audio.src = msg.stream_url;
    const pendingSeek = Number(msg.position || 0);
    const play = () => {
      if (pendingSeek) {
        try { audio.currentTime = pendingSeek; } catch (e) {}
      }
      const p = audio.play();
      if (p && p.catch) {
        p.catch(() => {
          audioBlocked = true;
          const btn = document.getElementById("btn-sound");
          if (btn) btn.hidden = false;
          render();
        });
      } else {
        audioBlocked = false;
        const btn = document.getElementById("btn-sound");
        if (btn) btn.hidden = true;
      }
      if (msg.paused) audio.pause();
    };
    if (audio.readyState >= 1) play();
    else audio.onloadedmetadata = play;
  } else if (msg.action === "pause") {
    audio.pause();
  } else if (msg.action === "resume") {
    audio.play().then(() => {
      audioBlocked = false;
      const btn = document.getElementById("btn-sound");
      if (btn) btn.hidden = true;
    }).catch(() => {
      audioBlocked = true;
      const btn = document.getElementById("btn-sound");
      if (btn) btn.hidden = false;
    });
  } else if (msg.action === "stop") {
    audio.pause();
    audio.removeAttribute("src");
    currentAudioId = "";
    lyricLines = [];
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "hello", role: isStandaloneWindow ? "console_window" : "console" }));
    sendPlayer({ event: "ready" });
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") {
      state = msg.payload;
      if (usingExternalPlayer() && audio && !audio.paused) audio.pause();
      render();
    } else if (msg.type === "log") {
      if (!state.logs) state.logs = [];
      state.logs.push(msg.payload);
      renderLogs();
    } else if (msg.type === "progress") {
      if (state.current) {
        state.current.position = msg.payload.position;
        state.current.duration = msg.payload.duration || state.current.duration;
        state.current.paused = msg.payload.paused;
        if (msg.payload && msg.payload.lyric_line !== undefined) {
          state.current.lyric_line = msg.payload.lyric_line;
        }
        renderNow();
      }
    } else if (msg.type === "player_cmd") {
      handlePlayerCmd(msg);
    }
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

function send(payload) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(payload));
}

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = String(Math.floor(sec / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function setPill(id, on, text) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = "pill " + (on ? "on" : "off");
}

function isIdleTrack(item) {
  return !!(item && (item.idle || (item.extra && item.extra.idle) || item.requester_name === "默认歌单"));
}

function idleSettings() {
  return (state.settings && state.settings.idle_playlist) || {};
}

let lastSettingsSig = "";
let lastLogSig = "";

function render() {
  setPill("pill-danmaku", !!state.danmaku_connected, state.danmaku_connected ? "弹幕：已连接" : "弹幕：未连接");
  setPill("pill-login", !!state.bili_logged_in, state.bili_logged_in ? "B站发送：已登录" : "B站发送：未登录");
  if (state.dedicated_player_connected) {
    setPill("pill-player", true, "播放器：独立页出声");
  } else if (state.console_window_connected && !isStandaloneWindow) {
    setPill("pill-player", true, "播放器：独立窗口出声");
  } else if (audioBlocked) {
    setPill("pill-player", false, "播放器：请点击开始出声");
  } else {
    setPill("pill-player", true, "播放器：控制台出声");
  }
  setPill("pill-room", true, `房间：${state.room_id || "-"}`);
  renderNow();
  renderQueue();
  renderIdle();
  renderSelection();
  const settingsSig = JSON.stringify(state.settings || {});
  if (settingsSig !== lastSettingsSig) {
    lastSettingsSig = settingsSig;
    renderSources();
    renderSettings();
    renderBlacklist();
    applyTheme(((state.settings || {}).console_theme), false);
  }
  renderLogs();
}

function showLyricsEnabled() {
  const ov = (state.settings && state.settings.overlay) || {};
  return ov.show_lyrics !== false;
}

function renderNextOn(el, nxt) {
  if (!el) return;
  if (nxt && (nxt.title || nxt.artist)) {
    el.textContent = "下一首：" + [nxt.title || "", nxt.artist || ""].filter(Boolean).join(" - ");
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

function renderNow() {
  const cur = state.current;
  const titleTrack = document.getElementById("now-title-track");
  const artist = document.getElementById("now-artist");
  const meta = document.getElementById("now-meta");
  const kicker = document.getElementById("now-kicker");
  const cover = document.getElementById("cover");
  const bar = document.getElementById("now-bar");
  const posEl = document.getElementById("now-pos");
  const time = document.getElementById("now-time");
  const lyricEl = document.getElementById("now-lyric");
  const idleOn = !!idleSettings().enabled;
  const showLyric = showLyricsEnabled();

  if (!cur) {
    setScrollingTitle(titleTrack, "暂无歌曲");
    artist.textContent = "";
    if (idleOn) {
      kicker.textContent = "默认歌单已开启";
      meta.textContent = "没人点歌时会自动播放默认歌单里的曲子。";
    } else if (state.dedicated_player_connected || state.player_connected) {
      kicker.textContent = "准备就绪";
      meta.textContent = "等待点歌。观众发送「点歌 歌名」即可。";
    } else {
      kicker.textContent = "准备就绪";
      meta.textContent = "打开本页即可出声。若要推到直播里，请把 /player 加到 OBS。";
    }
    setCover(cover, "");
    cover.classList.remove("is-playing");
    setVinylSpin(cover, false, true);
    bar.style.width = "0%";
    posEl.textContent = "00:00";
    time.textContent = "00:00";
    if (lyricEl) { lyricEl.textContent = ""; lyricEl.hidden = true; }
    renderNextOn(document.getElementById("now-next"), state.next);
    setPlayButton(true, true);
    setNowActions(false);
    return;
  }

  const idle = isIdleTrack(cur);
  kicker.textContent = idle ? "默认歌单" : (cur.paused ? "已暂停" : "正在播放");
  setScrollingTitle(titleTrack, cur.title || "");
  artist.textContent = cur.artist || "未知";
  const who = idle ? "默认歌单" : (cur.requester_name ? `点歌人 ${cur.requester_name}` : "");
  meta.textContent = [SOURCE_LABEL[cur.source] || cur.source, who].filter(Boolean).join("  ·  ");
  setCover(cover, cur.cover_url);
  cover.classList.toggle("is-playing", !cur.paused);
  setVinylSpin(cover, !cur.paused);
  const dur = cur.duration || 0;
  const pos = cur.position || 0;
  bar.style.width = dur ? `${Math.min(100, (pos / dur) * 100)}%` : "0%";
  posEl.textContent = fmt(pos);
  time.textContent = fmt(dur);
  if (lyricEl) {
    const line = cur.lyric_line || "";
    lyricEl.textContent = showLyric ? line : "";
    lyricEl.hidden = !showLyric || !line;
  }
  renderNextOn(document.getElementById("now-next"), state.next);
  setPlayButton(!!cur.paused, false);
  setNowActions(true);
}

function trackRow(item, opts) {
  const current = !!opts.current;
  const canDelete = !!opts.canDelete;
  const canMove = !!opts.canMove;
  const img = item.cover_url ? ` style="background-image:url('${coverSrc(item.cover_url)}')"` : "";
  const idle = isIdleTrack(item);
  const extra = item.extra || {};
  const pri = TIER_LABELS[extra.priority_tier];
  const seasonTotal = Number(extra.season_total || item.season_total || 0);
  const seasonIndex = extra.season_index || item.season_index || 0;
  const seasonTitle = extra.season_title || item.season_title || "";
  const tags = [];
  if (current) tags.push('<span class="tag">正在播放</span>');
  if (idle && seasonTotal > 1) {
    tags.push('<span class="tag tag-idle">合集' + (seasonIndex ? ` 第${seasonIndex}/${seasonTotal}` : "") + "</span>");
  } else if (idle) {
    tags.push('<span class="tag tag-idle">默认歌单</span>');
  } else if (!current) {
    tags.push('<span class="tag tag-request">点歌</span>');
  }
  if (pri && !idle) tags.push('<span class="tag">' + pri + '</span>');
  const tag = tags.join("");
  let who = "";
  if (idle) {
    who = seasonTitle && seasonTotal > 1 ? seasonTitle : "默认歌单";
  } else {
    who = item.requester_name ? "点歌人 " + item.requester_name : "点歌";
  }
  const actions = [];
  if (canMove) {
    const atStart = !!opts.atStart;
    const atEnd = !!opts.atEnd;
    actions.push(`<button type="button" class="ghost q-move" data-move="up" data-id="${esc(item.id)}" title="上移插队" aria-label="上移插队"${atStart ? " disabled" : ""}>上移</button>`);
    actions.push(`<button type="button" class="ghost q-move" data-move="down" data-id="${esc(item.id)}" title="下移" aria-label="下移"${atEnd ? " disabled" : ""}>下移</button>`);
  }
  if (canDelete) actions.push(`<button type="button" class="ghost danger" data-del="${esc(item.id)}">删除</button>`);
  const tools = actions.length ? `<div class="q-actions">${actions.join("")}</div>` : "<span></span>";
  const handle = current
    ? `<span class="q-handle is-disabled" aria-hidden="true"></span>`
    : `<span class="q-handle" title="拖动插队" aria-label="拖动插队">⋮⋮</span>`;
  const cls = [current ? "current" : "", canMove ? "is-movable" : ""].filter(Boolean).join(" ");
  return `
    <li class="${cls}" ${canMove ? `draggable="true" data-id="${esc(item.id)}"` : ""}>
      ${handle}
      <div class="q-cover"${img}></div>
      <div>
        <div class="q-title">${esc(item.title)}${tag}</div>
        <div class="q-sub">${esc(item.artist || "")} · ${esc(SOURCE_LABEL[item.source] || item.source || "")}${who ? " · " + esc(who) : ""}</div>
      </div>
      ${tools}
    </li>`;
}

async function submitQueueOrder(ids) {
  await fetch("/api/queue/reorder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
}

function renderQueue() {
  const ul = document.getElementById("queue");
  const queue = state.queue || [];
  const cur = state.current;
  if (!cur && !queue.length) {
    const idleOn = !!idleSettings().enabled;
    const msg = idleOn
      ? "点歌队列是空的。没人点歌时会播放「默认歌单」里的曲子。"
      : "队列是空的。观众发送「点歌 歌名」即可。";
    ul.innerHTML = `<li class="muted empty-row">${msg}</li>`;
    return;
  }
  const parts = [];
  if (cur) parts.push(trackRow(cur, { current: true, canDelete: false, canMove: false }));
  queue.forEach((item, i) => parts.push(trackRow(item, {
    current: false, canDelete: true, canMove: true, atStart: i === 0, atEnd: i === queue.length - 1,
  })));
  ul.innerHTML = parts.join("");
  ul.querySelectorAll("[data-del]").forEach((btn) => {
    btn.onclick = () => send({ type: "command", action: "delete", item_id: btn.dataset.del });
  });
  ul.querySelectorAll("[data-move]").forEach((btn) => {
    btn.onclick = async () => {
      const ids = (state.queue || []).map((it) => it.id);
      const from = ids.indexOf(btn.dataset.id);
      if (from < 0) return;
      const to = btn.dataset.move === "up" ? from - 1 : from + 1;
      if (to < 0 || to >= ids.length) return;
      ids.splice(to, 0, ids.splice(from, 1)[0]);
      await submitQueueOrder(ids);
    };
  });
  let dragId = null;
  ul.querySelectorAll("li.is-movable[data-id]").forEach((li) => {
    li.ondragstart = () => { dragId = li.dataset.id; li.classList.add("dragging"); };
    li.ondragend = () => li.classList.remove("dragging");
    li.ondragover = (e) => e.preventDefault();
    li.ondrop = async (e) => {
      e.preventDefault();
      const ids = (state.queue || []).map((it) => it.id);
      const from = ids.indexOf(dragId);
      const to = ids.indexOf(li.dataset.id);
      if (from < 0 || to < 0 || from === to) return;
      ids.splice(to, 0, ids.splice(from, 1)[0]);
      await submitQueueOrder(ids);
    };
  });
}

function renderIdle() {
  const cfg = idleSettings();
  const enabled = document.getElementById("idle-enabled");
  const loop = document.getElementById("idle-loop");
  const delay = document.getElementById("idle-delay");
  if (document.activeElement !== enabled) enabled.checked = !!cfg.enabled;
  if (document.activeElement !== loop) loop.checked = cfg.loop !== false;
  if (delay && document.activeElement !== delay) {
    const n = Number(cfg.resume_delay_seconds);
    delay.value = String(Number.isFinite(n) ? n : 5);
  }
  const ul = document.getElementById("idle-list");
  const items = cfg.items || [];
  if (!items.length) {
    ul.innerHTML = '<li class="muted empty-row">还没有曲子。添加歌名或哔哩哔哩视频后，没人点歌时就会播放。</li>';
    return;
  }
  ul.innerHTML = items.map((item) => {
    const img = item.cover_url ? ` style="background-image:url('${coverSrc(item.cover_url)}')"` : "";
    const title = item.title || item.query || item.bvid || "未命名";
    let sub = item.kind === "bili"
      ? `${item.artist || "哔哩哔哩"} · 哔哩哔哩视频`
      : (item.query || "按歌名搜索");
    if (item.kind === "bili" && Number(item.season_total) > 1) {
      const st = item.season_title || "合集";
      sub = `${item.artist || "哔哩哔哩"} · ${st} 第 ${item.season_index || 1}/${item.season_total}，播完自动接下集`;
    }
    return `
      <li draggable="true" data-id="${esc(item.id)}">
        <div class="q-cover"${img}></div>
        <div>
          <div class="q-title">${esc(title)}</div>
          <div class="q-sub">${esc(sub)}</div>
        </div>
        <button class="ghost danger" data-idle-del="${esc(item.id)}">移除</button>
      </li>`;
  }).join("");
  ul.querySelectorAll("[data-idle-del]").forEach((btn) => {
    btn.onclick = async () => {
      await fetch("/api/idle_playlist/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: btn.dataset.idleDel }),
      });
    };
  });
  let dragId = null;
  ul.querySelectorAll("li[data-id]").forEach((li) => {
    li.ondragstart = () => { dragId = li.dataset.id; li.classList.add("dragging"); };
    li.ondragend = () => li.classList.remove("dragging");
    li.ondragover = (e) => e.preventDefault();
    li.ondrop = async (e) => {
      e.preventDefault();
      const ids = (idleSettings().items || []).map((it) => it.id);
      const from = ids.indexOf(dragId);
      const to = ids.indexOf(li.dataset.id);
      if (from < 0 || to < 0 || from === to) return;
      ids.splice(to, 0, ids.splice(from, 1)[0]);
      await fetch("/api/idle_playlist/reorder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
    };
  });
}

function renderSelection() {
  const box = document.getElementById("selection");
  const sel = state.selection;
  if (!sel) {
    box.innerHTML = "";
    return;
  }
  const remain = Math.max(0, Math.floor((sel.deadline * 1000 - Date.now()) / 1000));
  box.innerHTML = `
    <div class="choice-box">
      <strong>${esc(sel.requester_name)}</strong> 点了「${esc(sel.query)}」，请回复序号（剩余 ${remain} 秒）
      <ol>${(sel.options || []).map((o) => {
        const img = o.cover_url ? ` style="background-image:url('${coverSrc(o.cover_url)}')"` : "";
        return `<li>
          <button data-ch="${o.index}">选 ${o.index}</button>
          <div class="opt-cover"${img}></div>
          <div>
            <div>${esc(o.title)} <span class="src-tag">${esc(SOURCE_LABEL[o.source] || o.source || "")}</span></div>
            <div class="muted">${esc(o.artist || "未知")}</div>
          </div>
        </li>`;
      }).join("")}</ol>
      <div class="controls" style="margin-top:8px"><button class="ghost danger" id="btn-cancel-sel">取消这次选歌</button></div>
    </div>`;
  box.querySelectorAll("[data-ch]").forEach((btn) => {
    btn.onclick = () => send({ type: "command", action: "choose", choice: Number(btn.dataset.ch) });
  });
  const cancel = document.getElementById("btn-cancel-sel");
  if (cancel) cancel.onclick = () => send({ type: "command", action: "cancel_selection" });
}

function renderSources() {
  const ol = document.getElementById("sources");
  const list = state.sources || [];
  const statusOf = (s) => {
    if (s.logged_in) return { cls: "available", label: "已登录" };
    return { cls: s.status || "", label: STATUS_LABEL[s.status] || s.status || "需登录" };
  };
  ol.innerHTML = list.map((s, i) => {
    const st = statusOf(s);
    return `
    <li draggable="true" data-id="${s.id}">
      <strong>${i + 1}</strong>
      <span class="source-chip-name">${esc(s.name)}</span>
      <span class="badge ${st.cls}">${esc(st.label)}</span>
    </li>`;
  }).join("");
  let dragId = null;
  ol.querySelectorAll("li").forEach((li) => {
    li.ondragstart = () => { dragId = li.dataset.id; li.classList.add("dragging"); };
    li.ondragend = () => li.classList.remove("dragging");
    li.ondragover = (e) => e.preventDefault();
    li.ondrop = (e) => {
      e.preventDefault();
      const ids = (state.settings?.sources_priority || []).slice();
      const from = ids.indexOf(dragId);
      const to = ids.indexOf(li.dataset.id);
      if (from < 0 || to < 0 || from === to) return;
      ids.splice(to, 0, ids.splice(from, 1)[0]);
      send({ type: "command", action: "update_settings", settings: { sources_priority: ids } });
    };
  });
}

function renderSettings() {
  const s = state.settings || {};
  const anti = s.anti_spam || {};
  const setv = (id, v) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (document.activeElement !== el) el.value = v ?? "";
  };
  setv("room-id", state.room_id);
  setv("sel-timeout", s.selection_timeout_seconds);
  setv("max-pending", anti.max_pending_per_user);
  setv("max-queue", anti.max_queue_size);
  setv("cooldown", anti.cooldown_seconds);
  const welcome = document.getElementById("welcome-enabled");
  if (welcome && document.activeElement !== welcome) {
    welcome.checked = (s.welcome || {}).enabled !== false;
  }
  const extra = document.getElementById("welcome-extra");
  if (extra && document.activeElement !== extra) {
    extra.value = ((s.welcome || {}).extra_templates || []).join("\n");
  }
  const msgs = s.messages || {};
  const msgMap = {
    "msg-not-found": msgs.not_found || "未找到可播原曲",
    "msg-not-found-platform": msgs.not_found_platform || "{platform}未找到可播",
    "msg-please-choose": msgs.please_choose || "请回{nums}选歌",
    "msg-selection-timeout": msgs.selection_timeout || "选歌超时已取消",
    "msg-choice-unplayable": msgs.choice_unplayable || "选中曲无法播放",
    "msg-blacklisted-user": msgs.blacklisted_user || "你已被拉黑",
    "msg-blacklisted-song": msgs.blacklisted_song || "这首歌已拉黑",
  };
  Object.keys(msgMap).forEach((id) => setv(id, msgMap[id]));
  const action = ((s.selection || {}).timeout_action) || "skip";
  document.querySelectorAll('input[name="timeout-action"]').forEach((el) => {
    if (document.activeElement !== el) el.checked = el.value === action;
  });
  const ov = s.overlay || {};
  const sliders = {
    "ov-top": ov.top ?? 77.6,
    "ov-bottom": ov.bottom ?? 5.0,
    "ov-left": ov.left ?? 6.7,
    "ov-right": ov.right ?? 71.1,
  };
  Object.keys(sliders).forEach((id) => {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = sliders[id];
  });
  const lyrics = document.getElementById("ov-lyrics");
  if (lyrics && document.activeElement !== lyrics) lyrics.checked = ov.show_lyrics !== false;
  const pri = document.getElementById("priority-enabled");
  if (pri && document.activeElement !== pri) {
    pri.checked = ((s.priority_queue || {}).enabled !== false);
  }
  applyOverlayPreview(false);
}

function fitInsets(a, b, minSize) {
  a = Math.min(Math.max(a, 0), 100 - minSize);
  b = Math.min(Math.max(b, 0), 100 - minSize);
  const overflow = a + b - (100 - minSize);
  if (overflow > 0) {
    if (b >= a) b = Math.max(0, b - overflow);
    else a = Math.max(0, a - overflow);
  }
  return [Math.round(a * 10) / 10, Math.round(b * 10) / 10];
}

function readOverlayForm() {
  const num = (id, fallback) => {
    const n = Number(document.getElementById(id) && document.getElementById(id).value);
    return Number.isFinite(n) ? n : fallback;
  };
  const lyrics = document.getElementById("ov-lyrics");
  let left = num("ov-left", 6.7);
  let right = num("ov-right", 71.1);
  let top = num("ov-top", 77.6);
  let bottom = num("ov-bottom", 5.0);
  [left, right] = fitInsets(left, right, 8);
  [top, bottom] = fitInsets(top, bottom, 6);
  return {
    top,
    right,
    left,
    bottom,
    width: Math.round((100 - left - right) * 10) / 10,
    height: Math.round((100 - top - bottom) * 10) / 10,
    show_lyrics: lyrics ? !!lyrics.checked : true,
  };
}

function applyOverlayPreview(pushSave) {
  const ov = readOverlayForm();
  const setVal = (id, n) => {
    const el = document.getElementById(id);
    if (el) el.textContent = Number(n).toFixed(1) + "%";
  };
  setVal("ov-top-val", ov.top);
  setVal("ov-bottom-val", ov.bottom);
  setVal("ov-left-val", ov.left);
  setVal("ov-right-val", ov.right);
  const card = document.getElementById("preview-card");
  if (card) {
    card.classList.toggle("hide-lyrics", !ov.show_lyrics);
    card.classList.toggle("has-lyric", !!ov.show_lyrics);
  }
  const map = document.getElementById("preview-map-card");
  if (map) {
    map.style.setProperty("--ov-top", ov.top + "%");
    map.style.setProperty("--ov-right", ov.right + "%");
    map.style.setProperty("--ov-left", ov.left + "%");
    map.style.setProperty("--ov-bottom", ov.bottom + "%");
  }
  const lyric = document.getElementById("preview-lyric");
  if (lyric) lyric.hidden = !ov.show_lyrics;
  const cur = state.current;
  const coverEl = document.getElementById("preview-cover");
  const artist = document.getElementById("preview-artist");
  const titleTrack = document.getElementById("preview-title-track");
  const nextEl = document.getElementById("preview-next");
  if (cur) {
    setScrollingTitle(titleTrack, cur.title || "正在播放");
    const idle = isIdleTrack(cur);
    const who = idle ? "默认歌单" : (cur.requester_name || "");
    const src = SOURCE_LABEL[cur.source] || cur.source || "";
    if (artist) artist.textContent = [cur.artist || "", src, who].filter(Boolean).join(" · ");
    setCover(coverEl, cur.cover_url);
    renderNextOn(nextEl, state.next);
    if (lyric && ov.show_lyrics) lyric.textContent = cur.lyric_line || lyric.textContent || "歌词预览";
  } else {
    setScrollingTitle(titleTrack, "夜曲 · 超长歌名滚动预览示例");
    if (artist) artist.textContent = "周杰伦 · QQ · 点歌人 小明";
    setCover(coverEl, "");
    if (nextEl) { nextEl.textContent = "下一首：七里香 - 周杰伦"; nextEl.hidden = false; }
    if (lyric && ov.show_lyrics) lyric.textContent = "表层的抚慰 禁不起咀嚼";
  }
  if (pushSave) scheduleOverlaySave();
}

function scheduleOverlaySave() {
  clearTimeout(overlaySaveTimer);
  overlaySaveTimer = setTimeout(() => {
    send({ type: "command", action: "update_settings", settings: { overlay: readOverlayForm() } });
  }, 200);
}

function collectRoomSettings() {
  const actionEl = document.querySelector('input[name="timeout-action"]:checked');
  return {
    room_id: Number(document.getElementById("room-id").value),
    selection_timeout_seconds: Number(document.getElementById("sel-timeout").value),
    selection: { timeout_action: (actionEl && actionEl.value) || "skip" },
    anti_spam: {
      max_pending_per_user: Number(document.getElementById("max-pending").value),
      max_queue_size: Number(document.getElementById("max-queue").value),
      cooldown_seconds: Number(document.getElementById("cooldown").value),
    },
    priority_queue: {
      enabled: !!(document.getElementById("priority-enabled") && document.getElementById("priority-enabled").checked),
    },
  };
}

function collectMessageSettings() {
  const extraEl = document.getElementById("welcome-extra");
  return {
    welcome: {
      enabled: document.getElementById("welcome-enabled").checked,
      extra_templates: extraEl ? extraEl.value.split("\n") : [],
    },
    messages: {
      not_found: document.getElementById("msg-not-found").value,
      not_found_platform: document.getElementById("msg-not-found-platform").value,
      please_choose: document.getElementById("msg-please-choose").value,
      selection_timeout: document.getElementById("msg-selection-timeout").value,
      choice_unplayable: document.getElementById("msg-choice-unplayable").value,
      blacklisted_user: (document.getElementById("msg-blacklisted-user") || {}).value,
      blacklisted_song: (document.getElementById("msg-blacklisted-song") || {}).value,
    },
  };
}

function moveTabInk(btn, tabsId, inkId) {
  const ink = document.getElementById(inkId || "tab-ink");
  const tabs = document.getElementById(tabsId || "settings-tabs");
  if (!ink || !tabs || !btn) return;
  const tr = tabs.getBoundingClientRect();
  const br = btn.getBoundingClientRect();
  ink.style.width = br.width + "px";
  ink.style.transform = "translateX(" + (br.left - tr.left - 4) + "px)";
}

function activateSettingsTab(name) {
  document.querySelectorAll("#settings-tabs .tab-btn").forEach((btn) => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
    if (on) moveTabInk(btn, "settings-tabs", "tab-ink");
  });
  document.querySelectorAll(".settings-card .tab-pane").forEach((pane) => {
    pane.classList.toggle("is-active", pane.dataset.pane === name);
  });
  if (name === "overlay") {
    requestAnimationFrame(() => applyOverlayPreview(false));
  }
}

function activateListTab(name) {
  document.querySelectorAll("#lists-tabs .list-tab").forEach((btn) => {
    const on = btn.dataset.list === name;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
    if (on) moveTabInk(btn, "lists-tabs", "list-tab-ink");
  });
  document.querySelectorAll(".lists-card .list-pane").forEach((pane) => {
    pane.classList.toggle("is-active", pane.dataset.list === name);
  });
}

function applyTheme(name, persist) {
  const id = THEME_IDS[name] ? name : "wine";
  document.documentElement.dataset.theme = id;
  document.body.dataset.theme = id;
  document.querySelectorAll(".theme-swatch").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.theme === id);
  });
  if (persist) {
    send({ type: "command", action: "update_settings", settings: { console_theme: id } });
  }
}

function setNowActions(enabled) {
  const addBtn = document.getElementById("btn-add-idle");
  const blockBtn = document.getElementById("btn-block-song");
  if (addBtn) addBtn.disabled = !enabled;
  if (blockBtn) blockBtn.disabled = !enabled;
}

function blacklistSettings() {
  return (state.settings && state.settings.blacklist) || { users: [], songs: [] };
}

function renderBlacklist() {
  const bl = blacklistSettings();
  const userUl = document.getElementById("block-user-list");
  const songUl = document.getElementById("block-song-list");
  if (userUl) {
    const users = bl.users || [];
    if (!users.length) {
      userUl.innerHTML = '<li class="muted empty-row">还没有拉黑用户。</li>';
    } else {
      userUl.innerHTML = users.map((u) => {
        const label = [u.name, u.uid ? "UID " + u.uid : ""].filter(Boolean).join(" · ") || "用户";
        return `<li><span class="block-label">${esc(label)}</span><button type="button" class="ghost danger" data-unblock-user-uid="${esc(u.uid || 0)}" data-unblock-user-name="${esc(u.name || "")}">移除</button></li>`;
      }).join("");
      userUl.querySelectorAll("[data-unblock-user-name]").forEach((btn) => {
        btn.onclick = () => send({
          type: "command",
          action: "unblacklist_user",
          uid: Number(btn.dataset.unblockUserUid || 0),
          name: btn.dataset.unblockUserName || "",
        });
      });
    }
  }
  if (songUl) {
    const songs = bl.songs || [];
    if (!songs.length) {
      songUl.innerHTML = '<li class="muted empty-row">还没有拉黑歌曲。</li>';
    } else {
      songUl.innerHTML = songs.map((s) => {
        return `<li><span class="block-label">${esc(s)}</span><button type="button" class="ghost danger" data-unblock-song="${esc(s)}">移除</button></li>`;
      }).join("");
      songUl.querySelectorAll("[data-unblock-song]").forEach((btn) => {
        btn.onclick = () => send({ type: "command", action: "unblacklist_song", title: btn.dataset.unblockSong });
      });
    }
  }
}

function renderLogs() {
  const box = document.getElementById("logs");
  const logs = (state.logs || []).slice(-80);
  const sig = logs.length + ":" + (logs.length ? `${logs[logs.length - 1].ts}|${logs[logs.length - 1].message}` : "");
  if (sig === lastLogSig) return;
  lastLogSig = sig;
  box.innerHTML = logs.map((l) => {
    const t = new Date((l.ts || 0) * 1000);
    const hh = t.toLocaleTimeString("zh-CN", { hour12: false });
    const cls = l.level === "error" ? "err" : l.level === "warn" ? "warn" : "";
    return `<div class="${cls}">[${hh}] ${esc(l.message)}</div>`;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const btnPrev = document.getElementById("btn-prev");
if (btnPrev) btnPrev.onclick = () => send({ type: "command", action: "previous" });
document.getElementById("btn-play").onclick = () => {
  const cur = state.current;
  if (!cur) return;
  send({ type: "command", action: cur.paused ? "resume" : "pause" });
};
document.getElementById("btn-skip").onclick = () => send({ type: "command", action: "skip" });
document.getElementById("btn-clear").onclick = () => send({ type: "command", action: "clear" });
document.getElementById("btn-request").onclick = () => {
  const q = document.getElementById("manual-query").value.trim();
  if (q) {
    send({ type: "command", action: "request", query: q });
    document.getElementById("manual-query").value = "";
  }
};
document.getElementById("manual-query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("btn-request").click();
});
document.getElementById("now-progress").addEventListener("click", (e) => {
  const cur = state.current;
  if (!cur) return;
  const dur = Number(cur.duration || (audio && audio.duration) || 0);
  if (!dur || !isFinite(dur)) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  const t = ratio * dur;
  if (audio && !usingExternalPlayer() && audio.src) {
    try { audio.currentTime = t; } catch (err) {}
  }
});
const btnSave = document.getElementById("btn-save");
if (btnSave) {
  btnSave.onclick = () => {
    send({ type: "command", action: "update_settings", settings: collectRoomSettings() });
  };
}
const btnSaveMsg = document.getElementById("btn-save-messages");
if (btnSaveMsg) {
  btnSaveMsg.onclick = () => {
    send({ type: "command", action: "update_settings", settings: collectMessageSettings() });
  };
}
document.getElementById("welcome-enabled").addEventListener("change", () => {
  send({
    type: "command",
    action: "update_settings",
    settings: { welcome: { enabled: document.getElementById("welcome-enabled").checked } },
  });
});
["ov-top", "ov-bottom", "ov-left", "ov-right"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("input", () => applyOverlayPreview(true));
});
const lyricsSw = document.getElementById("ov-lyrics");
if (lyricsSw) lyricsSw.addEventListener("change", () => applyOverlayPreview(true));
document.querySelectorAll('input[name="timeout-action"]').forEach((el) => {
  el.addEventListener("change", () => {
    send({
      type: "command",
      action: "update_settings",
      settings: { selection: { timeout_action: el.value } },
    });
  });
});
document.querySelectorAll("#settings-tabs .tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => activateSettingsTab(btn.dataset.tab));
});
document.querySelectorAll("#lists-tabs .list-tab").forEach((btn) => {
  btn.addEventListener("click", () => activateListTab(btn.dataset.list));
});
window.addEventListener("resize", () => {
  const active = document.querySelector("#settings-tabs .tab-btn.is-active");
  if (active) moveTabInk(active, "settings-tabs", "tab-ink");
  const listActive = document.querySelector("#lists-tabs .list-tab.is-active");
  if (listActive) moveTabInk(listActive, "lists-tabs", "list-tab-ink");
});
requestAnimationFrame(() => {
  const active = document.querySelector("#settings-tabs .tab-btn.is-active");
  if (active) moveTabInk(active, "settings-tabs", "tab-ink");
  const listActive = document.querySelector("#lists-tabs .list-tab.is-active");
  if (listActive) moveTabInk(listActive, "lists-tabs", "list-tab-ink");
});
function sendIdleFlags() {
  send({
    type: "command",
    action: "update_settings",
    settings: {
      idle_playlist: {
        enabled: document.getElementById("idle-enabled").checked,
        loop: document.getElementById("idle-loop").checked,
        resume_delay_seconds: Number(document.getElementById("idle-delay").value || 5),
      },
    },
  });
}
document.getElementById("idle-enabled").addEventListener("change", sendIdleFlags);
document.getElementById("idle-loop").addEventListener("change", sendIdleFlags);
document.getElementById("idle-delay").addEventListener("change", sendIdleFlags);
document.getElementById("btn-idle-add").onclick = addIdleItem;
document.getElementById("idle-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") addIdleItem();
});
const btnAddIdle = document.getElementById("btn-add-idle");
if (btnAddIdle) {
  btnAddIdle.onclick = () => send({ type: "command", action: "idle_add_current" });
}
const btnBlockSong = document.getElementById("btn-block-song");
if (btnBlockSong) {
  btnBlockSong.onclick = () => {
    if (!state.current) return;
    const title = state.current.title || "";
    if (title && !confirm(`确定把「${title}」加入黑名单？正在播放的不会自动跳过。`)) return;
    send({ type: "command", action: "blacklist_current" });
  };
}
document.querySelectorAll(".theme-swatch").forEach((btn) => {
  btn.addEventListener("click", () => applyTheme(btn.dataset.theme, true));
});
const priEl = document.getElementById("priority-enabled");
if (priEl) {
  priEl.addEventListener("change", () => {
    send({
      type: "command",
      action: "update_settings",
      settings: { priority_queue: { enabled: !!priEl.checked } },
    });
  });
}
function addBlockedUser() {
  const input = document.getElementById("block-user-input");
  const text = (input && input.value.trim()) || "";
  if (!text) return;
  const payload = { type: "command", action: "blacklist_user" };
  if (/^\d+$/.test(text)) payload.uid = Number(text);
  else payload.name = text;
  send(payload);
  if (input) input.value = "";
}
function addBlockedSong() {
  const input = document.getElementById("block-song-input");
  const text = (input && input.value.trim()) || "";
  if (!text) return;
  send({ type: "command", action: "blacklist_song", title: text });
  if (input) input.value = "";
}
const btnBlockUser = document.getElementById("btn-block-user");
if (btnBlockUser) btnBlockUser.onclick = addBlockedUser;
const btnBlockSongAdd = document.getElementById("btn-block-song-add");
if (btnBlockSongAdd) btnBlockSongAdd.onclick = addBlockedSong;
const blockUserInput = document.getElementById("block-user-input");
if (blockUserInput) blockUserInput.addEventListener("keydown", (e) => { if (e.key === "Enter") addBlockedUser(); });
const blockSongInput = document.getElementById("block-song-input");
if (blockSongInput) blockSongInput.addEventListener("keydown", (e) => { if (e.key === "Enter") addBlockedSong(); });

async function addIdleItem() {
  const input = document.getElementById("idle-input");
  const btn = document.getElementById("btn-idle-add");
  const text = input.value.trim();
  if (!text || idleBusy) return;
  idleBusy = true;
  btn.disabled = true;
  btn.textContent = "添加中…";
  try {
    const res = await fetch("/api/idle_playlist/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      alert(data.error || "添加失败");
    } else {
      input.value = "";
    }
  } catch (err) {
    alert("添加失败，请稍后重试");
  } finally {
    idleBusy = false;
    btn.disabled = false;
    btn.textContent = "添加";
  }
}

document.getElementById("btn-reconnect").onclick = () => send({ type: "command", action: "reconnect_danmaku" });
document.getElementById("btn-logout").onclick = async () => {
  await fetch("/api/bili/logout", { method: "POST" });
};
document.getElementById("btn-qr").onclick = startQr;
document.getElementById("btn-save-cookie").onclick = async () => {
  const platform = document.getElementById("cookie-platform").value;
  const cookies = document.getElementById("cookie-text").value;
  await fetch(`/api/cookies/${platform}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cookies }),
  });
  document.getElementById("cookie-text").value = "";
};
document.getElementById("btn-clear-cookie").onclick = async () => {
  const platform = document.getElementById("cookie-platform").value;
  await fetch(`/api/cookies/${platform}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cookies: "" }),
  });
};

async function startQr() {
  const box = document.getElementById("qr-box");
  const img = document.getElementById("qr-img");
  const msg = document.getElementById("qr-msg");
  box.hidden = false;
  msg.textContent = "正在获取二维码…";
  const res = await fetch("/api/bili/qr/start").then((r) => r.json());
  if (!res.ok) {
    msg.textContent = res.error || "获取二维码失败";
    return;
  }
  img.src = res.image;
  msg.textContent = "请用手机哔哩哔哩扫码并确认";
  if (qrTimer) clearInterval(qrTimer);
  qrTimer = setInterval(async () => {
    const p = await fetch(`/api/bili/qr/poll?qrcode_key=${encodeURIComponent(res.qrcode_key)}`).then((r) => r.json());
    msg.textContent = p.message || p.status;
    if (p.status === "ok") {
      clearInterval(qrTimer);
      msg.textContent = "登录成功";
      setTimeout(() => { box.hidden = true; }, 1500);
    }
    if (p.status === "expired" || p.status === "error") {
      clearInterval(qrTimer);
    }
  }, 1200);
}

connect();
setInterval(() => { if (state.selection) renderSelection(); }, 500);

if (audio) {
  applyVolume(loadVolume());
  audio.ontimeupdate = () => {};
  audio.onended = () => {
    if (!usingExternalPlayer()) sendPlayer({ event: "ended" });
  };
  audio.onerror = () => {
    if (!usingExternalPlayer()) sendPlayer({ event: "error", message: "音频加载失败" });
  };
  setInterval(() => {
    if (usingExternalPlayer() || !currentAudioId || !audio.src) return;
    sendPlayer({
      event: "progress",
      position: audio.currentTime || 0,
      duration: audio.duration || 0,
      paused: audio.paused,
      lyric_line: currentLyric(lyricLines, audio.currentTime || 0),
    });
  }, 400);
}
const volEl = document.getElementById("volume");
if (volEl) {
  applyVolume(loadVolume());
  volEl.addEventListener("input", () => applyVolume(Number(volEl.value) / 100));
}
const soundBtn = document.getElementById("btn-sound");
if (soundBtn) {
  soundBtn.onclick = () => {
    audio.play().then(() => {
      audioBlocked = false;
      soundBtn.hidden = true;
      render();
    }).catch(() => {});
  };
}


if (audio) {
  attachVisualizer(audio, (bins, paused) => {
    if (usingExternalPlayer()) return;
    sendPlayer({ event: "visualizer", bins, paused });
  });
}

const popBtn = document.getElementById("btn-popout");
if (popBtn) {
  popBtn.onclick = async () => {
    popBtn.disabled = true;
    try {
      await fetch("/api/console_window", { method: "POST" });
    } catch (e) {}
    setTimeout(() => { popBtn.disabled = false; }, 1200);
  };
}

applyTheme("wine", false);

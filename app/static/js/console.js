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
const THEME_IDS = { plain: "素白", wine: "酒夜", blush: "粉白", inkgold: "墨金", celadon: "青瓷" };
const TIER_LABELS = { 0: "总督", 1: "提督", 2: "舰长", 3: "粉丝牌" };
let audioBlocked = false;
let currentAudioId = "";
let idleBusy = false;
let lyricLines = [];
let overlaySaveTimer = null;

function loadVolume() {
  const raw = localStorage.getItem(VOLUME_KEY);
  const v = raw === null || raw === "" ? NaN : Number(raw);
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
      if (!hasLogEntry(state.logs, msg.payload)) {
        state.logs.push(msg.payload);
        renderLogs();
      }
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

// 服务器先把日志写进快照、再单独广播 log；两条消息先后不定，按 id（旧服务器无 id 时按 ts+内容）去重。
function hasLogEntry(logs, entry) {
  if (!entry) return true;
  for (let i = logs.length - 1, n = 0; i >= 0 && n < 120; i--, n++) {
    const l = logs[i];
    if (entry.id != null && l.id != null) {
      if (l.id === entry.id) return true;
      if (l.id < entry.id) return false;
    } else if (l.ts === entry.ts && l.message === entry.message && l.level === entry.level) {
      return true;
    }
  }
  return false;
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
  if (!el) return;
  el.classList.toggle("on", !!on);
  el.classList.toggle("off", !on);
  el.title = text;
  el.setAttribute("aria-label", text);
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

function renderNextOn(el, nxt, style) {
  if (!el) return;
  if (nxt && (nxt.title || nxt.artist)) {
    const parts = [nxt.title || "", nxt.artist || ""].filter(Boolean);
    el.textContent = style === "dot"
      ? "下一首 · " + parts.join(" — ")
      : "下一首：" + parts.join(" - ");
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
    setGlow("", "idle");
    bar.style.width = "0%";
    posEl.textContent = "00:00";
    time.textContent = "00:00";
    if (lyricEl) { lyricEl.textContent = ""; lyricEl.hidden = true; }
    renderNextOn(document.getElementById("now-next"), state.next, "dot");
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
  setGlow(cur.cover_url || "", cur.paused ? "paused" : "playing");
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
  renderNextOn(document.getElementById("now-next"), state.next, "dot");
  setPlayButton(!!cur.paused, false);
  setNowActions(true);
}

const IC_NOTE = "M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z";

// 安静的空状态：小图标 + 一行标题 + 一行说明（可选弹幕示例，LeiBox「标签叠放」思路的静态版）。
function emptyState(opts) {
  const tag = opts.tag || "li";
  const mark = opts.kind === "logs"
    ? '<span class="empty-mark mark-logs" aria-hidden="true"><i></i><i></i><i></i></span>'
    : `<span class="empty-mark mark-folder" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="${IC_NOTE}"/></svg></span>`;
  const title = opts.title ? `<p class="empty-title">${esc(opts.title)}</p>` : "";
  const chips = (opts.chips || []).length
    ? `<span class="tag-stack" aria-label="弹幕示例">${opts.chips.map((c) => `<span class="cmd-chip">${esc(c)}</span>`).join("")}</span>`
    : "";
  const cls = ["muted", "empty-row", "empty-state", opts.kind === "logs" ? "empty-logs" : ""].filter(Boolean).join(" ");
  return `<${tag} class="${cls}">${mark}${title}<p class="empty-text">${esc(opts.text)}</p>${chips}</${tag}>`;
}

// ---- 封面取色光晕 ----
// 从封面（同源 /api/cover 代理）缩到 16×16 取主色；取不到（无封面/CORS/解码失败）用主题自带的中性色。
const glowColorCache = new Map();
let glowKey = null;

function sampleCoverColor(img) {
  const c = document.createElement("canvas");
  c.width = 16; c.height = 16;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, 16, 16);
  const d = ctx.getImageData(0, 0, 16, 16).data;
  let r = 0, g = 0, b = 0, w = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] < 128) continue;
    const R = d[i], G = d[i + 1], B = d[i + 2];
    const max = Math.max(R, G, B), min = Math.min(R, G, B);
    const light = (max + min) / 2;
    if (light < 18 || light > 242) continue;
    const sat = max === 0 ? 0 : (max - min) / max;
    const wt = 0.15 + sat * sat * 2;
    r += R * wt; g += G * wt; b += B * wt; w += wt;
  }
  if (!w) return null;
  return [Math.round(r / w), Math.round(g / w), Math.round(b / w)];
}

function applyGlowColor(rgb) {
  const glow = document.getElementById("cover-glow");
  if (!glow) return;
  const layers = glow.querySelectorAll(".glow-layer");
  if (layers.length < 2) return;
  const front = glow.querySelector(".glow-layer.is-front") || layers[0];
  const back = front === layers[0] ? layers[1] : layers[0];
  if (rgb) back.style.setProperty("--glow-rgb", rgb.join(" "));
  else back.style.removeProperty("--glow-rgb");
  back.classList.add("is-front");
  front.classList.remove("is-front");
  glow.classList.toggle("has-color", !!rgb);
}

function setGlow(url, mode) {
  const glow = document.getElementById("cover-glow");
  if (!glow) return;
  const stage = glow.closest(".stage");
  if (stage) {
    stage.classList.toggle("is-playing", mode === "playing");
    stage.classList.toggle("is-paused", mode === "paused");
  }
  const key = url || "";
  if (key === glowKey) return;
  glowKey = key;
  if (!key) { applyGlowColor(null); return; }
  if (glowColorCache.has(key)) { applyGlowColor(glowColorCache.get(key)); return; }
  const img = new Image();
  img.decoding = "async";
  img.onload = () => {
    let rgb = null;
    try { rgb = sampleCoverColor(img); } catch (e) { rgb = null; }
    glowColorCache.set(key, rgb);
    if (glowKey === key) applyGlowColor(rgb);
  };
  img.onerror = () => {
    glowColorCache.set(key, null);
    if (glowKey === key) applyGlowColor(null);
  };
  img.src = coverSrc(key);
}

function iconBtn(cls, attrs, label, path) {
  return `<button type="button" class="row-btn ${cls}" ${attrs} title="${label}" aria-label="${label}"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg></button>`;
}

const IC_UP = "M12 5 6 11h4v8h4v-8h4z";
const IC_DOWN = "M12 19 18 13h-4V5h-4v8H6z";
const IC_DEL = "M9.2 3h5.6l.8 1.6H20v1.8H4V4.6h4.4L9.2 3zM7 8h10v12.2c0 .7-.5 1.2-1.2 1.2H8.2c-.7 0-1.2-.5-1.2-1.2V8zm2.2 2.2v8h1.8v-8H9.2zm3.8 0v8h1.8v-8H13z";

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
  if (idle && seasonTotal > 1) {
    tags.push('<span class="tag tag-idle">合集' + (seasonIndex ? ` 第${seasonIndex}/${seasonTotal}` : "") + "</span>");
  } else if (idle) {
    tags.push('<span class="tag tag-idle">默认歌单</span>');
  } else {
    tags.push('<span class="tag tag-request">点歌</span>');
  }
  if (pri && !idle) tags.push('<span class="tag tag-priority">' + pri + "</span>");
  const tag = tags.join("");
  const source = SOURCE_LABEL[item.source] || item.source || "";
  let who = "";
  if (idle) {
    who = seasonTitle && seasonTotal > 1 ? seasonTitle : "默认歌单";
  } else if (item.requester_name) {
    who = "点歌人 " + item.requester_name;
  }
  const subParts = [item.artist || "", source, who].filter(Boolean);
  const actions = [];
  if (canMove) {
    const atStart = !!opts.atStart;
    const atEnd = !!opts.atEnd;
    actions.push(iconBtn("q-move", `data-move="up" data-id="${esc(item.id)}"${atStart ? " disabled" : ""}`, "上移插队", IC_UP));
    actions.push(iconBtn("q-move", `data-move="down" data-id="${esc(item.id)}"${atEnd ? " disabled" : ""}`, "下移", IC_DOWN));
  }
  if (canDelete) actions.push(iconBtn("danger", `data-del="${esc(item.id)}"`, "移除", IC_DEL));
  const tools = actions.length ? `<div class="q-actions">${actions.join("")}</div>` : "<span class=\"q-actions\"></span>";
  const idx = current
    ? `<span class="q-idx"><span class="eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span></span>`
    : `<span class="q-idx">${opts.index || ""}</span>`;
  const cls = [current ? "current" : "", canMove ? "is-movable" : ""].filter(Boolean).join(" ");
  const dur = item.duration ? fmt(item.duration) : "";
  return `
    <li class="${cls}" ${canMove ? `draggable="true" data-id="${esc(item.id)}"` : ""}>
      ${idx}
      <div class="q-cover"${img}></div>
      <div class="q-meta">
        <div class="q-title">${esc(item.title)}</div>
        <div class="q-sub">${esc(subParts.join(" · "))}</div>
      </div>
      <span class="q-tags">${tag}</span>
      <time class="q-time">${dur}</time>
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
  updateListCount();
  if (!cur && !queue.length) {
    const idleOn = !!idleSettings().enabled;
    ul.innerHTML = idleOn
      ? emptyState({ title: "默认歌单会接着播", text: "还没有歌曲。没人点歌时会播放「默认歌单」里的曲子。" })
      : emptyState({
        title: "等待第一首点歌",
        text: "还没有歌曲，观众发送「点歌 歌名」后会显示在这里。",
        chips: ["点歌 晴天", "点歌 夜曲", "点歌 BV 号"],
      });
    return;
  }
  const parts = [];
  if (cur) parts.push(trackRow(cur, { current: true, canDelete: false, canMove: false, index: 1 }));
  queue.forEach((item, i) => parts.push(trackRow(item, {
    current: false, canDelete: true, canMove: true, atStart: i === 0, atEnd: i === queue.length - 1,
    index: (cur ? 2 : 1) + i,
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
  updateListCount();
  if (!items.length) {
    ul.innerHTML = emptyState({ title: "默认歌单还是空的", text: "还没有曲子。添加歌名或哔哩哔哩视频后，没人点歌时就会播放。" });
    return;
  }
  ul.innerHTML = items.map((item, i) => {
    const img = item.cover_url ? ` style="background-image:url('${coverSrc(item.cover_url)}')"` : "";
    const title = item.title || item.query || item.bvid || "未命名";
    const source = item.kind === "bili"
      ? "哔哩哔哩"
      : (SOURCE_LABEL[item.source] || item.source || (item.query ? "按歌名搜索" : ""));
    let who = "默认歌单";
    if (item.kind === "bili" && Number(item.season_total) > 1) {
      who = `${item.season_title || "合集"} 第 ${item.season_index || 1}/${item.season_total}`;
    }
    const sub = [item.artist || "", source, who].filter(Boolean).join(" · ");
    const dur = item.duration ? fmt(item.duration) : "";
    return `
      <li draggable="true" data-id="${esc(item.id)}">
        <span class="q-idx">${i + 1}</span>
        <div class="q-cover"${img}></div>
        <div class="q-meta">
          <div class="q-title">${esc(title)}</div>
          <div class="q-sub">${esc(sub)}</div>
        </div>
        <span class="q-tags"><span class="tag tag-idle">默认歌单</span></span>
        <time class="q-time">${dur}</time>
        <div class="q-actions">${iconBtn("danger", `data-idle-del="${esc(item.id)}"`, "移除", IC_DEL)}</div>
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

// LeiBox「点阵加载」（ai-generating）思路的轻量版：3×3 点只做透明度呼吸。
// 选歌框每 500ms 重绘一次，用负 delay 让动画相位连续，不会每次从头跳。
// Design reference: LeiBox ai-generating / 点阵加载, adapted to CSS only.
function dotMatrix() {
  const phase = -(Date.now() % 1600);
  let dots = "";
  for (let i = 0; i < 9; i++) dots += `<i style="animation-delay:${phase + ((i % 3) + Math.floor(i / 3)) * 160}ms"></i>`;
  return `<span class="dot-matrix" aria-hidden="true">${dots}</span>`;
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
      <div class="choice-head">${dotMatrix()}<span><strong>${esc(sel.requester_name)}</strong> 点了「${esc(sel.query)}」，请回复序号（剩余 ${remain} 秒）</span></div>
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
  updateListCount();
}

function applyTheme(name, persist) {
  const id = THEME_IDS[name] ? name : "plain";
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

function logSpan(cls, text) {
  return `<span class="${cls}">${esc(text)}</span>`;
}

function formatLogMessage(raw) {
  const s = String(raw ?? "");
  const rules = [
    [/^(.+?)( 点歌：)(.+)$/, (m) => logSpan("log-user", m[1]) + esc(m[2]) + logSpan("log-song", m[3])],
    [/^(已为 )(.+?)( 加入：)(.+?)( - )(.+?)(（.+）)?$/, (m) => esc(m[1]) + logSpan("log-user", m[2]) + esc(m[3]) + logSpan("log-song", m[4]) + esc(m[5]) + logSpan("log-artist", m[6]) + esc(m[7] || "")],
    [/^(开始播放默认歌单：|继续播放默认歌单：|开始播放：|上一首：|重新播放：)(.+?)( - )(.+)$/, (m) => esc(m[1]) + logSpan("log-song", m[2]) + esc(m[3]) + logSpan("log-artist", m[4])],
    [/^(.+?)( 选择了 \d+：)(.+?)( - )(.+)$/, (m) => logSpan("log-user", m[1]) + esc(m[2]) + logSpan("log-song", m[3]) + esc(m[4]) + logSpan("log-artist", m[5])],
    [/^(.+?)( 选歌已超时，已自动播放第一个结果：)(.+?)( - )(.+)$/, (m) => logSpan("log-user", m[1]) + esc(m[2]) + logSpan("log-song", m[3]) + esc(m[4]) + logSpan("log-artist", m[5])],
    [/^(「)(.+?)(」有多个候选，等待 )(.+?)( 在 .+)$/, (m) => esc(m[1]) + logSpan("log-song", m[2]) + esc(m[3]) + logSpan("log-user", m[4]) + esc(m[5])],
    [/^(没有找到可完整播放的「)(.+?)(」)$/, (m) => esc(m[1]) + logSpan("log-song", m[2]) + esc(m[3])],
    [/^(「)(.+?)(」在黑名单中，已拒绝 )(.+?)( 的点歌)$/, (m) => esc(m[1]) + logSpan("log-song", m[2]) + esc(m[3]) + logSpan("log-user", m[4]) + esc(m[5])],
    [/^(.+?)( 选歌已超时.*| 撤回.*| 已撤回.*| 没有可撤回.*| 想撤回.*| 正在处理.*| 回复了.*)$/, (m) => logSpan("log-user", m[1]) + esc(m[2])],
    [/^(检测到 )(.+?)( 进入直播间.*)$/, (m) => esc(m[1]) + logSpan("log-user", m[2]) + esc(m[3])],
    [/^(收到点歌，已暂停默认歌单：)(.+?)(（\d{1,2}:\d{2}）)$/, (m) => esc(m[1]) + logSpan("log-song", m[2]) + esc(m[3])],
    [/^(已跳过：|已停止播放：|默认歌单已更换，停止播放旧曲子：)(.+)$/, (m) => esc(m[1]) + logSpan("log-song", m[2])],
    [/^(无法播放「|默认歌单「|暂停的默认歌单「|合集「)(.+?)(」.*)$/, (m) => esc(m[1]) + logSpan("log-song", m[2]) + esc(m[0].slice(m[1].length + m[2].length))],
    [/^(已拉黑歌曲：|已取消拉黑歌曲：|已加入默认歌单：|已从默认歌单移除：|已加入播放列表 )(.+)$/, (m) => esc(m[1]) + logSpan("log-song", m[2])],
    [/^(已拉黑用户：)(.+)$/, (m) => esc(m[1]) + logSpan("log-user", m[2])],
  ];
  for (const [re, fn] of rules) {
    const m = s.match(re);
    if (m) return fn(m);
  }
  return esc(s).replace(/「([^」]+)」/g, (_, t) => "「" + logSpan("log-song", t) + "」");
}

function logKind(level, msg) {
  if (level === "error") return "log-error";
  if (level === "warn") return "log-warn";
  if (/点歌：/.test(msg) || /加入：/.test(msg)) return "log-request";
  if (/开始播放|继续播放|重新播放|上一首：/.test(msg)) return "log-play";
  if (/欢迎|进入直播间/.test(msg)) return "log-welcome";
  if (/拉黑|黑名单/.test(msg)) return "log-block";
  if (/跳过|超时|撤回|取消|清空|停止|无法播放/.test(msg)) return "log-skip";
  return "log-system";
}

function renderLogs() {
  const box = document.getElementById("logs");
  const logs = (state.logs || []).slice(-80);
  const sig = logs.length + ":" + (logs.length ? `${logs[logs.length - 1].ts}|${logs[logs.length - 1].message}` : "");
  if (sig === lastLogSig) return;
  lastLogSig = sig;
  if (!logs.length) {
    box.innerHTML = emptyState({ tag: "div", kind: "logs", text: "还没有记录，点歌和播放会出现在这里。" });
    return;
  }
  box.innerHTML = logs.map((l) => {
    const t = new Date((l.ts || 0) * 1000);
    const hh = t.toLocaleTimeString("zh-CN", { hour12: false });
    const cls = logKind(l.level, l.message || "");
    return `<div class="${cls}"><time>${esc(hh)}</time>${formatLogMessage(l.message)}</div>`;
  }).join("");
  box.scrollTop = box.scrollHeight;
}

function updateListCount() {
  const el = document.getElementById("list-count");
  if (!el) return;
  const active = document.querySelector("#lists-tabs .list-tab.is-active");
  const tab = (active && active.dataset.list) || "queue";
  let n = 0;
  if (tab === "idle") n = (idleSettings().items || []).length;
  else n = (state.current ? 1 : 0) + (state.queue || []).length;
  el.textContent = n + " 首";
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
  const idleLabel = btn.querySelector(".btn-label") || btn;
  idleLabel.textContent = "添加中…";
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
    const idleLabelDone = btn.querySelector(".btn-label") || btn;
    idleLabelDone.textContent = "添加";
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


function setSettingsOpen(open) {
  const drawer = document.getElementById("settings-drawer");
  const backdrop = document.getElementById("settings-backdrop");
  const btn = document.getElementById("btn-settings");
  if (!drawer) return;
  document.body.classList.toggle("settings-open", !!open);
  drawer.classList.toggle("is-open", !!open);
  drawer.setAttribute("aria-hidden", open ? "false" : "true");
  if (backdrop) backdrop.hidden = !open;
  if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    requestAnimationFrame(() => {
      const active = document.querySelector("#settings-tabs .tab-btn.is-active");
      if (active) moveTabInk(active, "settings-tabs", "tab-ink");
      applyOverlayPreview(false);
    });
  }
}

const btnSettings = document.getElementById("btn-settings");
const btnSettingsClose = document.getElementById("btn-settings-close");
const settingsBackdrop = document.getElementById("settings-backdrop");
if (btnSettings) btnSettings.onclick = () => setSettingsOpen(true);
if (btnSettingsClose) btnSettingsClose.onclick = () => setSettingsOpen(false);
if (settingsBackdrop) settingsBackdrop.onclick = () => setSettingsOpen(false);

function setMoreMenu(open) {
  const menu = document.getElementById("now-more-menu");
  const btn = document.getElementById("btn-more");
  if (!menu || !btn) return;
  menu.hidden = !open;
  btn.setAttribute("aria-expanded", open ? "true" : "false");
}
const btnMore = document.getElementById("btn-more");
if (btnMore) {
  btnMore.onclick = (e) => {
    e.stopPropagation();
    const menu = document.getElementById("now-more-menu");
    setMoreMenu(!!(menu && menu.hidden));
  };
}
document.addEventListener("click", (e) => {
  const menu = document.getElementById("now-more-menu");
  const wrap = document.querySelector(".stage-more");
  if (menu && !menu.hidden && wrap && !wrap.contains(e.target)) setMoreMenu(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  setMoreMenu(false);
  if (document.body.classList.contains("settings-open")) setSettingsOpen(false);
});

// LeiBox「渐变卡片跟随」思路：只在鼠标所在的列表行里画一圈很淡的柔光，跟随指针横向位置。
// 只在 pointermove 时改一个 CSS 变量，不开常驻动画循环；空闲时零开销。
["queue", "idle-list"].forEach((id) => {
  const ul = document.getElementById(id);
  if (!ul) return;
  ul.addEventListener("pointermove", (e) => {
    const li = e.target.closest && e.target.closest("li:not(.empty-row)");
    if (!li) return;
    const r = li.getBoundingClientRect();
    li.style.setProperty("--mx", Math.round(e.clientX - r.left) + "px");
  }, { passive: true });
});

applyTheme("plain", false);

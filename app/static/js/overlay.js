const SOURCE_LABEL = { qq: "QQ 音乐", qishui: "汽水音乐", kugou: "酷狗概念版", bili: "哔哩哔哩", netease: "网易云音乐" };
const SOURCE_SHORT = { qq: "QQ", qishui: "汽水", kugou: "酷狗", bili: "B站", netease: "网易云" };

const compact = new URLSearchParams(location.search).get("mode") === "bar";
if (compact) document.body.classList.add("compact");

let state = {};
let lyrics = [];
let lastLyricId = "";

function isIdleTrack(item) {
  return !!(item && (item.idle || (item.extra && item.extra.idle) || item.requester_name === "默认歌单"));
}

function idleEnabled() {
  return !!(((state.settings || {}).idle_playlist || {}).enabled);
}

function overlaySettings() {
  return (state.settings && state.settings.overlay) || {};
}

function overlayStyle() {
  const params = new URLSearchParams(location.search);
  const mode = params.get("style") || params.get("mode");
  if (mode === "portrait" || mode === "card") return "portrait";
  if (mode === "bar") return "bar";
  return overlaySettings().style === "portrait" ? "portrait" : "classic";
}

function activeOverlay() {
  const ov = overlaySettings();
  const style = overlayStyle();
  const boxed = (ov.styles && style !== "bar" && ov.styles[style]) || {};
  return Object.assign({}, ov, boxed, { style: style });
}

function showLyricsEnabled() {
  return activeOverlay().show_lyrics !== false;
}

function applyOverlayStyle() {
  const style = overlayStyle();
  document.body.classList.toggle("style-portrait", style === "portrait");
  if (style === "bar" || compact) document.body.classList.add("compact");
  else document.body.classList.remove("compact");
  if (window.PortraitCard) {
    if (!PortraitCard.root) PortraitCard.init(document.getElementById("portrait-card"));
    if (style === "portrait") PortraitCard.applySettings(activeOverlay());
  }
}


function trackHasLyrics() {
  const el = document.getElementById("lyric");
  return !!(el && el.textContent.trim());
}

function syncLyricOrWave() {
  const card = document.getElementById("now-card");
  const lyricEl = document.getElementById("lyric");
  const show = showLyricsEnabled() && trackHasLyrics();
  if (card) card.classList.toggle("has-lyric", show);
  if (lyricEl && !show) lyricEl.textContent = "";
}


function applyOverlayGeometry() {
  const card = document.getElementById("now-stack") || document.getElementById("now-card");
  if (!card) return;
  const ov = activeOverlay();
  const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : NaN;
  };
  let left = num(ov.left), right = num(ov.right), top = num(ov.top), bottom = num(ov.bottom);
  let width = num(ov.width), height = num(ov.height);
  if (!Number.isFinite(width) && Number.isFinite(left) && Number.isFinite(right)) width = 100 - left - right;
  if (!Number.isFinite(height) && Number.isFinite(top) && Number.isFinite(bottom)) height = 100 - top - bottom;
  if (!Number.isFinite(top) && Number.isFinite(bottom) && Number.isFinite(height)) top = 100 - bottom - height;
  if (!Number.isFinite(right) && Number.isFinite(left) && Number.isFinite(width)) right = 100 - left - width;
  if (Number.isFinite(left) && Number.isFinite(width)) right = 100 - left - width;
  if (Number.isFinite(bottom) && Number.isFinite(height)) top = 100 - bottom - height;
  const set = (name, val, fallback) => {
    const n = Number.isFinite(val) ? val : fallback;
    card.style.setProperty(name, n + "%");
  };
  set("--ov-top", top, 77.6);
  set("--ov-right", right, 71.1);
  set("--ov-left", left, 6.7);
  set("--ov-bottom", bottom, 5.0);
  set("--ov-width", width, 22.2);
  set("--ov-height", height, 17.4);
  document.body.classList.toggle("hide-lyrics", !showLyricsEnabled());
}

function renderNextLine() {
  const el = document.getElementById("next-line");
  if (!el) return;
  const nxt = state.next;
  if (nxt && (nxt.title || nxt.artist)) {
    const bits = [nxt.title || "", nxt.artist || ""].filter(Boolean).join(" - ");
    el.textContent = "下一首：" + bits;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => ws.send(JSON.stringify({ type: "hello", role: "overlay" }));
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") {
      state = msg.payload;
      if (state.current && state.current.id !== lastLyricId) {
        lastLyricId = state.current.id;
        lyrics = [];
      }
      render();
    } else if (msg.type === "progress") {
      if (state.current) {
        state.current.position = msg.payload.position;
        state.current.duration = msg.payload.duration || state.current.duration;
        if (msg.payload && msg.payload.paused !== undefined) {
          state.current.paused = msg.payload.paused;
          const coverEl = document.getElementById("cover");
          if (coverEl) {
            const playing = !state.current.paused;
            coverEl.classList.toggle("is-playing", playing);
            setVinylSpin(coverEl, playing);
          }
        }
      }
      if (msg.payload && msg.payload.lyric_line !== undefined) {
        const lyricEl = document.getElementById("lyric");
        if (lyricEl && showLyricsEnabled()) lyricEl.textContent = msg.payload.lyric_line || "";
        syncLyricOrWave();
      }
      updateProgress();
    } else if (msg.type === "visualizer") {
      waveBins = Array.isArray(msg.bins) ? msg.bins : [];
      wavePaused = !!msg.paused;
      lastBinAt = performance.now();
    } else if (msg.type === "request_toast") {
      showRequestToast(msg.payload || {});
    }
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}

function render() {
  applyOverlayGeometry();
  applyOverlayStyle();
  if (overlayStyle() === "portrait") setGuideOpen(false);
  else syncSongGuide();
  const cur = state.current;
  const waitingIdle = !cur && idleEnabled() && !(state.queue || []).length && !state.selection;
  const empty = !cur && !(state.queue || []).length && !state.selection && !waitingIdle;
  document.body.classList.toggle("empty", empty && !state.selection);
  document.body.classList.toggle("idle-standby", waitingIdle);
  const title = document.getElementById("title");
  const artist = document.getElementById("artist");
  const meta = document.getElementById("meta");
  const kicker = document.getElementById("kicker");
  const cover = document.getElementById("cover");
  const card = document.getElementById("now-card");
  if (cur) {
    const idle = isIdleTrack(cur);
    kicker.textContent = idle ? "默认歌单" : "正在播放";
    setScrollingTitle(cur.title || "");
    const who = idle ? "默认歌单" : (cur.requester_name || "");
    const src = SOURCE_SHORT[cur.source] || SOURCE_LABEL[cur.source] || cur.source || "";
    artist.textContent = [cur.artist || "", src, who].filter(Boolean).join(" · ");
    meta.textContent = [SOURCE_LABEL[cur.source] || cur.source, who].filter(Boolean).join("  ·  ");
    setCover(cover, cur.cover_url);
    applyCoverColors(cur.cover_url || cur.cover_data || "");
    cover.classList.toggle("is-playing", !cur.paused);
    setVinylSpin(cover, !cur.paused);
    card.classList.add("is-playing");
    const lyricEl = document.getElementById("lyric");
    if (lyricEl && !showLyricsEnabled()) lyricEl.textContent = "";
  } else if (waitingIdle) {
    kicker.textContent = "默认歌单";
    setScrollingTitle("闲时音乐准备中");
    artist.textContent = "";
    meta.textContent = "没人点歌时播放默认歌单 · 发送弹幕：点歌 歌名";
    setCover(cover, "");
    applyCoverColors("");
    cover.classList.remove("is-playing");
    setVinylSpin(cover, false, true);
    document.getElementById("lyric").textContent = "";
    card.classList.remove("is-playing");
  } else {
    kicker.textContent = "正在播放";
    setScrollingTitle("等待点歌");
    artist.textContent = "";
    meta.textContent = "发送弹幕：点歌 歌名";
    setCover(cover, "");
    applyCoverColors("");
    cover.classList.remove("is-playing");
    setVinylSpin(cover, false, true);
    document.getElementById("lyric").textContent = "";
    card.classList.remove("is-playing");
  }
  renderNextLine();
  const qcard = document.getElementById("queue-card");
  if (qcard) qcard.style.display = "none";
  const sel = state.selection;
  const sc = document.getElementById("select-card");
  if (sel && !compact) {
    sc.hidden = false;
    const remain = Math.max(0, Math.ceil((sel.deadline * 1000 - Date.now()) / 1000));
    document.getElementById("sel-lead").textContent = `@${sel.requester_name} 请回复序号选歌（${remain}s）`;
    document.getElementById("sel-sub").textContent = `你点的是「${sel.query}」，有多个合适结果，回复 1 / 2 / … 即可（只需数字）。`;
    document.getElementById("sel-list").innerHTML = (sel.options || []).map((o) => {
      const img = o.cover_url ? ` style="background-image:url('${coverSrc(o.cover_url)}')"` : "";
      return `<li>
        <span class="idx">${o.index}</span>
        <div class="opt-cover"${img}></div>
        <div>
          <div class="opt-title">${esc(o.title)} <span class="src-tag">${esc(SOURCE_LABEL[o.source] || o.source || "")}</span></div>
          <div class="opt-sub">${esc(o.artist || "未知")}</div>
        </div>
      </li>`;
    }).join("");
  } else {
    sc.hidden = true;
  }
  syncLyricOrWave();
  updateProgress();
  if (window.PortraitCard && overlayStyle() === "portrait") {
    PortraitCard.renderTrack(cur, {
      paused: !!(cur && cur.paused),
      coverSrc: (u) => u ? coverSrc(u) : "",
      position: cur ? Number(cur.position) || 0 : 0,
      duration: cur ? Number(cur.duration) || 0 : 0,
    });
  }
}


function updateProgress() {
  const cur = state.current;
  const dur = (cur && (cur.duration || 0)) || 0;
  const pos = (cur && (cur.position || 0)) || 0;
  const bar = document.getElementById("bar");
  const t0 = document.getElementById("t0");
  const t1 = document.getElementById("t1");
  if (bar) bar.style.width = dur ? `${Math.min(100, (pos / dur) * 100)}%` : "0%";
  if (t0) t0.textContent = fmt(pos);
  if (t1) t1.textContent = fmt(dur);
  if (window.PortraitCard && overlayStyle() === "portrait") {
    PortraitCard.renderProgress(pos, dur);
    PortraitCard.renderLyricAt(pos, cur || null);
    const card = document.getElementById("portrait-card");
    if (card) card.classList.toggle("is-paused", !!(cur && cur.paused));
  }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}



let waveBins = [];
let wavePaused = true;
let lastBinAt = 0;
let lastCoverColorKey = "";

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h = 0, s = 0;
  const l = (max + min) / 2;
  const d = max - min;
  if (d) {
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h /= 6;
  }
  return [h, s, l];
}

function hslToRgb(h, s, l) {
  const hue = (p, q, t) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  let r, g, b;
  if (!s) {
    r = g = b = l;
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue(p, q, h + 1 / 3);
    g = hue(p, q, h);
    b = hue(p, q, h - 1 / 3);
  }
  return { r: Math.round(r * 255), g: Math.round(g * 255), b: Math.round(b * 255) };
}

function colorRoot() {
  return document.getElementById("now-stack") || document.getElementById("now-card");
}

function colorTargets() {
  return [colorRoot(), document.getElementById("portrait-card")].filter(Boolean);
}

function clearCoverColors() {
  const keys = ["--np-bg", "--np-accent", "--np-text", "--np-muted", "--np-border"];
  colorTargets().forEach((card) => keys.forEach((k) => card.style.removeProperty(k)));
}

function applyCoverColors(url) {
  const card = colorRoot();
  if (!card) return;
  if (!url) {
    lastCoverColorKey = "";
    clearCoverColors();
    return;
  }
  const src = coverSrc(url);
  if (src === lastCoverColorKey) return;
  lastCoverColorKey = src;
  const img = new Image();
  img.onload = () => {
    try {
      const c = document.createElement("canvas");
      c.width = 32;
      c.height = 32;
      const cx = c.getContext("2d");
      cx.drawImage(img, 0, 0, 32, 32);
      const data = cx.getImageData(0, 0, 32, 32).data;
      const buckets = new Map();
      for (let i = 0; i < data.length; i += 4) {
        if (data[i + 3] < 200) continue;
        const r = data[i], g = data[i + 1], b = data[i + 2];
        const [hh, ss, ll] = rgbToHsl(r, g, b);
        if (ll < 0.08 || ll > 0.92 || ss < 0.12) continue;
        const key = `${Math.round(hh * 24)}_${Math.round(ss * 8)}`;
        const prev = buckets.get(key) || { n: 0, r: 0, g: 0, b: 0, s: 0, h: 0 };
        prev.n += 1;
        prev.r += r; prev.g += g; prev.b += b;
        prev.s += ss; prev.h += hh;
        buckets.set(key, prev);
      }
      let top = null;
      for (const v of buckets.values()) {
        const score = v.n * (0.35 + v.s / v.n);
        if (!top || score > top.score) {
          top = { score, h: v.h / v.n, s: v.s / v.n };
        }
      }
      const h = top ? top.h : 0.78;
      const s = top ? Math.min(0.75, Math.max(0.35, top.s)) : 0.55;
      const bg = hslToRgb(h, Math.min(0.5, s * 0.85), 0.16);
      const accent = hslToRgb(h, Math.min(0.9, s * 1.15), 0.62);
      const muted = hslToRgb(h, Math.min(0.25, s * 0.35), 0.72);
      const targets = colorTargets();
      targets.forEach((el) => {
        el.style.setProperty("--np-bg", `rgba(${bg.r}, ${bg.g}, ${bg.b}, 0.94)`);
        el.style.setProperty("--np-accent", `rgb(${accent.r}, ${accent.g}, ${accent.b})`);
        el.style.setProperty("--np-text", "#ffffff");
        el.style.setProperty("--np-muted", `rgb(${muted.r}, ${muted.g}, ${muted.b})`);
        el.style.setProperty("--np-border", `rgba(${accent.r}, ${accent.g}, ${accent.b}, 0.35)`);
      });
    } catch (e) {
      clearCoverColors();
    }
  };
  img.onerror = () => {
    if (lastCoverColorKey === src) clearCoverColors();
  };
  img.src = src;
}

function drawWave() {
  const portrait = overlayStyle() === "portrait";
  const canvas = portrait
    ? (window.PortraitCard && PortraitCard.waveCanvas())
    : document.getElementById("wave");
  if (!canvas || canvas.hidden) return;
  const card = portrait ? document.getElementById("portrait-card") : document.getElementById("now-card");
  if (!portrait && card && card.classList.contains("has-lyric")) return;
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 200;
  const cssH = canvas.clientHeight || 22;
  const tw = Math.max(1, Math.round(cssW * dpr));
  const th = Math.max(1, Math.round(cssH * dpr));
  if (canvas.width !== tw || canvas.height !== th) {
    canvas.width = tw;
    canvas.height = th;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const n = 28;
  const live = waveBins.length && !wavePaused;
  const bins = live ? waveBins : [];
  const gap = 2.5;
  const barW = Math.max(2, (cssW - gap * (n - 1)) / n);
  const mid = cssH / 2;
  ctx.fillStyle = (card && getComputedStyle(card).getPropertyValue("--np-accent").trim()) || "#ff5dad";
  for (let i = 0; i < n; i++) {
    let v = (Number(bins[i] ?? bins[Math.floor(i * (bins.length || 1) / n)]) || 0) / 100;
    if (!live) v = 0.08;
    else v = Math.min(1, Math.pow(Math.max(0, v), 0.65) * 1.25);
    const h = Math.max(2, v * (cssH - 2));
    const x = i * (barW + gap);
    const y = mid - h / 2;
    const r = Math.min(1.5, barW / 2);
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, barW, h, r);
    else ctx.rect(x, y, barW, h);
    ctx.fill();
  }
}

function loopWave() {
  if (!wavePaused && lastBinAt) {
    const age = performance.now() - lastBinAt;
    if (age > 450 && age < 2200 && waveBins.length) {
      waveBins = waveBins.map((v) => Math.max(8, v * 0.86));
    }
  }
  drawWave();
  requestAnimationFrame(loopWave);
}

if (window.PortraitCard) PortraitCard.init(document.getElementById("portrait-card"));
connect();
setInterval(() => { if (state.selection) render(); }, 400);
loopWave();


let toastTimer = null;
let toastHideTimer = null;
function showRequestToast(payload) {
  const el = document.getElementById("request-toast");
  const whoEl = document.getElementById("toast-who");
  const songEl = document.getElementById("toast-song");
  if (!el) return;
  const uname = (payload && payload.uname) || "观众";
  const title = (payload && payload.title) || "";
  const artist = (payload && payload.artist) || "";
  if (whoEl) whoEl.textContent = uname + " 点了";
  if (songEl) songEl.textContent = [title, artist].filter(Boolean).join(" - ") || "一首歌";
  el.hidden = false;
  el.classList.remove("is-on");
  requestAnimationFrame(() => el.classList.add("is-on"));
  if (toastTimer) clearTimeout(toastTimer);
  if (toastHideTimer) clearTimeout(toastHideTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("is-on");
    toastHideTimer = setTimeout(() => { el.hidden = true; }, 300);
  }, 4500);
}



const GUIDE_HTML = "弹幕点歌：<span class=\"g-cmd\">点歌 歌名 [歌手] [平台]</span><span class=\"g-sep\" aria-hidden=\"true\"></span>平台：<span class=\"g-plat\">B站/QQ/网易云/酷狗</span><span class=\"g-sep\" aria-hidden=\"true\"></span>例：<span class=\"g-eg\">点歌 晴天 周杰伦 B站</span>";
const GUIDE_EXTEND_MS = 380;

let guideTimer = 0;
let guideKey = "";
let guideRunning = false;

function guideSettings() {
  const ov = overlaySettings();
  const plays = parseInt(ov.guide_play_count, 10);
  const delay = Number(ov.guide_hide_delay_seconds);
  const reappear = Number(ov.guide_reappear_seconds);
  const speed = Number(ov.guide_scroll_speed);
  return {
    enabled: ov.guide_enabled !== false,
    plays: Number.isFinite(plays) ? Math.max(1, Math.min(20, plays)) : 1,
    hideDelay: Number.isFinite(delay) ? Math.max(0, delay) : 3,
    reappear: Number.isFinite(reappear) ? Math.max(0, reappear) : 90,
    speed: Number.isFinite(speed) ? Math.max(16, Math.min(120, speed)) : 40,
  };
}

function clearGuideTimers() {
  clearTimeout(guideTimer);
  guideTimer = 0;
}

function setGuideOpen(open) {
  const stack = document.getElementById("now-stack");
  if (stack) stack.classList.toggle("guide-open", open);
}

function fillGuideText() {
  const inner = document.getElementById("guide-seq");
  const clone = document.getElementById("guide-seq-clone");
  if (inner && inner.innerHTML !== GUIDE_HTML) inner.innerHTML = GUIDE_HTML;
  if (clone && clone.innerHTML !== GUIDE_HTML) clone.innerHTML = GUIDE_HTML;
}

function measureGuideUnit() {
  const track = document.getElementById("guide-track");
  const unit = track && track.querySelector(".guide-unit");
  if (unit && unit.offsetWidth) return unit.offsetWidth;
  if (track && track.scrollWidth) return track.scrollWidth / 2;
  return 0;
}

function resetGuideTrack() {
  const track = document.getElementById("guide-track");
  if (!track) return;
  track.classList.remove("is-looping");
  track.style.removeProperty("animation");
  track.style.removeProperty("transition");
  track.style.removeProperty("transform");
  track.style.removeProperty("--guide-duration");
}

function syncSongGuide() {
  if (overlayStyle() === "portrait") return;
  const cfg = guideSettings();
  const key = `${cfg.enabled}|${cfg.plays}|${cfg.hideDelay}|${cfg.reappear}|${cfg.speed}`;
  const stack = document.getElementById("now-stack");
  if (!stack) return;
  if (!cfg.enabled) {
    guideKey = key;
    guideRunning = false;
    clearGuideTimers();
    setGuideOpen(false);
    resetGuideTrack();
    return;
  }
  fillGuideText();
  if (key !== guideKey) {
    guideKey = key;
    clearGuideTimers();
    guideRunning = false;
    startGuideCycle();
    return;
  }
  if (!guideRunning) startGuideCycle();
}

function startGuideCycle() {
  if (guideRunning) return;
  if (!guideSettings().enabled) return;
  guideRunning = true;
  fillGuideText();
  resetGuideTrack();
  setGuideOpen(true);
  clearGuideTimers();
  guideTimer = setTimeout(runGuideScroll, GUIDE_EXTEND_MS + 80);
}


function startGuideLoop(track, unit, speed) {
  const apply = (width) => {
    const duration = width > 0 ? width / (speed || 40) : 18;
    track.classList.remove("is-looping");
    track.style.removeProperty("animation");
    track.style.removeProperty("transition");
    track.style.removeProperty("transform");
    track.style.setProperty("--guide-duration", duration + "s");
    void track.offsetWidth;
    track.classList.add("is-looping");
  };
  apply(unit);
  if (unit <= 0) {
    clearGuideTimers();
    guideTimer = setTimeout(() => apply(measureGuideUnit()), 120);
  }
}

function runGuideScroll() {
  const cfg = guideSettings();
  const track = document.getElementById("guide-track");
  if (!track) {
    retractGuide();
    return;
  }
  const unit = measureGuideUnit();
  const speed = cfg.speed;
  if (cfg.reappear === 0) {
    startGuideLoop(track, unit, speed);
    return;
  }
  const view = track.parentElement ? track.parentElement.offsetWidth : 0;
  const needScroll = unit > view + 8;
  const distance = needScroll ? unit * cfg.plays : 0;
  const duration = distance > 0 ? (distance / speed) * 1000 : 0;
  if (distance > 0) {
    resetGuideTrack();
    void track.offsetWidth;
    track.style.transition = `transform ${duration}ms linear`;
    track.style.transform = `translateX(${-distance}px)`;
  }
  clearGuideTimers();
  guideTimer = setTimeout(() => {
    guideTimer = setTimeout(retractGuide, cfg.hideDelay * 1000);
  }, duration);
}

function retractGuide() {
  const cfg = guideSettings();
  if (cfg.enabled && cfg.reappear === 0) return;
  setGuideOpen(false);
  clearGuideTimers();
  guideTimer = setTimeout(() => {
    resetGuideTrack();
    guideRunning = false;
    if (!guideSettings().enabled) return;
    const wait = guideSettings().reappear * 1000;
    if (wait <= 0) {
      startGuideCycle();
      return;
    }
    guideTimer = setTimeout(() => {
      if (!guideSettings().enabled) return;
      startGuideCycle();
    }, wait);
  }, GUIDE_EXTEND_MS + 40);
}

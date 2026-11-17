/* 澜音竖卡控制器：覆盖层和控制台预览共用 */
(function (global) {
  const COVER = 0;
  const GUIDE = 1;
  const SOURCE_LABEL = { qq: "QQ", qishui: "汽水", kugou: "酷狗", bili: "哔哩哔哩", netease: "网易云" };

  function pad(n) { return String(n).padStart(2, "0"); }
  function weekDay(d) { return "星期" + "日一二三四五六"[d.getDay()]; }
  function fmt(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    return pad(Math.floor(sec / 60)) + ":" + pad(sec % 60);
  }
  function toTime(m, s, ms) {
    return Number(m) * 60 + Number(s) + Number(ms ? String(ms).padEnd(3, "0") : 0) / 1000;
  }

  const PortraitCard = {
    root: null,
    slideIndex: GUIDE,
    slideMs: 6000,
    timer: null,
    playing: false,
    showLyrics: true,
    lyricLines: [],
    lastLyricRaw: "",
    lastTitle: "",
    clockStarted: false,
  };

  function $(sel) {
    return PortraitCard.root ? PortraitCard.root.querySelector(sel) : null;
  }

  function parseLrc(text) {
    const lines = [];
    String(text || "").split(/\r?\n/).forEach((raw) => {
      const tags = [...raw.matchAll(/\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]/g)];
      if (!tags.length) return;
      const lyric = raw.replace(/\[[^\]]+\]/g, "").replace(/<[^>]+>/g, "").trim();
      if (!lyric) return;
      tags.forEach((m) => lines.push({ t: toTime(m[1], m[2], m[3]), text: lyric }));
    });
    lines.sort((a, b) => a.t - b.t);
    return lines;
  }

  PortraitCard.syncScale = function () {
    const root = PortraitCard.root;
    if (!root) return;
    const w = root.clientWidth || 278;
    root.style.setProperty("--cqw", (w / 100) + "px");
  };

  PortraitCard.init = function (root) {
    PortraitCard.root = root || document.getElementById("portrait-card");
    if (!PortraitCard.root) return;
    PortraitCard.syncScale();
    if (!PortraitCard._resizeObs && typeof ResizeObserver !== "undefined") {
      PortraitCard._resizeObs = new ResizeObserver(() => PortraitCard.syncScale());
      PortraitCard._resizeObs.observe(PortraitCard.root);
    } else if (PortraitCard._resizeObs) {
      try { PortraitCard._resizeObs.observe(PortraitCard.root); } catch (e) {}
    }
    PortraitCard.goSlide(GUIDE);
    PortraitCard.tickClock();
    if (!PortraitCard.clockStarted) {
      PortraitCard.clockStarted = true;
      setInterval(() => PortraitCard.tickClock(), 1000);
    }
    PortraitCard.restartTimer();
  };

  PortraitCard.applySettings = function (ov) {
    const root = PortraitCard.root;
    if (!root) return;
    ov = ov || {};
    PortraitCard.syncScale();
    root.classList.toggle("hide-date", ov.show_date === false);
    root.classList.toggle("hide-time", ov.show_time === false);
    PortraitCard.showLyrics = ov.show_lyrics !== false;
    const ms = Math.round((Number(ov.carousel_seconds) || 6) * 1000);
    const next = Math.max(2000, Math.min(30000, ms));
    if (next !== PortraitCard.slideMs) {
      PortraitCard.slideMs = next;
      PortraitCard.restartTimer();
    }
  };

  PortraitCard.tickClock = function () {
    if (!PortraitCard.root) return;
    const now = new Date();
    const dateEl = $(".clock-date");
    const timeEl = $(".clock-time");
    if (dateEl) dateEl.textContent = (now.getMonth() + 1) + "月" + now.getDate() + "日  " + weekDay(now);
    if (timeEl) timeEl.textContent = pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
  };

  PortraitCard.goSlide = function (i) {
    if (!PortraitCard.root) return;
    const slides = PortraitCard.root.querySelectorAll(".slide");
    if (!slides.length) return;
    PortraitCard.slideIndex = (i + slides.length) % slides.length;
    slides.forEach((el, idx) => el.classList.toggle("is-active", idx === PortraitCard.slideIndex));
    PortraitCard.root.querySelectorAll(".dots span").forEach((el, idx) => {
      el.classList.toggle("is-on", idx === PortraitCard.slideIndex);
    });
  };

  PortraitCard.restartTimer = function () {
    if (PortraitCard.timer) {
      clearInterval(PortraitCard.timer);
      PortraitCard.timer = null;
    }
    PortraitCard.timer = setInterval(() => {
      if (!PortraitCard.playing) {
        PortraitCard.goSlide(GUIDE);
        return;
      }
      PortraitCard.goSlide(PortraitCard.slideIndex + 1);
    }, PortraitCard.slideMs);
  };

  PortraitCard.setPlaying = function (on) {
    const next = !!on;
    const root = PortraitCard.root;
    if (root) root.classList.toggle("is-idle", !next);
    if (!next) PortraitCard.goSlide(GUIDE);
    else if (!PortraitCard.playing) PortraitCard.goSlide(COVER);
    PortraitCard.playing = next;
  };

  PortraitCard.setTitle = function (text) {
    const el = $(".title");
    if (!el) return;
    const next = text || "等待点歌";
    if (el.dataset.text === next && (el.classList.contains("is-wrap") || el.classList.contains("is-marquee"))) return;
    el.dataset.text = next;
    el.className = "title is-wrap";
    el.innerHTML = "";
    const inner = document.createElement("span");
    inner.className = "title-text";
    inner.textContent = next;
    el.appendChild(inner);
    requestAnimationFrame(() => {
      if (el.dataset.text !== next) return;
      const cs = getComputedStyle(el);
      const lineH = parseFloat(cs.lineHeight) || (parseFloat(cs.fontSize) * 1.35);
      if (el.scrollHeight <= lineH * 2 + 1) return;
      el.className = "title is-marquee";
      inner.style.paddingRight = "40px";
      const clone = inner.cloneNode(true);
      el.appendChild(clone);
      const dur = Math.max(8, inner.offsetWidth / 42);
      const anim = "portrait-title-marquee " + dur + "s linear infinite";
      inner.style.animation = anim;
      clone.style.animation = anim;
    });
  };

  PortraitCard.setArt = function (url) {
    const el = $(".cover-img");
    if (!el) return;
    el.style.backgroundImage = url ? 'url("' + url + '")' : "none";
  };

  PortraitCard.setLyricRaw = function (raw) {
    const next = raw || "";
    if (next === PortraitCard.lastLyricRaw) return;
    PortraitCard.lastLyricRaw = next;
    PortraitCard.lyricLines = parseLrc(next);
  };

  PortraitCard.renderLyricAt = function (pos, cur) {
    const lyricEl = $(".lyric");
    const wave = $(".wave") || $("canvas.wave");
    if (!lyricEl) return;
    if (!PortraitCard.showLyrics) {
      lyricEl.hidden = true;
      if (wave) wave.hidden = !PortraitCard.playing;
      return;
    }
    const lines = PortraitCard.lyricLines;
    let idx = -1;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].t <= pos + 0.12) idx = i;
      else break;
    }
    const line = idx >= 0 ? lines[idx] : null;
    const fallback = (cur && (cur.lyric_line || "").trim()) || "";
    if (!line && !fallback && !lines.length) {
      const waiting = !(cur && (cur.title || cur.cover_url || cur.cover_data));
      if (waiting) {
        lyricEl.className = "lyric is-empty";
        lyricEl.textContent = "还没有正在播放的歌曲";
        lyricEl.hidden = false;
        if (wave) wave.hidden = true;
      } else {
        lyricEl.className = "lyric is-empty";
        lyricEl.textContent = "这首歌暂时没有歌词";
        lyricEl.hidden = true;
        if (wave) wave.hidden = false;
      }
      return;
    }
    const text = (line && line.text) || fallback;
    if (!text) {
      lyricEl.className = "lyric is-empty";
      lyricEl.textContent = "";
      lyricEl.hidden = true;
      if (wave) wave.hidden = false;
      return;
    }
    lyricEl.className = "lyric";
    lyricEl.textContent = text;
    lyricEl.hidden = false;
    if (wave) wave.hidden = true;
  };

  PortraitCard.renderProgress = function (pos, dur) {
    const bar = $("[data-bar]") || document.getElementById("portrait-bar");
    const t0 = document.getElementById("portrait-t0") || $("[data-t0]");
    const t1 = document.getElementById("portrait-t1") || $("[data-t1]");
    if (bar) bar.style.width = dur ? Math.min(100, (pos / dur) * 100) + "%" : "0%";
    if (t0) t0.textContent = fmt(pos);
    if (t1) t1.textContent = fmt(dur);
  };

  PortraitCard.renderTrack = function (cur, extras) {
    const root = PortraitCard.root;
    if (!root) return;
    extras = extras || {};
    const paused = !!extras.paused;
    root.classList.toggle("is-paused", paused);
    const playing = !!(cur && (cur.title || cur.cover_url || cur.cover_data));
    PortraitCard.setPlaying(playing);
    const kicker = $(".kicker");
    const sub = $(".sub");
    if (!playing) {
      if (kicker) kicker.textContent = "等待点歌";
      PortraitCard.setTitle("等待点歌");
      if (sub) sub.textContent = "澜音";
      PortraitCard.setLyricRaw("");
      PortraitCard.setArt("");
      PortraitCard.renderLyricAt(0, null);
      PortraitCard.renderProgress(0, 0);
      return;
    }
    const idle = /默认歌单|idle/i.test((cur.kind || cur.origin || "") + " " + (cur.requester_name || "")) || cur.idle === true;
    if (kicker) kicker.textContent = idle ? "默认歌单" : ((cur.requester_name || "观众") + " 点歌");
    PortraitCard.setTitle(cur.title || "正在播放");
    const bits = [];
    if (!idle && cur.requester_name) bits.push(cur.requester_name);
    const plat = SOURCE_LABEL[cur.source] || cur.source;
    if (plat) bits.push(plat);
    if (cur.artist) bits.push(cur.artist);
    if (sub) sub.textContent = bits.join(" · ") || "澜音";
    if (typeof extras.coverSrc === "function") PortraitCard.setArt(extras.coverSrc(cur.cover_url || cur.cover_data || ""));
    else PortraitCard.setArt(cur.cover_url || cur.cover_data || "");
    if (cur.lyric && /\[\d{1,2}:\d{1,2}/.test(cur.lyric)) PortraitCard.setLyricRaw(cur.lyric);
    else PortraitCard.setLyricRaw("");
    PortraitCard.renderLyricAt(Number(extras.position) || 0, cur);
    if (extras.duration !== undefined) PortraitCard.renderProgress(Number(extras.position) || 0, Number(extras.duration) || 0);
  };

  PortraitCard.waveCanvas = function () {
    return PortraitCard.root && (PortraitCard.root.querySelector("canvas.wave") || document.getElementById("portrait-wave"));
  };

  global.PortraitCard = PortraitCard;
})(window);

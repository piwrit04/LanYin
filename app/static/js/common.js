function coverSrc(url) {
  if (!url) return "";
  return "/api/cover?u=" + encodeURIComponent(url);
}

function setCover(el, url) {
  if (!el) return;
  el.style.backgroundImage = url ? ('url("' + coverSrc(url) + '")') : "";
}


function setVinylSpin(coverEl, playing, reset) {
  const stage = coverEl && coverEl.closest ? coverEl.closest(".cover-stage.vinyl") : null;
  if (!stage) return;
  const disc = stage.querySelector(".vinyl-disc");
  if (!disc) return;
  const on = !!playing && !reset;
  stage.classList.toggle("is-spinning", on);
  disc.style.animation = "none";

  if (typeof disc.animate === "function") {
    let anim = disc._vinylAnim;
    if (reset) {
      if (anim) {
        try { anim.cancel(); } catch (e) {}
      }
      disc._vinylAnim = null;
      disc.style.transform = "";
      return;
    }
    if (!anim || anim.playState === "idle") {
      anim = disc.animate(
        [{ transform: "rotate(0deg)" }, { transform: "rotate(360deg)" }],
        { duration: 12000, iterations: Infinity, easing: "linear" }
      );
      anim.pause();
      disc._vinylAnim = anim;
    }
    if (on) anim.play();
    else anim.pause();
    return;
  }

  if (reset) {
    disc.style.transform = "";
    disc.style.animationPlayState = "";
    return;
  }
  if (on) {
    disc.style.transform = "";
    disc.style.animation = "vinyl-spin 12s linear infinite";
    disc.style.animationPlayState = "running";
  } else {
    const tr = getComputedStyle(disc).transform;
    disc.style.transform = (tr && tr !== "none") ? tr : "";
    disc.style.animationPlayState = "paused";
  }
}


function parseLrc(text) {
  const lines = [];
  String(text || "").split(/\r?\n/).forEach((line) => {
    const tags = [...line.matchAll(/\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]/g)];
    const lyric = line.replace(/\[[^\]]+\]/g, "").trim();
    tags.forEach((m) => {
      const t = Number(m[1]) * 60 + Number(m[2]) + Number(m[3] ? m[3].padEnd(3, "0") : 0) / 1000;
      lines.push({ t, text: lyric });
    });
  });
  lines.sort((a, b) => a.t - b.t);
  return lines;
}

function currentLyric(lines, pos) {
  let text = "";
  const list = lines || [];
  for (const line of list) {
    if (line.t <= pos + 0.15) text = line.text;
    else break;
  }
  return text;
}

const _marqueeState = new WeakMap();

function setScrollingTitle(track, text) {
  if (typeof text === "undefined") {
    text = track;
    track = document.getElementById("title-track");
  }
  const wrap = track && track.parentElement;
  const next = text || "";
  if (!track || !wrap) {
    const el = document.getElementById("title") || document.getElementById("now-title");
    if (el) el.textContent = next;
    return;
  }
  const prev = _marqueeState.get(track);
  if (prev && prev.text === next && track.querySelector(".title")) {
    _measureMarquee(track, wrap);
    return;
  }
  _marqueeState.set(track, { text: next });
  track.classList.remove("is-marquee");
  track.style.animation = "";
  track.style.animationDuration = "";
  track.innerHTML = "";
  const el = document.createElement("span");
  el.className = "title";
  const preferredId = track.dataset.titleId || (track.id === "title-track" ? "title" : "now-title");
  if (!document.getElementById(preferredId) || (track.querySelector("#" + preferredId) == null && !document.getElementById(preferredId))) {
    el.id = preferredId;
  } else if (track.id === "title-track") {
    el.id = "title";
  }
  el.textContent = next;
  track.appendChild(el);
  _measureMarquee(track, wrap, el);
  if (!track._ro) {
    track._ro = new ResizeObserver(() => _measureMarquee(track, wrap));
    track._ro.observe(wrap);
  }
}

function _applyMarqueeAnimation(track, dur) {
  track.classList.add("is-marquee");
  track.style.animation = "title-marquee " + dur + "s linear infinite";
}

function _marqueeLimit(wrap) {
  const col = (wrap.closest && wrap.closest(".now-info, .now-copy, .player-main, .now-body")) || wrap.parentElement || wrap;
  const cw = col ? col.clientWidth : 0;
  const ww = wrap.clientWidth || 0;
  if (cw > 0 && (ww === 0 || ww > cw + 2)) return cw;
  return ww || cw;
}

function _measureMarquee(track, wrap, el, attempt) {
  el = el || track.querySelector(".title:not([aria-hidden])") || track.querySelector(".title");
  attempt = attempt || 0;
  const run = () => {
    if (!el || !wrap || !track.isConnected) return;
    const w = _marqueeLimit(wrap);
    if (w === 0 && attempt < 20) {
      setTimeout(() => _measureMarquee(track, wrap, el, attempt + 1), 50);
      return;
    }
    if (el.scrollWidth > w + 2) {
      if (!track.querySelector(".title[aria-hidden]")) {
        const clone = el.cloneNode(true);
        clone.id = "";
        clone.setAttribute("aria-hidden", "true");
        track.appendChild(clone);
      }
      const dur = Math.max(8, el.scrollWidth / 36);
      _applyMarqueeAnimation(track, dur);
    } else {
      track.classList.remove("is-marquee");
      track.style.animation = "";
      track.style.animationDuration = "";
      const clone = track.querySelector(".title[aria-hidden]");
      if (clone) clone.remove();
    }
  };
  requestAnimationFrame(run);
  if (attempt === 0 && document.fonts && document.fonts.status !== "loaded" && document.fonts.ready) {
    document.fonts.ready.then(() => {
      if (track.isConnected) _measureMarquee(track, wrap, el, 1);
    });
  }
}

function attachVisualizer(audio, sendBins) {
  if (!audio || !sendBins) return;
  let ctx = null;
  let analyser = null;
  let hooked = false;
  const BINS = 32;

  function collectBins() {
    if (!analyser) return;
    const arr = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(arr);
    const out = [];
    const start = 1;
    const usable = Math.max(1, Math.floor(arr.length * 0.55) - start);
    for (let i = 0; i < BINS; i++) {
      const a = start + Math.floor((i * usable) / BINS);
      const b = start + Math.floor(((i + 1) * usable) / BINS);
      let peak = 0;
      let s = 0;
      let c = 0;
      for (let j = a; j < Math.max(a + 1, b); j++) {
        const v = arr[j] || 0;
        peak = Math.max(peak, v);
        s += v;
        c++;
      }
      const mixed = peak * 0.7 + (c ? s / c : 0) * 0.3;
      out.push(Math.round(Math.min(100, (mixed / 2.55) * 1.15)));
    }
    sendBins(out, !!audio.paused);
  }

  function hookClock(sourceNode) {
    let usedWorklet = false;
    const silent = ctx.createGain();
    silent.gain.value = 0;
    const attachScript = () => {
      const sp = ctx.createScriptProcessor(2048, 1, 1);
      sp.onaudioprocess = () => collectBins();
      sourceNode.connect(sp);
      sp.connect(silent);
    };
    if (ctx.audioWorklet && ctx.audioWorklet.addModule) {
      const src = "class VizClock extends AudioWorkletProcessor{constructor(){super();this.n=0;}process(){this.n++;if(this.n%3===0)this.port.postMessage(0);return true;}}registerProcessor('viz-clock',VizClock);";
      const url = URL.createObjectURL(new Blob([src], { type: "application/javascript" }));
      ctx.audioWorklet.addModule(url).then(() => {
        URL.revokeObjectURL(url);
        if (usedWorklet) return;
        try {
          const node = new AudioWorkletNode(ctx, "viz-clock");
          node.port.onmessage = () => collectBins();
          sourceNode.connect(node);
          node.connect(silent);
          usedWorklet = true;
        } catch (e) {
          attachScript();
        }
      }).catch(() => {
        URL.revokeObjectURL(url);
        if (!usedWorklet) attachScript();
      });
    } else {
      attachScript();
    }
    silent.connect(ctx.destination);
  }

  function setup() {
    if (hooked) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    try {
      if (!ctx) ctx = new AC();
    } catch (e) {
      return;
    }
    let src;
    try {
      src = ctx.createMediaElementSource(audio);
      hooked = true;
    } catch (e) {
      hooked = true;
      return;
    }
    try {
      analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.38;
      src.connect(analyser);
      analyser.connect(ctx.destination);
      hookClock(analyser);
      ctx.resume().catch(() => {});
    } catch (e) {
      // source already hooked; audio still plays through if the graph is partial
    }
  }

  function resumeCtx() {
    if (!hooked) setup();
    if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {});
  }

  audio.addEventListener("play", resumeCtx);
  audio.addEventListener("playing", resumeCtx);
  document.addEventListener("visibilitychange", resumeCtx);
  window.addEventListener("focus", resumeCtx);
  setInterval(() => {
    resumeCtx();
  }, 1000);
  if (!audio.paused && audio.src) resumeCtx();
}

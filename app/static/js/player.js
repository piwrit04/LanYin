const audio = document.getElementById("audio");
const title = document.getElementById("title");
const meta = document.getElementById("meta");
const cover = document.getElementById("cover");
const bar = document.getElementById("bar");
const t0 = document.getElementById("t0");
const t1 = document.getElementById("t1");
const hint = document.getElementById("hint");

let ws;
let currentId = "";
let lyricLines = [];
let pendingSeek = 0;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "hello", role: "player" }));
    sendPlayer({ event: "ready" });
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "player_cmd") handleCmd(msg);
    else if (msg.type === "state" && msg.payload && msg.payload.volume != null) {
      applyVolume(msg.payload.volume, false);
    }
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

function sendPlayer(payload) {
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: "player", ...payload }));
  }
}

function handleCmd(msg) {
  if (msg.action === "volume") {
    applyVolume(msg.volume, false);
    return;
  }
  if (msg.volume != null) applyVolume(msg.volume, false);
  if (msg.action === "play") {
    const item = msg.item || {};
    currentId = item.id;
    title.textContent = item.title || "未知歌曲";
    meta.textContent = `${item.artist || ""}  ·  ${item.requester_name || ""}`;
    setCover(cover, item.cover_url);
    cover.classList.toggle("is-playing", !msg.paused);
    setVinylSpin(cover, !msg.paused);
    lyricLines = parseLrc(msg.lyric || "");
    audio.src = msg.stream_url;
    pendingSeek = Number(msg.position || 0);
    const play = () => {
      if (pendingSeek) {
        try { audio.currentTime = pendingSeek; } catch (e) {}
        pendingSeek = 0;
      }
      const p = audio.play();
      if (p && p.catch) {
        p.catch(() => {
          hint.textContent = "浏览器拦截了自动播放，请点击「开始播放」。OBS 浏览器源一般可以直接出声。";
        });
      }
      if (msg.paused) audio.pause();
    };
    if (audio.readyState >= 1) play();
    else audio.onloadedmetadata = play;
  } else if (msg.action === "pause") {
    audio.pause();
    cover.classList.remove("is-playing");
    setVinylSpin(cover, false);
  } else if (msg.action === "resume") {
    audio.play().catch(() => {});
    cover.classList.add("is-playing");
    setVinylSpin(cover, true);
  } else if (msg.action === "stop") {
    audio.pause();
    audio.removeAttribute("src");
    currentId = "";
    title.textContent = "播放器已就绪";
    meta.textContent = "等待下一首";
    bar.style.width = "0%";
    cover.classList.remove("is-playing");
    setVinylSpin(cover, false, true);
    setCover(cover, "");
  }
}

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}

audio.ontimeupdate = () => {
  const pos = audio.currentTime || 0;
  const dur = audio.duration || 0;
  bar.style.width = dur ? `${Math.min(100, (pos / dur) * 100)}%` : "0%";
  t0.textContent = fmt(pos);
  t1.textContent = fmt(dur);
};

audio.onended = () => sendPlayer({ event: "ended" });
audio.onerror = () => {
  hint.textContent = "音频加载失败，正在尝试跳过。";
  sendPlayer({ event: "error", message: "音频加载失败" });
};

setInterval(() => {
  if (!currentId || !audio.src) return;
  sendPlayer({
    event: "progress",
    position: audio.currentTime || 0,
    duration: audio.duration || 0,
    paused: audio.paused,
    lyric_line: currentLyric(lyricLines, audio.currentTime || 0),
  });
}, 400);

function syncVinyl() {
  const playing = Boolean(currentId) && !audio.paused;
  cover.classList.toggle("is-playing", playing);
  setVinylSpin(cover, playing);
}
audio.onplay = syncVinyl;
audio.onpause = syncVinyl;
document.getElementById("btn-start").onclick = () => {
  audio.play().catch(() => {});
  hint.textContent = "已尝试开始播放";
};
document.getElementById("btn-pause").onclick = () => {
  if (audio.paused) audio.play().catch(() => {});
  else audio.pause();
};

const VOLUME_KEY = "obs-song-volume";
let lastSentVolume = null;

function applyVolume(v, sync) {
  const vol = Math.min(1, Math.max(0, Number(v)));
  if (!Number.isFinite(vol)) return;
  audio.volume = vol;
  localStorage.setItem(VOLUME_KEY, String(vol));
  const slider = document.getElementById("volume");
  const label = document.getElementById("volume-val");
  if (slider && document.activeElement !== slider) slider.value = String(Math.round(vol * 100));
  if (label) label.textContent = Math.round(vol * 100) + "%";
  if (sync !== false && ws && ws.readyState === 1 && (lastSentVolume === null || Math.abs(vol - lastSentVolume) >= 0.005)) {
    lastSentVolume = vol;
    ws.send(JSON.stringify({ type: "volume", volume: vol }));
  } else if (sync === false) {
    lastSentVolume = vol;
  }
}

(function initVolume() {
  const slider = document.getElementById("volume");
  const stored = Number(localStorage.getItem(VOLUME_KEY));
  applyVolume(Number.isFinite(stored) ? stored : 0.8, false);
  if (slider) {
    slider.addEventListener("input", () => applyVolume(Number(slider.value) / 100));
  }
})();

connect();

attachVisualizer(audio, (bins, paused) => {
  if (!currentId || !audio.src) return;
  sendPlayer({ event: "visualizer", bins, paused });
});


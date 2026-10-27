"use strict";

const $ = (id) => document.getElementById(id);
const FRAMES = 12;        // сколько кадров отдаём ИИ
const FRAME_WIDTH = 512;  // ширина кадра, px

const state = { url: "", info: null, mode: "video", quality: null };
let lastFile = null;      // последний скачанный файл — чтобы сразу оценить его

// ---------- Вкладки ----------
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.tab === name;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", on);
  });
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === name));
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

// ---------- Статус ----------
function setStatus(el, text, { error = false, busy = false, progress = null } = {}) {
  el.hidden = false;
  el.className = "status" + (error ? " error" : "");
  el.replaceChildren();
  if (busy) el.append(Object.assign(document.createElement("div"), { className: "spin" }));
  const box = document.createElement("div");
  box.style.flex = "1";
  box.append(text);
  if (progress !== null) {
    const bar = document.createElement("div");
    bar.className = "progress";
    const fill = document.createElement("div");
    fill.style.width = `${Math.round(progress * 100)}%`;
    bar.append(fill);
    box.append(bar);
  }
  el.append(box);
}
const hide = (el) => { el.hidden = true; };

async function errorText(res) {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return data.detail;
  } catch {}
  if (res.status === 504) return "Сервер не успел. Попробуй качество пониже или ролик покороче.";
  return `Ошибка сервера (${res.status})`;
}

async function postJSON(path, body, headers = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res;
}

// ---------- Скачивание файла с прогрессом ----------
async function fetchFile(body, statusEl) {
  setStatus(statusEl, "Готовлю файл… обычно до минуты", { busy: true });
  const res = await postJSON("/api/download", body);

  const total = Number(res.headers.get("Content-Length")) || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    setStatus(statusEl, `Загружаю… ${(got / 1048576).toFixed(1)} МБ`, { busy: true, progress: total ? got / total : null });
  }
  const type = res.headers.get("Content-Type") || "video/mp4";
  let name = type.includes("audio") ? "track.mp3" : "video.mp4";
  const m = (res.headers.get("Content-Disposition") || "").match(/filename\*=UTF-8''([^;]+)/);
  if (m) name = decodeURIComponent(m[1]);
  return new File(chunks, name, { type });
}

// ---------- Шаг 1: ссылка → что за видео ----------
$("dl-paste").addEventListener("click", async () => {
  try {
    $("dl-url").value = (await navigator.clipboard.readText()).trim();
    $("dl-form").requestSubmit();
  } catch {}
});

$("dl-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("dl-url").value.trim();
  if (!url) return;
  $("dl-go").disabled = true;
  hide($("dl-card"));
  hide($("dl-result"));
  setStatus($("dl-status"), "Ищу…", { busy: true });
  try {
    const info = await (await postJSON("/api/info", { url })).json();
    state.url = url;
    state.info = info;
    showInfo(info);
    hide($("dl-status"));
  } catch (err) {
    setStatus($("dl-status"), err.message, { error: true });
  } finally {
    $("dl-go").disabled = false;
  }
});

function showInfo(info) {
  const spotify = info.kind === "spotify";
  $("info-thumb").src = info.thumbnail || "/icon-192.png";
  $("info-thumb").style.aspectRatio = "1";
  $("info-platform").textContent = info.platform || "видео";
  $("info-title").textContent = info.title;
  const parts = [info.author, info.duration ? fmtTime(info.duration) : ""].filter(Boolean);
  $("info-author").textContent = parts.join(" · ");

  $("mode-box").hidden = spotify;
  $("spotify-note").hidden = !spotify;
  state.mode = spotify ? "audio" : "video";

  // Качество: по умолчанию 1080p (быстро и красиво), иначе лучшее доступное
  const qs = info.qualities || [];
  state.quality = qs.find((q) => q.value === 1080)?.value ?? qs[0]?.value ?? null;
  $("qualities").replaceChildren(...qs.map((q) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = q.label;
    b.dataset.value = q.value;
    b.addEventListener("click", () => { state.quality = q.value; renderOptions(); });
    return b;
  }));

  $("long-note").hidden = !info.too_long;
  $("long-note").textContent = "Видео слишком длинное: бесплатный сервер успевает скачать ролики до 10 минут.";
  $("dl-btn").disabled = info.too_long;

  renderOptions();
  $("dl-card").hidden = false;
}

function renderOptions() {
  document.querySelectorAll("#modes button").forEach((b) => b.classList.toggle("on", b.dataset.mode === state.mode));
  document.querySelectorAll("#qualities .chip").forEach((c) => c.classList.toggle("on", Number(c.dataset.value) === state.quality));
  const hasQualities = (state.info?.qualities || []).length > 0;
  $("quality-box").hidden = state.mode === "audio" || !hasQualities;

  const q = (state.info?.qualities || []).find((x) => x.value === state.quality);
  $("dl-btn").textContent = state.mode === "audio"
    ? "скачать mp3"
    : `скачать ${q ? q.label + " · " : ""}mp4${state.mode === "mute" ? " без звука" : ""}`;
}

document.querySelectorAll("#modes button").forEach((b) =>
  b.addEventListener("click", () => { state.mode = b.dataset.mode; renderOptions(); }));

function fmtTime(sec) {
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : m) + ":" + String(s).padStart(2, "0");
}

// ---------- Шаг 2: скачать с выбранными настройками ----------
$("dl-btn").addEventListener("click", async () => {
  const btn = $("dl-btn");
  btn.disabled = true;
  hide($("dl-result"));
  try {
    const file = await fetchFile({ url: state.url, mode: state.mode, quality: state.quality }, $("dl-status"));
    lastFile = file;
    showResult(file);
    hide($("dl-status"));
  } catch (err) {
    setStatus($("dl-status"), err.message, { error: true });
  } finally {
    btn.disabled = false;
  }
});

function showResult(file) {
  const href = URL.createObjectURL(file);
  const isAudio = file.type.includes("audio");
  $("dl-video").hidden = isAudio;
  $("dl-audio").hidden = !isAudio;
  (isAudio ? $("dl-audio") : $("dl-video")).src = href;
  $("dl-save").href = href;
  $("dl-save").download = file.name;
  $("dl-save").textContent = `сохранить ${isAudio ? "mp3" : "видео"}`;
  $("dl-share").hidden = !(navigator.canShare && navigator.canShare({ files: [file] }));
  $("dl-to-rate").hidden = isAudio;
  $("dl-result").hidden = false;
  $("dl-result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("dl-share").addEventListener("click", async () => {
  try { await navigator.share({ files: [lastFile] }); } catch {}
});

$("dl-to-rate").addEventListener("click", () => {
  showTab("rate");
  rateFile(lastFile);
});

// ---------- Кадры из видео (прямо в браузере) ----------
function once(el, event) {
  return new Promise((resolve, reject) => {
    const ok = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error("Браузер не смог открыть это видео")); };
    const cleanup = () => { el.removeEventListener(event, ok); el.removeEventListener("error", fail); };
    el.addEventListener(event, ok);
    el.addEventListener("error", fail);
  });
}

async function extractFrames(file, onProgress) {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  video.src = URL.createObjectURL(file);
  try {
    await once(video, "loadedmetadata");
    // Safari не рисует кадры, пока видео ни разу не запускалось
    try { await video.play(); video.pause(); } catch {}

    const duration = video.duration;
    if (!isFinite(duration) || duration <= 0) throw new Error("Не удалось определить длину видео");
    const scale = Math.min(1, FRAME_WIDTH / video.videoWidth);
    const canvas = $("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const ctx = canvas.getContext("2d");

    const frames = [];
    for (let i = 0; i < FRAMES; i++) {
      const t = (duration * (i + 0.5)) / FRAMES;
      video.currentTime = t;
      await once(video, "seeked");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const jpeg = canvas.toDataURL("image/jpeg", 0.72).split(",")[1];
      frames.push({ t: Math.round(t * 10) / 10, jpeg });
      onProgress((i + 1) / FRAMES);
    }
    return { duration, width: video.videoWidth, height: video.videoHeight, frames };
  } finally {
    URL.revokeObjectURL(video.src);
  }
}

// ---------- Оценка ----------
let busy = false;

async function rateFile(file) {
  if (busy || !file) return;
  busy = true;
  const status = $("rate-status");
  hide($("rate-result"));
  try {
    setStatus(status, "Смотрю видео…", { busy: true, progress: 0 });
    const data = await extractFrames(file, (p) =>
      setStatus(status, "Смотрю видео…", { busy: true, progress: p }));

    setStatus(status, "ИИ оценивает эдит… обычно 10–30 секунд", { busy: true });
    const code = $("access-code").value.trim();
    const res = await postJSON("/api/rate", data, code ? { "X-Access-Code": code } : {});
    showRating(await res.json());
    try { if (code) localStorage.setItem("accessCode", code); } catch {}
    hide(status);
  } catch (err) {
    setStatus(status, err.message, { error: true });
  } finally {
    busy = false;
  }
}

function fillList(id, items) {
  $(id).replaceChildren(...items.map((s) => Object.assign(document.createElement("li"), { textContent: s })));
}

function showRating(r) {
  $("score").textContent = r.score;
  $("verdict").textContent = r.verdict;
  fillList("strengths", r.strengths);
  fillList("weaknesses", r.weaknesses);
  fillList("tips", r.tips);
  $("score-fill").style.width = "0";
  $("rate-result").hidden = false;
  requestAnimationFrame(() => requestAnimationFrame(() => { $("score-fill").style.width = `${r.score * 10}%`; }));
  $("rate-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("rate-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  rateFile(file);
});

const drop = $("drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  rateFile([...e.dataTransfer.files].find((f) => f.type.startsWith("video/")));
});

$("rate-url-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("rate-url").value.trim();
  if (!url || busy) return;
  try {
    // Для оценки хватает 720p — так быстрее
    rateFile(await fetchFile({ url, mode: "video", quality: 720 }, $("rate-status")));
  } catch (err) {
    setStatus($("rate-status"), err.message, { error: true });
  }
});

// ---------- Настройки сайта ----------
fetch("/api/health").then((r) => r.json()).then((h) => {
  $("rate-off").hidden = h.rating;
  $("code-box").hidden = !h.code_required;
  try { $("access-code").value = localStorage.getItem("accessCode") || ""; } catch {}
}).catch(() => {});

// ---------- Установка как приложение (PWA) ----------
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $("install").hidden = false;
});
$("install").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $("install").hidden = true;
});

// Ссылка, которой поделились в приложение (Android: «Поделиться» → ts-speed)
{
  const params = new URLSearchParams(location.search);
  const m = (params.get("text") || params.get("url") || "").match(/https?:\/\/\S+/);
  if (m) {
    $("dl-url").value = m[0];
    $("dl-form").requestSubmit();
  }
}

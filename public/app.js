"use strict";

const $ = (id) => document.getElementById(id);
const FRAMES = 12;        // сколько кадров отдаём ИИ
const FRAME_WIDTH = 512;  // ширина кадра, px

let lastVideo = null;     // последнее скачанное видео (Blob) — чтобы сразу оценить его

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
  if (res.status === 504) return "Сервер не успел скачать видео. Попробуй ролик покороче.";
  return `Ошибка сервера (${res.status})`;
}

// ---------- Скачивание ----------
async function fetchVideo(url, statusEl) {
  setStatus(statusEl, "Скачиваю видео… это может занять до минуты", { busy: true });
  const res = await fetch("/api/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) throw new Error(await errorText(res));

  const total = Number(res.headers.get("Content-Length")) || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    const mb = (got / 1048576).toFixed(1);
    setStatus(statusEl, `Получаю файл… ${mb} МБ`, { busy: true, progress: total ? got / total : null });
  }
  let name = "video.mp4";
  const cd = res.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename\*=UTF-8''([^;]+)/);
  if (m) name = decodeURIComponent(m[1]);
  return new File(chunks, name, { type: "video/mp4" });
}

$("dl-paste").addEventListener("click", async () => {
  try { $("dl-url").value = (await navigator.clipboard.readText()).trim(); } catch {}
});

$("dl-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("dl-btn");
  btn.disabled = true;
  hide($("dl-result"));
  try {
    const file = await fetchVideo($("dl-url").value.trim(), $("dl-status"));
    lastVideo = file;
    const href = URL.createObjectURL(file);
    $("dl-video").src = href;
    $("dl-save").href = href;
    $("dl-save").download = file.name;
    $("dl-share").hidden = !(navigator.canShare && navigator.canShare({ files: [file] }));
    $("dl-result").hidden = false;
    hide($("dl-status"));
  } catch (err) {
    setStatus($("dl-status"), "❌ " + err.message, { error: true });
  } finally {
    btn.disabled = false;
  }
});

$("dl-share").addEventListener("click", async () => {
  try { await navigator.share({ files: [lastVideo] }); } catch {}
});

$("dl-to-rate").addEventListener("click", () => {
  showTab("rate");
  rateFile(lastVideo);
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
    const headers = { "Content-Type": "application/json" };
    const code = $("access-code").value.trim();
    if (code) headers["X-Access-Code"] = code;
    const res = await fetch("/api/rate", { method: "POST", headers, body: JSON.stringify(data) });
    if (!res.ok) throw new Error(await errorText(res));
    showRating(await res.json());
    try { if (code) localStorage.setItem("accessCode", code); } catch {}
    hide(status);
  } catch (err) {
    setStatus(status, "❌ " + err.message, { error: true });
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
  const file = [...e.dataTransfer.files].find((f) => f.type.startsWith("video/"));
  rateFile(file);
});

$("rate-url-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("rate-url").value.trim();
  if (!url || busy) return;
  try {
    rateFile(await fetchVideo(url, $("rate-status")));
  } catch (err) {
    setStatus($("rate-status"), "❌ " + err.message, { error: true });
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

// Ссылка, которой поделились в приложение (Android: «Поделиться» → TS Speeds)
const shared = new URLSearchParams(location.search).get("text") || new URLSearchParams(location.search).get("url");
if (shared) {
  const m = shared.match(/https?:\/\/\S+/);
  if (m) $("dl-url").value = m[0];
}

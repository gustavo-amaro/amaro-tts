const $ = (id) => document.getElementById(id);
const $$ = (sel) => document.querySelectorAll(sel);

const state = {
  docId: null,
  meta: null,
  cur: 0,            // current chunk
  playing: false,
  waiting: false,    // playing, but the current chunk has not been generated yet
  pages: new Map(),  // cache of loaded pages
  page: -1,          // rendered page
  ready: new Set(),
  failed: new Set(),
  durations: {},
  events: null,
  engine: "loading",
};

// two alternating audio elements: one plays while the other preloads the next chunk
const players = [new Audio(), new Audio()];
let active = 0;
let preloaded = null; // id of the chunk loaded in players[1 - active]
players.forEach((p) => { p.preload = "auto"; p.addEventListener("ended", onEnded); });

const audioUrl = (id) => `/api/docs/${state.docId}/audio/${id}`;

// API errors carry a stable `code` (an i18n key under "errors.") plus `params`; `detail` is the English fallback
function errorMessage(body, status) {
  if (body && body.code) return t(`errors.${body.code}`, body.params || {});
  if (body && typeof body.detail === "string") return body.detail;
  return t("errors.generic", { status });
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(errorMessage(await res.json().catch(() => null), res.status));
  return res.json();
}
const postJson = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---------------- engine ----------------
function setEngine(status) {
  state.engine = status;
  const el = $("engine-status");
  el.className = "engine " + status;
  el.querySelector("span").textContent = ["loading", "ready", "error"].includes(status) ? t(`engine.${status}`) : status;
}

function renderVoice(hasVoice) {
  $$(".voice-label").forEach((el) => { el.textContent = t(hasVoice ? "settings.voiceCustom" : "settings.voiceDefault"); });
  $$(".voice-clear").forEach((el) => { el.hidden = !hasVoice; });
}

async function refreshStatus() {
  const s = await api("/api/status");
  setEngine(s.engine);
  state.settings = s.settings;
  state.voice = s.voice;
  $$(".quality").forEach((el) => { el.value = String(s.settings.steps); });
  renderVoice(s.voice);
  updateEstimate();
  if (s.engine === "loading") setTimeout(refreshStatus, 3000);
  return s;
}

// ---------------- screens ----------------
function showScreen(name) {
  for (const id of ["home", "reader", "tts"]) $(id).hidden = id !== name;
  $("player").hidden = name !== "reader";
  $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === (name === "tts" ? "tts" : "home")));
}

function leaveReader() {
  stop();
  state.docId = null;
  if (state.events) state.events.close();
}

const emptyItem = (key) => `<li class="empty">${t(key)}</li>`;

async function showHome() {
  leaveReader();
  showScreen("home");
  await loadDocs();
}

async function loadDocs() {
  const docs = await api("/api/docs");
  const list = $("doclist");
  list.innerHTML = "";
  if (!docs.length) list.innerHTML = emptyItem("home.empty");
  for (const d of docs) {
    const li = document.createElement("li");
    const pct = d.chunks ? Math.round((100 * d.progress) / d.chunks) : 0;
    const info = d.status === "ready" ? t("home.docInfo", { count: d.pages, pct }) : t(`doc.status.${d.status}`);
    li.innerHTML = `<a href="#doc=${d.id}"><span></span><small></small></a>`;
    li.querySelector("span").textContent = d.name;
    li.querySelector("small").textContent = info;
    list.appendChild(li);
  }
}

function uploadPdf(file) {
  if (!file) return;
  const bar = $("upload-bar");
  bar.hidden = false;
  const fd = new FormData();
  fd.append("file", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/docs");
  xhr.upload.onprogress = (e) => { bar.firstElementChild.style.width = `${(100 * e.loaded) / e.total}%`; };
  xhr.onload = () => {
    bar.hidden = true;
    let body = null;
    try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
    if (xhr.status !== 200) return alertStatus(errorMessage(body, xhr.status));
    location.hash = `doc=${body.id}`;
  };
  xhr.send(fd);
}

function alertStatus(msg) { $("status").textContent = msg; console.warn(msg); }

// ---------------- reader ----------------
function pageMessage(text) {
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = text;
  $("page").replaceChildren(p);
}

async function openDoc(docId) {
  stop();
  state.docId = docId;
  Object.assign(state, { meta: null, pages: new Map(), page: -1, ready: new Set(), failed: new Set(), durations: {} });
  showScreen("reader");
  subscribe(docId);

  let meta = await api(`/api/docs/${docId}`);
  $("doc-title").textContent = meta.name;
  while (meta.status === "extracting") {
    pageMessage(t("doc.extracting"));
    await new Promise((r) => setTimeout(r, 700));
    meta = await api(`/api/docs/${docId}`);
  }
  if (meta.status !== "ready") {
    pageMessage(t("doc.readFailed", { error: meta.error || meta.status }));
    return;
  }
  state.meta = meta;
  $("page-total").textContent = meta.pages;
  $("page-input").max = meta.pages;
  if (!meta.chunks) {
    pageMessage(t("doc.noText"));
    return;
  }
  await goTo(meta.progress || 0, false);
}

function subscribe(docId) {
  if (state.events) state.events.close();
  const es = new EventSource(`/api/docs/${docId}/events`);
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "engine") setEngine(ev.status);
    else if (ev.type === "chunk_ready") onChunkReady(ev.id, ev.duration);
    else if (ev.type === "chunk_error") onChunkFailed(ev.id);
    else if (ev.type === "generating" && state.waiting && ev.id === state.cur) setStatus(t("player.generatingCurrent"));
  };
  state.events = es;
}

// page that contains the chunk: largest p with page_first[p] <= id
function pageOf(id) {
  const pf = state.meta.page_first;
  let lo = 0, hi = pf.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (pf[mid] <= id) lo = mid; else hi = mid - 1;
  }
  return lo;
}

async function loadPage(n) {
  if (!state.pages.has(n)) {
    const p = await api(`/api/docs/${state.docId}/pages/${n}`);
    p.ready.forEach((id) => state.ready.add(id));
    state.pages.set(n, p);
  }
  return state.pages.get(n);
}

async function renderPage(n) {
  const p = await loadPage(n);
  state.page = n;
  $("page-input").value = n + 1;
  const root = $("page");
  root.innerHTML = "";
  if (!p.chunks.length) {
    pageMessage(t("doc.emptyPage"));
    return;
  }
  let pos = 0;
  for (const c of p.chunks) {
    if (c.start > pos) root.append(p.text.slice(pos, c.start));
    const span = document.createElement("span");
    span.className = "chunk";
    span.dataset.id = c.id;
    span.textContent = p.text.slice(c.start, c.end);
    root.append(span);
    pos = c.end;
  }
  if (pos < p.text.length) root.append(p.text.slice(pos));
  decorate();
  // preload the next page so turning it is instant
  if (n + 1 < state.meta.pages) loadPage(n + 1).catch(() => {});
}

function decorate() {
  for (const span of $("page").querySelectorAll(".chunk")) {
    const id = +span.dataset.id;
    span.classList.toggle("cur", id === state.cur);
    span.classList.toggle("ready", state.ready.has(id));
    span.classList.toggle("failed", state.failed.has(id));
  }
}

function spanOf(id) { return $("page").querySelector(`.chunk[data-id="${id}"]`); }

function scrollToCurrent() {
  const span = spanOf(state.cur);
  if (!span) return;
  const r = span.getBoundingClientRect();
  const bottomLimit = window.innerHeight - $("player").offsetHeight - 40;
  if (r.top < 80 || r.bottom > bottomLimit) span.scrollIntoView({ behavior: "smooth", block: "center" });
}

// move the cursor to chunk `id`; if `play`, start playing
async function goTo(id, play = state.playing) {
  id = Math.max(0, Math.min(id, state.meta.chunks - 1));
  state.cur = id;
  const page = pageOf(id);
  if (page !== state.page) await renderPage(page);
  else decorate();
  scrollToCurrent();
  updateProgress();
  const res = await postJson(`/api/docs/${state.docId}/cursor`, { chunk: id });
  res.ready.forEach((r) => state.ready.add(r));
  decorate();
  if (play) playCurrent();
  else updateBuffer();
}

function waitingStatus() {
  return engineReady() ? t("player.generatingCurrent") : t("player.waitingModel");
}

function playCurrent() {
  state.playing = true;
  $("play").textContent = "⏸";
  const id = state.cur;
  if (state.failed.has(id)) return advance();
  if (!state.ready.has(id)) {
    state.waiting = true;
    players[active].pause();
    setStatus(waitingStatus());
    return;
  }
  state.waiting = false;
  const other = 1 - active;
  if (preloaded === id) {
    active = other; // the next chunk was already loaded in the other player
  } else {
    players[active].src = audioUrl(id);
  }
  preloaded = null;
  players[1 - active].pause();
  const p = players[active];
  p.playbackRate = p.defaultPlaybackRate = +$("speed").value;
  p.play().catch((e) => alertStatus(t("player.clickToPlay", { error: e.message })));
  preloadNext();
  updateBuffer();
}

function preloadNext() {
  const next = state.cur + 1;
  if (preloaded === next || !state.ready.has(next)) return;
  const p = players[1 - active];
  p.src = audioUrl(next);
  p.load();
  preloaded = next;
}

function onEnded(e) {
  if (e.target !== players[active] || !state.playing) return;
  advance();
}

function advance() {
  if (state.cur + 1 >= state.meta.chunks) {
    stop();
    state.ended = true;
    setStatus(t("player.end"));
    return;
  }
  goTo(state.cur + 1, true);
}

function pause() {
  state.playing = false;
  state.waiting = false;
  players[active].pause();
  $("play").textContent = "▶";
  updateBuffer();
}

function stop() {
  pause();
  players.forEach((p) => p.removeAttribute("src"));
  preloaded = null;
}

function togglePlay() {
  if (!state.meta) return;
  if (state.playing) return pause();
  const p = players[active];
  // resume from the paused position if the same chunk is still loaded
  if (p.src && p.src.endsWith(`/audio/${state.cur}`) && !p.ended && p.currentTime > 0) {
    state.playing = true;
    $("play").textContent = "⏸";
    p.playbackRate = +$("speed").value;
    p.play();
    updateBuffer();
  } else {
    playCurrent();
  }
}

function onChunkReady(id, duration) {
  state.ready.add(id);
  if (duration) state.durations[id] = duration;
  const span = spanOf(id);
  if (span) span.classList.add("ready");
  if (state.waiting && id === state.cur) playCurrent();
  else if (state.playing && id === state.cur + 1) preloadNext();
  updateBuffer();
}

function onChunkFailed(id) {
  state.failed.add(id);
  const span = spanOf(id);
  if (span) span.classList.add("failed");
  if (state.waiting && id === state.cur) advance();
}

const engineReady = () => state.engine === "ready";

function setStatus(text) { $("status").textContent = text; }

function updateBuffer() {
  if (state.waiting || !state.meta) return;
  state.ended = false;
  let n = 0, secs = 0;
  for (let i = state.cur + 1; state.ready.has(i); i++) { n++; secs += state.durations[i] || 0; }
  const ahead = n
    ? t("player.ahead", { count: n }) + (secs ? " " + t("player.aheadSeconds", { seconds: Math.round(secs) }) : "")
    : t("player.generatingNext");
  const where = t("player.position", { current: state.cur + 1, total: state.meta.chunks });
  setStatus(`${where} · ${state.playing ? ahead : t("player.paused")}`);
}

// re-render the status line in the current language
function refreshPlayerStatus() {
  if (!state.meta) return;
  if (state.waiting) setStatus(waitingStatus());
  else if (state.ended) setStatus(t("player.end"));
  else updateBuffer();
}

function updateProgress() {
  $("doc-progress").style.width = `${(100 * state.cur) / Math.max(1, state.meta.chunks - 1)}%`;
}

function goToPage(n) {
  if (!state.meta) return;
  n = Math.max(0, Math.min(n, state.meta.pages - 1));
  goTo(state.meta.page_first[n]);
}

// ---------------- UI events ----------------
$("file").addEventListener("change", (e) => uploadPdf(e.target.files[0]));
const drop = $("drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadPdf(e.dataTransfer.files[0]); });

$("play").onclick = togglePlay;
$("next").onclick = () => state.meta && goTo(state.cur + 1);
$("prev").onclick = () => state.meta && goTo(state.cur - 1);
$("prev-page").onclick = () => goToPage(state.page - 1);
$("next-page").onclick = () => goToPage(state.page + 1);
$("page-input").addEventListener("change", (e) => goToPage(+e.target.value - 1));
$("page").addEventListener("click", (e) => {
  const span = e.target.closest(".chunk");
  if (span) goTo(+span.dataset.id, true);
});
$("speed").addEventListener("change", (e) => players.forEach((p) => { p.playbackRate = p.defaultPlaybackRate = +e.target.value; }));

async function settingsChanged() {
  // the audio cache depends on the settings: start over from the current chunk
  const wasPlaying = state.playing;
  stop();
  state.ready = new Set();
  state.failed = new Set();
  state.durations = {};
  state.pages = new Map();
  state.page = -1;
  if (state.meta) await goTo(state.cur, wasPlaying);
}

$$(".quality").forEach((el) => el.addEventListener("change", async (e) => {
  await postJson("/api/settings", { steps: +e.target.value });
  await refreshStatus();
  await settingsChanged();
}));
$$(".voice-file").forEach((el) => el.addEventListener("change", async (e) => {
  const fd = new FormData();
  fd.append("file", e.target.files[0]);
  try {
    await api("/api/voice", { method: "POST", body: fd });
  } catch (err) {
    alertStatus(err.message);
    showTtsError(err.message);
  }
  e.target.value = "";
  await refreshStatus();
  await settingsChanged();
}));
$$(".voice-clear").forEach((el) => { el.onclick = async () => {
  await api("/api/voice", { method: "DELETE" });
  await refreshStatus();
  await settingsChanged();
}; });

// ---------------- text to audio ----------------
const CHARS_PER_SEC = 16.5; // measured: ~200 characters ≈ 12 s of speech
const fmtTime = (secs) => (secs < 60 ? t("time.seconds", { n: Math.round(secs) }) : t("time.minutes", { n: Math.round(secs / 60) }));

function updateEstimate() {
  const n = $("tts-text").value.trim().length;
  $("tts-count").textContent = t("tts.chars", { count: n });
  if (!n) { $("tts-estimate").textContent = ""; return; }
  const audio = n / CHARS_PER_SEC;
  const rtf = state.settings && state.settings.steps >= 10 ? 1.1 : 0.8;
  $("tts-estimate").textContent = t("tts.estimate", { audio: fmtTime(audio), generate: fmtTime(audio * rtf) });
}

function showTtsError(msg) {
  const el = $("tts-error");
  el.textContent = msg || "";
  el.hidden = !msg;
}

async function showTts() {
  leaveReader();
  showScreen("tts");
  await loadExports();
}

async function loadExports() {
  const jobs = await api("/api/exports");
  const list = $("exports");
  list.innerHTML = "";
  if (!jobs.length) list.innerHTML = emptyItem("tts.empty");
  for (const job of jobs) list.appendChild(renderExport(job));
}

const progressText = (done, total) => t("tts.progress", { done, total, pct: Math.round((100 * done) / total) });

function renderExport(job) {
  const li = document.createElement("li");
  li.dataset.job = job.id;
  const when = i18n.date(new Date(job.created * 1000), { dateStyle: "short", timeStyle: "short" });
  const voice = t(job.voice ? "tts.voiceCustom" : "tts.voiceDefault");
  li.innerHTML = `
    <div class="exp-head"><span class="exp-title"></span><span class="exp-info"></span></div>
    <div class="exp-body"></div>`;
  li.querySelector(".exp-title").textContent = job.title;
  li.querySelector(".exp-title").title = job.title;
  const info = li.querySelector(".exp-info");
  const body = li.querySelector(".exp-body");
  const del = document.createElement("button");
  del.className = "link";
  del.onclick = async () => { await api(`/api/exports/${job.id}`, { method: "DELETE" }); li.remove(); };

  if (job.status === "done") {
    info.textContent = `${fmtTime(job.duration)} · ${voice} · ${when}`;
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.src = `/api/exports/${job.id}/audio`;
    const dl = document.createElement("a");
    dl.className = "download";
    dl.href = `/api/exports/${job.id}/audio?download=1`;
    dl.textContent = t("tts.download");
    del.textContent = t("tts.delete");
    body.append(audio, dl, del);
  } else if (job.status === "error") {
    info.textContent = when;
    const err = document.createElement("span");
    err.className = "exp-error";
    err.textContent = t("tts.failed", { error: job.error });
    del.textContent = t("tts.delete");
    body.append(err, del);
  } else {
    const pct = Math.round((100 * job.done) / job.total);
    info.textContent = job.done ? progressText(job.done, job.total) : t("tts.queued");
    body.innerHTML = `<div class="bar"><div style="width:${pct}%"></div></div>`;
    del.textContent = t("tts.cancel");
    body.append(del);
  }
  return li;
}

function onExportEvent(ev) {
  const li = document.querySelector(`#exports li[data-job="${ev.job}"]`);
  if (ev.type === "export_progress" && li) {
    li.querySelector(".exp-info").textContent = progressText(ev.done, ev.total);
    const bar = li.querySelector(".bar div");
    if (bar) bar.style.width = `${Math.round((100 * ev.done) / ev.total)}%`;
  } else if ((ev.type === "export_done" || ev.type === "export_error") && !$("tts").hidden) {
    loadExports();
  }
}

$("tts-text").addEventListener("input", updateEstimate);
$("tts-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showTtsError("");
  const text = $("tts-text").value.trim();
  if (!text) return showTtsError(t("tts.emptyText"));
  $("tts-submit").disabled = true;
  try {
    await postJson("/api/exports", { text, style: $("tts-style").value });
    await loadExports();
  } catch (err) {
    showTtsError(err.message);
  } finally {
    $("tts-submit").disabled = false;
  }
});

document.addEventListener("keydown", (e) => {
  if (!state.meta || $("reader").hidden || e.target.matches("input, select, textarea")) return;
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  else if (e.code === "ArrowRight") goTo(state.cur + 1);
  else if (e.code === "ArrowLeft") goTo(state.cur - 1);
});

// ---------------- language ----------------
function setupLanguagePicker() {
  const select = $("lang");
  for (const [code, name] of Object.entries(i18n.locales)) select.add(new Option(name, code));
  select.value = i18n.locale;
  select.addEventListener("change", () => i18n.setLocale(select.value));
}

// static markup is handled by i18n.translate(); re-render what JS generated
function onLocaleChange() {
  $("lang").value = i18n.locale;
  setEngine(state.engine);
  renderVoice(state.voice);
  updateEstimate();
  refreshPlayerStatus();
  if (!$("tts-error").hidden) showTtsError("");
  if (!$("home").hidden) loadDocs();
  if (!$("tts").hidden) loadExports();
}

// ---------------- boot ----------------
function route() {
  const m = location.hash.match(/doc=([0-9a-f]+)/);
  if (m) openDoc(m[1]).catch((e) => { alertStatus(e.message); showHome(); });
  else if (location.hash === "#tts") showTts();
  else showHome();
}

i18n.init().catch((e) => console.warn("i18n:", e)).finally(() => {
  setupLanguagePicker();
  document.addEventListener("localechange", onLocaleChange);
  const exportEvents = new EventSource("/api/exports/events");
  exportEvents.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "engine") setEngine(ev.status);
    else onExportEvent(ev);
  };
  window.addEventListener("hashchange", route);
  refreshStatus();
  route();
});

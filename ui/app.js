"use strict";
// Montażownia UI. All times on screen are OUTPUT seconds (the finished film); the server owns the files.
const $ = (id) => document.getElementById(id);
const S = { films: [], film: null, view: null, peaks: null, sel: null, playhead: 0, timeline: null, items: null,
  groups: null, polling: new Set(), icons: null, lastJob: null, fixJob: null, previewJob: null,
  dirty: { inspector: false, globals: false }, origin: null };

// ---------- helpers ----------
const fmt = (s) => { if (s == null) return "–"; const d = Math.round(s * 10), m = Math.floor(d / 600); return `${m}:${((d - m * 600) / 10).toFixed(1).padStart(4, "0")}`; };  // round first: 59.96 -> 1:00.0
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function toast(msg, err = false) {
  const t = $("toast"); t.textContent = msg; t.className = "on" + (err ? " err" : "");
  clearTimeout(toast.h); toast.h = setTimeout(() => (t.className = ""), err ? 6000 : 2500);
}
function setConn(ok) { $("conn").hidden = ok; }
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts }); } catch {
    const msg = "Brak połączenia z panelem: czy działa jeszcze „montazownia” w terminalu?";
    setConn(false); toast(msg, true); throw new Error(msg);
  }
  setConn(true);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : null;
  if (!r.ok) { const msg = (data && (data.error || data.detail)) || `błąd ${r.status}`; toast(msg, true); throw new Error(msg); }
  return data;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const fq = () => encodeURIComponent(S.film);

// buttons: [label, value, class]; the LAST one is the safe choice (Esc, initial focus unless `focus` names an element id)
let modalQueue = Promise.resolve();
function modal(html, buttons, focus = null) {  // one dialog at a time: a second call waits for the first to close
  const run = modalQueue.then(() => showModal(html, buttons, focus));
  modalQueue = run.catch(() => {});
  return run;
}
function showModal(html, buttons, focus) {
  $("modalbody").innerHTML = html; const row = $("modalbtns"); row.innerHTML = "";
  const back = document.activeElement, box = $("modal");
  return new Promise((res) => {
    const close = (val) => { box.classList.remove("on"); document.removeEventListener("keydown", keys, true); if (back) back.focus(); res(val); };
    const keys = (e) => {
      if (e.key === "Escape") { e.preventDefault(); close(buttons[buttons.length - 1][1]); return; }
      if (e.key === "Enter" && e.target.matches("#modal input")) { e.preventDefault(); if (!e.repeat) close(buttons[0][1]); return; }
      if (e.key !== "Tab") return;
      const f = [...box.querySelectorAll("input, button, textarea, select")];  // keep focus inside the dialog
      const i = f.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
    };
    for (const [label, val, cls] of buttons) {
      const b = document.createElement("button"); b.textContent = label; if (cls) b.className = cls;
      b.onclick = () => close(val); row.appendChild(b);
    }
    box.classList.add("on"); document.addEventListener("keydown", keys, true);
    ((focus && $(focus)) || row.lastElementChild).focus();
  });
}
const modalOpen = () => $("modal").classList.contains("on");
async function ask(title, value = "") {
  let v = null;
  const ok = await modal(`<h3 id="modaltitle"><label for="askv">${esc(title)}</label></h3><input id="askv" style="width:100%" value="${esc(value)}">`,
    [["OK", true], ["Anuluj", false, "ghost"]], "askv").then((r) => { v = $("askv").value; return r; });
  return ok ? v : null;
}

// ---------- jobs ----------
// a button that starts a job stays disabled while a job of that kind runs (a second click would queue a copy)
const JOB_BUTTONS = { check: "check", fix: "fix", beats: "beats", broll: "broll", preview: "preview", render: "render" };
function busy(kind, on) {
  S.running = S.running || {};
  if (on) S.running[kind] = true; else delete S.running[kind];
  if (JOB_BUTTONS[kind]) $(JOB_BUTTONS[kind]).disabled = !!on;
}
async function startJob(kind, path) {
  busy(kind, true);  // at once: the click that queues the job is the last one that can
  try { return await post(path); } catch (e) { busy(kind, false); throw e; }
}
async function watchJob(id, onDone) {
  if (!id) return; S.lastJob = id; let since = 0, fails = 0;
  const log = $("log");
  const tick = async () => {
    let j;
    try { j = await api(`/api/jobs/${id}?since=${since}`); fails = 0; } catch {
      // panel restarting or network hiccup: keep trying for a minute, then say so instead of freezing on "trwa"
      if (++fails < 30) { $("job").textContent = "brak połączenia, ponawiam…"; return setTimeout(tick, 2000); }
      $("job").textContent = "utracono połączenie z panelem: odśwież stronę"; return;
    }
    if (j.lines.length) { log.textContent += j.lines.join("\n") + "\n"; log.scrollTop = log.scrollHeight; }
    since = j.n;
    $("job").textContent = `${label(j.kind)}: ${statusPl(j.status)} ${j.seconds}s`;
    busy(j.kind, j.status === "queued" || j.status === "running");
    if (j.status === "queued" || j.status === "running") return setTimeout(tick, 800);
    if (j.status !== "done") toast(`${label(j.kind)}: ${statusPl(j.status)} ${j.error || ""}`, j.status === "failed");
    if (onDone) onDone(j);
  };
  tick();
}
const label = (k) => ({ rebuild: "Przeliczanie", check: "Krytyk", fix: "Poprawki", preview: "Podgląd", render: "Render", beats: "Momenty", broll: "B-roll" }[k] || k);
const statusPl = (s) => ({ queued: "w kolejce", running: "trwa", done: "gotowe", failed: "błąd", stopped: "zatrzymane" }[s] || s);

// ---------- loading ----------
async function loadProject() {
  const p = await api("/api/project");
  S.films = p.films; S.out = p.out;
  if (!p.films.length) { $("timeline").textContent = "W tym katalogu nie ma plan_*.json."; return; }
  $("film").innerHTML = p.films.map((f) => `<option>${esc(f)}</option>`).join("");
  const want = new URLSearchParams(location.search).get("film") || localStorage.getItem("mz.film");
  S.film = p.films.includes(want) ? want : p.films[0];
  $("film").value = S.film;
  showBattery(p.battery);
  await loadFilm(true);
  // after a page reload: follow jobs that are still running (a full render keeps its live log)
  for (const j of await api("/api/jobs")) if (j.status === "queued" || j.status === "running") watchJob(j.id, () => loadFilm());
}
async function loadFilm(fresh = false) {
  const v = await api(`/api/film/${fq()}`);
  S.view = v;
  if (fresh) { S.peaks = null; api(`/api/film/${fq()}/peaks`).then((d) => { S.peaks = d; drawWave(); }).catch(() => { S.peaks = { error: true }; drawWave(); }); }
  $("undo").disabled = !v.history.undo; $("redo").disabled = !v.history.redo;
  $("undo").title = v.history.undo ? `Cofnij: ${v.history.undo} (Ctrl+Z)` : "Nic do cofnięcia";
  $("redo").title = v.history.redo ? `Ponów: ${v.history.redo}` : "Nic do ponowienia";
  renderTimeline(fresh); renderTranscript(); renderCritic();
  // a rebuild ends a few seconds after an edit: fields the user started typing into stay as typed (until Zapisz / another selection)
  if (!S.dirty.globals) renderGlobals();
  if (!S.dirty.inspector) renderInspector(); else toast("oś przeliczona; niezapisane pola w inspektorze zostały", false);
}
for (const [key, id] of [["inspector", "inspector"], ["globals", "globals"]]) {
  $(id).addEventListener("input", () => { S.dirty[key] = true; S.origin = null; });
}
// which panel's button started the edit editOp() is about to send. Capture phase: set before the button's own handler.
// It lasts through that handler's dialogs (Duplikuj/Usuń ask first) and ends at the next click or key anywhere else.
document.addEventListener("click", (e) => {
  const panel = ["inspector", "globals"].find((k) => e.target.closest(`#${k}`));
  const button = e.target.closest("button");
  if (panel && button && !button.closest(".icons")) S.origin = panel;  // icon picks only fill a field: not a save
  else if (!e.target.closest("#modal")) S.origin = null;
}, true);
for (const ev of ["keydown", "pointerdown"]) {  // timeline drags and shortcuts edit too: they are not this panel's save
  document.addEventListener(ev, (e) => { if (!e.target.closest?.("#modal, #inspector, #globals")) S.origin = null; }, true);
}
function showBattery(b) {
  const el = $("battery");
  if (!b || b.percent == null) { el.textContent = ""; return; }
  el.textContent = `bateria ${b.percent}%${b.charging ? " ⚡" : ""}`;
  el.className = "badge" + (b.warn ? " warn" : "");
  el.title = b.warn ? "Poniżej 40% i bez ładowania: podłącz zasilacz przed pełnym renderem" : b.status || "";
}

// ---------- timeline ----------
const NO_FACE_LIMIT = 2.0;  // = planops.NO_FACE_LIMIT
const T = (s) => new Date(Math.round(s * 1000));
const sec = (d) => (+d) / 1000;
function zoomText(z) { return typeof z === "object" ? `${z.a}→${z.b}` : `${z}`; }
function buildItems(tl) {
  const it = [];
  for (const s of tl.segments) {
    it.push({ id: `seg:${s.i}`, group: "v1", start: T(s.start), end: T(s.end), content: zoomText(s.zoom),
      className: `k-${s.kind}${s.override ? " k-override" : ""}`, title: `ujęcie ${s.i + 1}: ${fmt(s.start)}–${fmt(s.end)}, zoom ${zoomText(s.zoom)}, limit ${s.zmax ?? "?"}` });
  }
  for (const o of tl.overlays) {
    const slide = o.pip || o.type === "slide";  // full-screen cards (slide, web page) get their own lane
    it.push({ id: `gfx:${o.id}`, group: slide ? "v3" : "v2", start: T(o.start), end: T(o.end), editable: { updateTime: true },
      content: esc(`${o.id} · ${Array.isArray(o.text) ? o.text[0] : o.text}`), className: slide ? "g-slide" : "g-gfx",
      title: esc(`${o.type}: ${Array.isArray(o.text) ? o.text.join(" / ") : o.text}${o.pip ? " (z PiP)" : ""}`) });
  }
  for (const [k, b] of (tl.broll || []).entries()) {
    it.push({ id: `broll:${k}`, group: "v4", start: T(b.start), end: T(b.end), content: esc(b.query), className: "g-broll",
      title: esc(`b-roll na „${b.said}”: ${b.query}${b.author ? ` · autor: ${b.author}` : ""}`) });
  }
  it.push({ id: "music", group: "a2", start: T(0), end: T(tl.total), content: `muzyka (seed ${tl.music.seed}, ${tl.music.db} dB)`, className: "g-music" });
  tl.sfx.forEach((e, k) => it.push({ id: `sfx:${k}`, group: "a3", start: T(e.t), type: "point", content: e.kind, className: "g-sfx" }));
  for (const [k, b] of (tl.beats || []).entries()) {
    if (b.at == null) continue;
    it.push({ id: b.pos != null ? `beat:${b.pos}` : `pace:${k}`, group: "mo", start: T(b.at), type: "point", content: esc(b.label),
      className: `b-${b.fx || "none"}${b.off ? " b-off" : ""}${b.force ? " b-force" : ""}`,
      title: esc(`${b.label} „${b.text}” · ${FX[b.fx] || "jeszcze nie przeliczony"}${b.gfx ? ` · grafika: ${b.gfx}` : ""}${b.broll ? ` · b-roll: ${b.broll}` : ""}${b.force ? " · wymuszony" : ""}`) });
  }
  for (const c of tl.issues) {
    if (c.t == null) continue;
    it.push({ id: `iss:${c.id}`, group: "kr", start: T(c.t), type: "point", content: c.code, className: `c-${c.sev}`, title: esc(c.msg) });
  }
  return it;
}
function renderTimeline(fresh) {
  const tl = S.view.timeline;
  if (!tl.ready) { $("timeline").innerHTML = '<div class="muted" style="padding:20px">Brak EDL: kliknij „Sprawdź”, żeby zbudować oś.</div>'; S.timeline = null; return; }
  const groups = [
    { id: "mo", content: "Momenty" }, { id: "v1", content: "V1 ujęcia" }, { id: "v2", content: "V2 grafiki" }, { id: "v3", content: "V3 slajdy+PiP" }, { id: "v4", content: "V4 b-roll" },
    { id: "a2", content: "A2 muzyka" }, { id: "a3", content: "A3 SFX" }, { id: "kr", content: "Krytyk" }];
  const items = buildItems(tl);
  if (S.timeline && !fresh) {
    S.items.clear(); S.items.add(items);
    S.timeline.setOptions({ max: T(tl.total + 2), zoomMax: Math.max(10000, (tl.total + 2) * 1000) });
    if (S.sel) S.timeline.setSelection([S.sel], { focus: false });
    drawWave(); return;
  }
  if (S.timeline) S.timeline.destroy();
  $("timeline").innerHTML = "";
  S.items = new vis.DataSet(items); S.groups = new vis.DataSet(groups);
  const lab = (d) => fmt(sec(d)).replace(/\.0$/, "");
  S.timeline = new vis.Timeline($("timeline"), S.items, S.groups, {
    start: T(0), end: T(Math.min(tl.total, 120) + 1), min: T(-1), max: T(tl.total + 2), zoomMin: 1500,
    zoomMax: Math.max(10000, (tl.total + 2) * 1000), stack: false, showCurrentTime: false, selectable: true,
    editable: { updateTime: false, add: false, remove: false, overrideItems: false }, snap: null, orientation: "top",
    margin: { item: 4, axis: 4 }, format: { minorLabels: lab, majorLabels: () => "" }, groupHeightMode: "fixed",
    onMove: (item, cb) => { cb(null); moveGfx(item); }, tooltip: { followMouse: true }, verticalScroll: false, height: 305,
  });
  S.timeline.addCustomTime(T(S.playhead), "ph");
  S.timeline.on("select", (p) => select(p.items[0] || null));
  S.timeline.on("click", (p) => { if (!p.item && p.time) setPlayhead(sec(p.time)); });
  S.timeline.on("rangechanged", drawWave); S.timeline.on("changed", drawWave);
  setTimeout(drawWave, 50);
}
function setPlayhead(t) {
  S.playhead = Math.max(0, t);
  if (S.timeline) S.timeline.setCustomTime(T(S.playhead), "ph");
  markNearWord();
}
async function moveGfx(item) {
  const gid = item.id.slice(4), t = sec(item.start);
  const w = nearestKept(t);
  if (!w) return toast("brak słowa w pobliżu", true);
  const lead = Math.round((t - w.out) * 100) / 100;
  await editOp({ op: "anchor_gfx", id: gid, i: w.i });
  await editOp({ op: "set_gfx_lead", id: gid, lead: Math.max(-10, Math.min(10, lead)) });  // anchor_gfx keeps the old lead
}
function nearestKept(t) {
  let best = null;
  for (const w of S.view.timeline.words) if (w.out != null && (!best || Math.abs(w.out - t) < Math.abs(best.out - t))) best = w;
  return best;
}

// voice waveform in output time: peaks are per SOURCE second, mapped through the segments
function drawWave() {
  const c = $("wave"), box = $("wavebox"), tl = S.view && S.view.timeline;
  const center = document.querySelector("#timeline .vis-panel.vis-center");
  if (!center || !tl || !tl.ready) return;
  const bl = box.getBoundingClientRect(), cr = center.getBoundingClientRect();
  c.style.left = `${cr.left - bl.left}px`; c.width = Math.max(1, Math.round(cr.width)); c.height = 54; c.style.width = `${cr.width}px`;
  const g = c.getContext("2d"); g.clearRect(0, 0, c.width, c.height);
  if (!S.peaks || S.peaks.error) { g.fillStyle = "#8b919c"; g.fillText(S.peaks ? "nie udało się odczytać dźwięku (zob. log panelu)" : "liczę falę dźwięku…", 8, 30); return; }
  const w = S.timeline.getWindow(), t0 = sec(w.start), t1 = sec(w.end), px = c.width / (t1 - t0);
  const P = S.peaks.peaks, R = S.peaks.rate;
  g.fillStyle = "#2de2c4";
  for (const s of tl.segments) {
    if (s.end < t0 || s.start > t1) continue;
    for (let x = Math.max(0, Math.floor((s.start - t0) * px)); x < Math.min(c.width, (s.end - t0) * px); x++) {
      const tout = t0 + x / px, src = s.src_in + (tout - s.start);
      const v = P[Math.floor(src * R)] || 0, h = (v / 255) * 25;
      g.fillRect(x, 27 - h, 1, Math.max(1, h * 2));
    }
  }
  g.fillStyle = "rgba(255,255,255,.08)";
  for (const s of tl.segments) { const x = (s.start - t0) * px; if (x >= 0 && x <= c.width) g.fillRect(x, 0, 1, 54); }
}

// ---------- transcript ----------
function renderTranscript() {
  const words = S.view.timeline.words, plan = S.view.plan;
  let lo = 0, hi = words.length - 1;
  if (!$("allwords").checked && plan.keeps.length) {
    lo = Math.max(0, Math.min(...plan.keeps.map((k) => k[0])) - 40);
    hi = Math.min(words.length - 1, Math.max(...plan.keeps.map((k) => k[1])) + 40);
  }
  const selG = S.sel && S.sel.startsWith("gfx:") ? S.sel.slice(4) : null;
  const out = [];
  for (let i = lo; i <= hi; i++) {
    const w = words[i];
    if (i > lo && !w.kept !== !words[i - 1].kept && !w.kept) out.push('<span class="gap"></span>');
    for (const g of w.gfx) out.push(`<span class="gtag${g === selG ? " sel" : ""}" data-g="${esc(g)}" tabindex="0" role="button" aria-label="grafika ${esc(g)}">${esc(g)}</span>`);
    const cls = `w${w.kept ? "" : " cut"}${w.emph != null ? (w.emph_live === false ? " emph dead" : " emph") : ""}`;
    out.push(`<span class="${cls}${i === S.cursor ? " cur" : ""}" id="w${i}" role="option" aria-selected="${w.kept}" data-i="${i}" title="${w.out != null ? "w filmie " + fmt(w.out) : "wycięte"} · źródło ${fmt(w.src)}">${esc(w.text)}</span>`);
  }
  $("transcript").innerHTML = out.join("");
  markNearWord();
}
function markNearWord() {
  document.querySelectorAll("#transcript .near").forEach((e) => e.classList.remove("near"));
  if (!S.view || !S.view.timeline.ready) return;
  const w = nearestKept(S.playhead);
  const el = w && document.querySelector(`#transcript .w[data-i="${w.i}"]`);
  if (el) el.classList.add("near");
}
$("transcript").addEventListener("click", (e) => {
  const tag = e.target.closest(".gtag");
  if (tag) { select(`gfx:${tag.dataset.g}`); return; }
  const el = e.target.closest(".w"); if (!el) return;
  cursorTo(+el.dataset.i); wordAction(+el.dataset.i, e);
});
// keyboard: arrows move over words, Enter = click, Alt+Enter = punch-in, Shift+Enter = anchor the selected graphic
$("transcript").addEventListener("keydown", (e) => {
  const tag = e.target.closest && e.target.closest(".gtag");
  if (tag) {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault(); const g = tag.dataset.g; select(`gfx:${g}`);  // select() redraws the transcript: focus the new tag
    const again = [...document.querySelectorAll("#transcript .gtag")].find((x) => x.dataset.g === g); if (again) again.focus();
    return;
  }
  if (e.repeat && e.key === "Enter") return;  // a held Enter must not cut/restore a word again and again
  const ws = [...document.querySelectorAll("#transcript .w")];
  const at = ws.findIndex((x) => +x.dataset.i === S.cursor);
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault(); const n = ws[Math.max(0, Math.min(ws.length - 1, at + (e.key === "ArrowRight" ? 1 : -1)))];
    if (n) cursorTo(+n.dataset.i);
  } else if (e.key === "Enter" && at >= 0) { e.preventDefault(); wordAction(S.cursor, e); }
});
function cursorTo(i) {
  document.querySelectorAll("#transcript .cur").forEach((x) => x.classList.remove("cur"));
  S.cursor = i; const el = $(`w${i}`);
  if (el) { el.classList.add("cur"); el.scrollIntoView({ block: "nearest" }); $("transcript").setAttribute("aria-activedescendant", el.id); }
}
async function wordAction(i, e) {
  const w = S.view.timeline.words[i];
  if (e.shiftKey) {
    if (!S.sel || !S.sel.startsWith("gfx:")) return toast("najpierw zaznacz grafikę (na osi albo jej znacznik w tekście)", true);
    return editOp({ op: "anchor_gfx", id: S.sel.slice(4), i });
  }
  if (e.ctrlKey || e.metaKey) {  // a word that already has a moment: open it instead of adding a second one
    const at = (S.view.plan.beats || []).findIndex((b) => b.word === i);
    return at >= 0 ? select(`beat:${at}`) : addBeat(i);
  }
  if (e.altKey) {
    if (w.emph != null) { S.sel = `emph:${w.emph}`; renderInspector(); return; }
    await editOp({ op: "add_emph", i });
    const v = S.view.timeline.words[i]; if (v.emph != null) { S.sel = `emph:${v.emph}`; renderInspector(); }
    return;
  }
  if (w.out != null) setPlayhead(w.out);
  await editOp({ op: "toggle_word", i });
}
$("allwords").onchange = renderTranscript;

// ---------- edits ----------
async function editOp(body) {
  const from = S.origin; S.origin = null;  // the panel whose button sent this (read before the first await)
  const r = await post(`/api/film/${fq()}/edit`, body);
  if (from) S.dirty[from] = false;  // saved: only now may the panel redraw from the server (a failed save keeps the typing)
  if (!r.changed) return r;
  $("dirty").textContent = "przeliczam oś…";
  await loadFilm();
  watchJob(r.job, async () => { $("dirty").textContent = ""; await loadFilm(); });
  return r;
}
async function undoRedo(which) {
  const r = await post(`/api/${which}?film=${fq()}`);
  if (!r.label) return toast(which === "undo" ? "nic do cofnięcia" : "nic do ponowienia");
  const other = (r.films || []).filter((f) => f !== S.film);
  toast(`${which === "undo" ? "Cofnięto" : "Ponowiono"}: ${r.label}${other.length ? ` (film ${other.join(", ")})` : ""}`);
  await loadFilm(); watchJob(r.job, () => loadFilm());
}
$("undo").onclick = () => undoRedo("undo");
$("redo").onclick = () => undoRedo("redo");
document.addEventListener("keydown", (e) => {
  if (modalOpen() || e.target.matches("input, textarea, select")) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undoRedo(e.shiftKey ? "redo" : "undo"); }
});

// ---------- inspector ----------
function select(id) {
  S.sel = id;
  if (S.timeline) S.timeline.setSelection(id && S.items.get(id) ? [id] : [], { focus: false });
  if (id && id.startsWith("iss:")) { const c = S.view.timeline.issues.find((x) => `iss:${x.id}` === id); if (c && c.t != null) setPlayhead(c.t); }
  else if (id && S.items && S.items.get(id)) setPlayhead(sec(S.items.get(id).start));  // preview/new graphic go where you clicked
  renderInspector(); renderTranscript();
  const near = document.querySelector("#transcript .near"); if (near) near.scrollIntoView({ block: "center" });
}
function field(label, html) {
  const id = (html.match(/id="([^"]+)"/) || [])[1];  // the first control in the cell is what the label names
  return `<label${id ? ` for="${id}"` : ""}>${label}</label><div>${html}</div>`;
}
function num(id, v, step = 0.01, extra = "", aria = "") {
  return `<input type="number" id="${id}" value="${v ?? ""}" step="${step}" ${extra}${aria ? ` aria-label="${aria}"` : ""} style="width:110px">`;
}
function renderInspector() {
  S.dirty.inspector = false;
  const box = $("inspector"), id = S.sel, tl = S.view.timeline;
  if (!id) {
    const skipped = (tl.broll_skipped || []).map((b) => `<li>${b.said ? `„${esc(b.said)}”` : `słowo nr ${esc(String(b.word ?? "?"))}`}: ${esc(b.reason)}</li>`).join("");
    box.innerHTML = '<div class="muted">Kliknij element na osi albo słowo w transkrypcji.</div><div class="row"><button id="newgfx" class="ghost">+ Nowa grafika przy znaczniku</button></div>'
      + (skipped ? `<p class="badge warn">B-roll z planu, którego nie ma na osi (V4):</p><ul class="muted">${skipped}</ul>` : "");
    $("newgfx").onclick = newGfx; return;
  }
  if (id.startsWith("seg:")) return inspectSegment(box, tl.segments[+id.slice(4)]);
  if (id.startsWith("gfx:")) return inspectGfx(box, id.slice(4));
  if (id.startsWith("emph:")) return inspectEmph(box, +id.slice(5));
  if (id.startsWith("beat:")) return inspectBeat(box, +id.slice(5));
  if (id.startsWith("broll:")) return inspectBroll(box, (tl.broll || [])[+id.slice(6)]);
  if (id.startsWith("pace:")) { const b = (tl.beats || [])[+id.slice(5)]; box.innerHTML = b ? `<h3>Rytm na „${esc(b.text)}”</h3><div>Długo bez żadnego zdarzenia i bez rozpoznanego momentu: delikatny najazd tylko dla rytmu. Lepsza decyzja: przytnij ten fragment albo oznacz w nim moment (Ctrl+klik w słowo).</div>` : ""; return; }
  if (id.startsWith("iss:")) { const c = tl.issues.find((x) => `iss:${x.id}` === id); box.innerHTML = c ? `<h3>${esc(c.code)} · ${SEV[c.sev] || esc(c.sev)}</h3><div>${esc(c.msg)}</div>` : ""; return; }
  if (id === "music") { S.sel = null; document.querySelector('[data-tab="t-globals"]').click(); return renderInspector(); }
  box.innerHTML = '<div class="muted">Ten element nie ma ustawień.</div>';
}

function inspectSegment(box, s) {
  if (!s) { box.innerHTML = ""; return; }
  const z = s.zoom, dyn = typeof z === "object";
  const emphPos = S.view.timeline.words.find((w) => w.emph != null && w.src >= s.src_in && w.src <= s.src_out)?.emph;
  box.innerHTML = `<h3>Ujęcie ${s.i + 1} <span class="muted">${fmt(s.start)}–${fmt(s.end)} (${(s.end - s.start).toFixed(1)} s)</span></h3>
  <div class="grid">
    ${field("Rodzaj", { static: "statyczny", zoom: "zoom na cięciu", drift: "drift (powolny)", emph: "najazd" }[s.kind] + (s.override ? " · ręczny" : ""))}
    ${field("Zoom start (a)", num("za", dyn ? z.a : z, 0.005, 'min="1"'))}
    ${field("Zoom koniec (b)", num("zb", dyn ? z.b : z, 0.005, 'min="1"'))}
    ${field("Ruch od / do (s)", `${num("zt0", dyn ? z.t0 : 0, 0.05, 'min="0"')} ${num("zt1", dyn ? z.t1 : +(s.end - s.start).toFixed(2), 0.05, 'min="0"', "ruch do (s)")}`)}
    ${field("Bezpieczny limit", s.zmax != null ? `<b>${s.zmax}</b> <span class="muted">(głowa zostaje w kadrze; więcej się nie da)</span>`
    : `<b>${NO_FACE_LIMIT}</b> <span class="muted">(face_track nie znalazł twarzy w tym ujęciu: nie ma czego chronić, limit ogólny)</span>`)}
  </div>
  <div class="row"><button id="zsave">Zapisz zoom ujęcia</button>${s.override ? '<button id="zauto" class="ghost">Wróć do automatycznego</button>' : ""}
  ${emphPos != null ? `<button id="toemph" class="ghost">Najazd w tym ujęciu</button>` : ""}</div>
  <div id="cropbox"><img id="cropimg" alt="kadr"><canvas id="cropcv"></canvas></div>
  <div class="muted">Zielona ramka: kadr na starcie, przerywana: na końcu ruchu. Czerwona: głowa (z face_track).</div>`;
  const zmax = s.zmax ?? NO_FACE_LIMIT;
  const clampInfo = () => { for (const k of ["za", "zb"]) $(k).style.borderColor = +$(k).value > zmax + 1e-9 ? "#ef4444" : ""; drawCrop(s); };
  ["za", "zb"].forEach((k) => { $(k).max = zmax; $(k).oninput = clampInfo; });
  $("zsave").onclick = () => {
    const a = +$("za").value, b = +$("zb").value;
    if (a > zmax + 1e-9 || b > zmax + 1e-9) return toast(`limit tego ujęcia to ${zmax}`, true);
    const zoom = Math.abs(a - b) < 0.001 ? a : { a, b, t0: +$("zt0").value, t1: +$("zt1").value };
    editOp({ op: "segment_zoom", seg_in: s.src_in, zoom });
  };
  if (s.override) $("zauto").onclick = () => editOp({ op: "segment_zoom", seg_in: s.src_in, zoom: null });
  if (emphPos != null) $("toemph").onclick = () => { S.sel = `emph:${emphPos}`; renderInspector(); };
  const img = $("cropimg");
  img.onload = () => drawCrop(s);
  img.src = `/api/film/${fq()}/frame?t=${((s.src_in + s.src_out) / 2).toFixed(2)}`;
}
function cropRect(s, z) {
  // same framing as face_track.py fits(): horizontal crop 1/z centred on the face; vertical 9:16 with the face at 40 % height
  const vert = S.view.timeline.vertical, [fx, fy] = s.face || [0.5, 0.45];
  const cw = vert ? (1440 * 9 / 16 / 2560) / z : 1 / z, ch = 1 / z, ty = vert ? 0.40 : fy;
  const x0 = Math.min(Math.max(fx - cw / 2, 0), 1 - cw), y0 = Math.min(Math.max(fy - ch * ty, 0), 1 - ch);
  return [x0, y0, cw, ch];
}
function drawCrop(s) {
  const img = $("cropimg"), cv = $("cropcv"); if (!img || !img.naturalWidth) return;
  cv.width = img.clientWidth; cv.height = img.clientHeight;
  const g = cv.getContext("2d"), W = cv.width, H = cv.height;
  const rect = (r, color, dash) => { g.setLineDash(dash ? [6, 4] : []); g.strokeStyle = color; g.lineWidth = 2; g.strokeRect(r[0] * W, r[1] * H, r[2] * W, r[3] * H); };
  const a = +$("za").value || 1, b = +$("zb").value || a;
  rect(cropRect(s, a), "#22c55e");
  if (Math.abs(a - b) > 0.001) rect(cropRect(s, b), "#22c55e", true);
  if (s.head) rect([s.head[0], s.head[1], s.head[2] - s.head[0], s.head[3] - s.head[1]], "#ef4444");
}

function inspectEmph(box, pos) {
  const e = (S.view.plan.emph || [])[pos];
  if (!e) { S.sel = null; return renderInspector(); }
  const w = S.view.timeline.words.find((x) => x.emph === pos);
  box.innerHTML = `<h3>Najazd na „${esc(w ? w.text.trim() : e.find || "?")}”${e.auto ? ' <span class="muted">(z poprawiacza)</span>' : ""}</h3>
  <div class="grid">
    ${field("Siła (amt)", num("eamt", e.amt ?? 0.2, 0.01, 'min="0.02" max="0.38"'))}
    ${field("Czas najazdu (s)", num("espeed", e.speed ?? 0.3, 0.05, 'min="0.1" max="2"'))}
    ${field("W filmie", w && w.out != null ? fmt(w.out) : "–")}
  </div>
  ${w && w.emph_live === false ? `<p class="badge warn">Tego najazdu nie widać w filmie: ujęcie ma bezpieczny limit zoomu ${esc(segOf(w)?.zmax ?? "?")} (głowa wyszłaby z kadru), obok jest ramka grafiki albo słowo jest na samym początku ujęcia. Ruch można ustawić ręcznie w ujęciu: „Zoom start” niżej niż limit.</p>` : ""}
  <div class="row"><button id="esave">Zapisz</button><button id="edel" class="ghost">Usuń najazd</button></div>
  <div class="muted">Najazd nie zadziała pod ramką grafiki (kw/list/slide); build_edl go wtedy pomija. Limit zoomu ujęcia i tak go przytnie.</div>`;
  $("esave").onclick = () => editOp({ op: "update_emph", pos, amt: +$("eamt").value, speed: +$("espeed").value });
  $("edel").onclick = async () => { await editOp({ op: "remove_emph", pos }); S.sel = null; renderInspector(); };
  if (w && w.out != null) setPlayhead(w.out);
}

// what build_edl/beatfx did with a moment (edl beat_fx "fx"), in words
const FX = { punch: "najazd z gramatyki montażu", spacing: "bez najazdu: za blisko silniejszego najazdu",
  quiet: "bez najazdu: moment poboczny, wokół dzieje się dość", none: "ten typ nie dostaje najazdu (np. wyznanie: emocję niesie twarz)",
  box: "pod ramką grafiki: grafika jest efektem", gfx: "efektem jest grafika", cut: "słowo wycięte", off: "wyłączony ręcznie",
  manual: "bez najazdu: zoom tego ujęcia ustawiony ręcznie", unknown: "typu nie ma w gramatyce (style/grammar.json): zmień typ" };
function inspectBeat(box, pos) {
  const b = (S.view.timeline.beats || []).find((x) => x.pos === pos), raw = (S.view.plan.beats || [])[pos];
  if (!b || !raw) { S.sel = null; return renderInspector(); }
  const types = S.view.beat_types || {}, vertical = !!S.view.plan.vertical;
  // a type no longer in the grammar stays selected (and is not sent back) until the editor picks another one
  const opts = (b.type in types ? "" : `<option value="${esc(b.type)}" selected>${esc(b.type)} (spoza gramatyki)</option>`)
    + Object.entries(types).map(([k, v]) => `<option value="${esc(k)}"${k === b.type ? " selected" : ""}>${esc(v)}</option>`).join("");
  box.innerHTML = `<h3>Moment: ${esc(b.label)} „${esc(b.text)}” <span class="muted">(${b.src === "manual" ? "ręczny" : b.src === "llm" ? "lokalne AI" : "reguła"})</span></h3>
  <div class="grid">
    ${field("Typ", `<select id="btype">${opts}</select>`)}
    ${field("Wyłącz", '<input type="checkbox" id="boff"' + (b.off ? " checked" : "") + ">")}
    ${field("Wymuś najazd", '<input type="checkbox" id="bforce"' + (b.force ? " checked" : "") + ">")}
    ${field("W filmie", b.at != null ? fmt(b.at) : "–")}
    ${field("Efekt", esc(FX[b.fx] || "kliknij „Sprawdź”"))}
    ${field("Grafika", esc(b.gfx || "–"))}${vertical ? field("B-roll", esc(b.broll || "–")) : ""}${field("Dźwięk", esc(b.sfx || "–"))}
  </div>
  <div class="row"><button id="bsave">Zapisz</button>${b.gfx === "kw" ? '<button id="bgfx" class="ghost">+ grafika z tego momentu</button>' : ""}${b.src === "manual" ? '<button id="bdel" class="ghost">Usuń moment</button>' : ""}</div>
  <div class="muted">Efekty wynikają z gramatyki montażu (style/grammar.json): ten sam typ momentu = ten sam efekt w każdym filmie. Zmiana typu robi moment ręcznym (ponowne rozpoznanie go nie nadpisze).</div>`;
  $("boff").onchange = () => { if ($("boff").checked) $("bforce").checked = false; };  // off and forced exclude each other
  $("bforce").onchange = () => { if ($("bforce").checked) $("boff").checked = false; };
  $("bsave").onclick = () => editOp({ op: "set_beat", pos, fields: { ...($("btype").value !== b.type ? { type: $("btype").value } : {}), off: $("boff").checked, force: $("bforce").checked } });
  if ($("bdel")) $("bdel").onclick = async (ev) => {  // removal is by position: a second click must not delete the next moment
    const btn = ev.currentTarget; btn.disabled = true;
    try { await editOp({ op: "remove_beat", pos }); } catch { btn.disabled = false; return; }  // error already toasted by api()
    S.sel = null; renderInspector();
  };
  if ($("bgfx")) $("bgfx").onclick = async () => {
    const text = await ask(`Tekst grafiki dla momentu „${b.text}”`, b.text.replace(/[,.?!]+$/, ""));
    if (!text) return;
    const id = `m_${S.film}_${raw.word}`.slice(0, 80);
    await editOp({ op: "add_gfx", spec: { id, type: "kw", text, dur: 3, ...(vertical ? { canvas: "v" } : {}) }, i: raw.word });
    select(`gfx:${id}`);
  };
  if (b.at != null) setPlayhead(b.at);
}
function inspectBroll(box, b) {
  if (!b) { S.sel = null; return renderInspector(); }
  box.innerHTML = `<h3>B-roll na „${esc(b.said || "?")}”</h3>
  <div class="grid">${field("Szukane", esc(b.query))}${field("W filmie", `${fmt(b.start)}–${fmt(b.end)}`)}${field("Autor", esc(b.author || "–"))}${field("Źródło", esc(b.url || "–"))}</div>
  ${b.pos != null ? `<div class="row"><button id="brdel" class="ghost">Usuń b-roll</button></div>
  <div class="muted">Pełny kadr ujęcia stock nad twarzą; głos leci dalej, napisy i grafiki są nad nim. Pasuje źle? Usuń: ten klip i to słowo nie wrócą przy kolejnym doborze (Ctrl+Z cofa usunięcie).</div>`
    : '<p class="badge warn">Tego klipu nie ma już w planie (oś pokazuje ostatni EDL). Kliknij „Sprawdź”, żeby przeliczyć oś.</p>'}`;
  if ($("brdel")) $("brdel").onclick = async (ev) => {  // removal is by position: a second click must not delete the next clip
    const btn = ev.currentTarget; btn.disabled = true;
    try { await editOp({ op: "remove_broll", pos: b.pos }); } catch { btn.disabled = false; return; }  // error already toasted by api()
    S.sel = null; renderInspector();
  };
  setPlayhead(b.start);
}
async function addBeat(i) {
  const types = S.view.beat_types || {}, w = S.view.timeline.words[i];
  const opts = Object.entries(types).filter(([k]) => k !== "hook").map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
  let type = null;
  const first = !("beats" in S.view.plan);
  const warn = first ? '<p class="muted">Pierwszy moment włącza gramatykę montażu dla całego filmu: najazdy tylko z momentów, losowe auto-najazdy znikną (Ctrl+Z cofa).</p>' : "";
  const ok = await modal(`<h3 id="modaltitle">Moment na „${esc(w.text.trim())}”</h3><label for="nbtype">Typ</label> <select id="nbtype">${opts}</select>${warn}`,
    [["Dodaj", true], ["Anuluj", false, "ghost"]], "nbtype").then((r) => { type = $("nbtype").value; return r; });  // read before a queued dialog replaces it
  if (!ok) return;
  await editOp({ op: "add_beat", i, type });
  if (first && "beats" in S.view.plan) toast("Gramatyka montażu włączona dla tego filmu: najazdy tylko z momentów (Ctrl+Z cofa)");
  const pos = (S.view.plan.beats || []).findIndex((b) => b.word === i);
  if (pos >= 0) select(`beat:${pos}`);
}
function segOf(w) { return S.view.timeline.segments.find((s) => s.src_in <= w.src && w.src <= s.src_out); }
async function icons() { if (!S.icons) S.icons = await api("/api/icons"); return S.icons; }
function inspectGfx(box, gid) {
  const spec = S.view.specs.find((s) => s.id === gid), ref = (S.view.plan.gfx || []).find((g) => g.id === gid);
  const ov = S.view.timeline.overlays.find((o) => o.id === gid);
  if (!spec) { box.innerHTML = `<div class="muted">Brak opisu grafiki ${esc(gid)} w gfx_specs.</div>`; return; }
  const w = S.view.timeline.words.find((x) => x.gfx.includes(gid));
  const types = ["kw", "list", "lower", "chap", "cta", "slide", "end", "web"];
  box.innerHTML = `<h3>Grafika ${esc(gid)} <span class="muted">${ov ? fmt(ov.start) + "–" + fmt(ov.end) : "poza filmem"}</span></h3>
  <div class="grid">
    ${field("Typ", `<select id="gtype">${types.map((t) => `<option${t === spec.type ? " selected" : ""}>${t}</option>`).join("")}</select>`)}
    ${field("Tekst", `<input id="gtext" style="width:100%" value="${esc(spec.text)}">`)}
    ${field("Podtytuł", `<input id="gsub" style="width:100%" value="${esc(spec.sub)}">`)}
    ${spec.type === "list" ? field("Punkty (1 na linię)", `<textarea id="gitems">${esc((spec.items || []).join("\n"))}</textarea>`) : ""}
    <div id="gweb" style="display:${spec.type === "web" ? "contents" : "none"}">
      ${field("Adres strony (https://)", `<input id="gurl" style="width:100%" value="${esc(spec.url)}">`)}
      ${field("Zdanie ze strony (zostanie zaznaczone)", `<textarea id="gfind" style="min-height:60px">${esc(spec.find)}</textarea>`)}
      ${field("Podpis źródła (puste = domena i tytuł)", `<input id="gsrc" style="width:100%" value="${esc(spec.src)}">`)}
      ${field("Albo własny zrzut (plik w katalogu projektu, gdy strona blokuje roboty)", `<input id="gimg" style="width:100%" placeholder="zrzut.png" value="${esc(spec.img)}">`)}
    </div>
    ${spec.type === "slide" ? field("HTML slajdu", `<textarea id="ghtml" style="min-height:90px">${esc(spec.html)}</textarea>`) : ""}
    ${field("Długość (s)", num("gdur", spec.dur, 0.1, 'min="0.5" max="60"'))}
    ${field("Start na słowie", w ? `„${esc(w.text.trim())}” <span class="muted">(Shift+klik w tekście = przenieś)</span>` : '<span class="muted">nie znaleziono w tekście (Shift+klik słowa)</span>')}
    ${field("Przesunięcie (s)", num("glead", ref ? ref.lead ?? 0 : 0, 0.05, 'min="-10" max="10"'))}
    ${field("Ikona", `<input id="gicon" placeholder="szukaj ikony…" value="${esc(spec.icon || "")}" style="width:180px"> <img id="gicoprev" style="width:24px;height:24px;background:#f2f2f2;border-radius:4px;vertical-align:middle"><div class="icons" id="giconlist"></div>`)}
  </div>
  <div class="row"><button id="gsave">Zapisz grafikę</button><button id="gdup" class="ghost">Duplikuj</button><button id="gdel" class="ghost">Usuń z filmu</button><button id="newgfx" class="ghost">+ Nowa grafika</button></div>
  <div class="muted">Po zmianie treści grafika renderuje się od nowa przy podglądzie albo renderze.</div>`;
  const prev = () => { const n = $("gicon").value.trim(); $("gicoprev").src = n ? `/icons/${encodeURIComponent(n)}.svg` : ""; };
  const list = async () => {
    const q = $("gicon").value.trim().toLowerCase(), all = await icons();
    const hits = all.filter((n) => n.includes(q)).slice(0, 120);
    $("giconlist").innerHTML = hits.map((n) => `<button class="${n === q ? "on" : ""}" data-n="${n}" title="${n}"><img src="/icons/${n}.svg" alt="${n}"></button>`).join("");
  };
  $("gicon").oninput = () => { prev(); list(); };
  $("giconlist").onclick = (e) => { const b = e.target.closest("button"); if (b) { $("gicon").value = b.dataset.n; prev(); list(); } };
  prev(); list();
  $("gtype").onchange = () => { $("gweb").style.display = $("gtype").value === "web" ? "contents" : "none"; };
  $("gsave").onclick = async () => {
    const fields = { type: $("gtype").value, text: $("gtext").value, sub: $("gsub").value, dur: +$("gdur").value, icon: $("gicon").value.trim() || undefined };
    if (!fields.sub && spec.sub == null) delete fields.sub;  // an empty field must not count as a change (the graphic would render again)
    if ($("gitems")) fields.items = $("gitems").value.split("\n").map((x) => x.trim()).filter(Boolean);
    if ($("ghtml")) fields.html = $("ghtml").value;
    if (fields.type === "web") {
      // only a real change is sent: a re-trimmed or emptied copy of an untouched field would change the capture (key) of the card
      for (const [k, el] of [["url", "gurl"], ["find", "gfind"], ["img", "gimg"]]) { const v = $(el).value.trim(); if (v !== String(spec[k] ?? "").trim()) fields[k] = v; }
      if ($("gsrc").value.trim() || spec.src != null) fields.src = $("gsrc").value.trim();
    }
    if (!fields.icon) delete fields.icon;
    const lead = +$("glead").value;  // read now: the first save redraws the inspector
    await editOp({ op: "update_spec", id: gid, fields });
    if (ref && (ref.lead ?? 0) !== lead) await editOp({ op: "set_gfx_lead", id: gid, lead });
  };
  $("gdup").onclick = async () => {
    const nid = await ask("Identyfikator kopii", `${gid}_b`); if (!nid) return;
    const at = nearestKept(S.playhead); if (!at) return toast("ustaw znacznik na osi", true);
    await editOp({ op: "duplicate_gfx", id: gid, new_id: nid, i: at.i }); select(`gfx:${nid}`);
  };
  $("gdel").onclick = async () => {
    if (!(await modal(`<h3 id="modaltitle">Usunąć ${esc(gid)} z filmu?</h3><div class="muted">Opis grafiki zostaje, jeśli używa jej inny film. Cofniesz przez Ctrl+Z.</div>`, [["Usuń", true, "danger"], ["Anuluj", false, "ghost"]]))) return;
    await editOp({ op: "remove_gfx", id: gid }); S.sel = null; renderInspector();
  };
  $("newgfx").onclick = newGfx;
}
async function newGfx() {
  const at = nearestKept(S.playhead);
  if (!at) return toast("ustaw znacznik na osi w miejscu nowej grafiki", true);
  const id = await ask(`Nowa grafika przy słowie „${at.text.trim()}” — identyfikator (np. g30_nowa)`, "g_nowa");
  if (!id) return;
  await editOp({ op: "add_gfx", spec: { id, type: "kw", text: "Nowa grafika", dur: 4 }, i: at.i });
  select(`gfx:${id}`);
}

// ---------- critic ----------
const SEV = { error: "błąd", warn: "uwaga", info: "info" };  // severity in words too, not only the colour of the edge
function renderCritic() {
  const rep = S.view.critic, box = $("critic");
  if (!rep) { box.innerHTML = '<span class="muted">Kliknij „Sprawdź”, żeby uruchomić krytyka.</span>'; $("critcount").textContent = ""; return; }
  const iss = S.view.timeline.issues || [];
  $("critcount").textContent = iss.length ? `(${iss.length})` : "✓";
  const acc = rep.accepted || [];
  box.innerHTML = (iss.length ? "" : '<div>Brak problemów. ✓</div>') + iss.map((c) => `<div class="issue ${c.sev}" data-id="${c.id}" tabindex="0" role="button">
    <span class="code">${esc(c.code)}</span><span class="sev">${SEV[c.sev] || esc(c.sev)}</span>${esc(c.msg)} <span class="muted">${c.t != null ? "· " + fmt(c.t) : ""}</span>
    <button class="ghost acc" data-id="${c.id}">Akceptuję</button></div>`).join("") +
    (acc.length ? `<div class="muted" style="margin-top:10px">Zaakceptowane: ${acc.map((a) => esc(a.code + " " + a.msg)).join("; ")}</div>` : "");
}
$("critic").addEventListener("click", async (e) => {
  const b = e.target.closest(".acc"), row = e.target.closest(".issue");
  if (b) {
    const c = S.view.timeline.issues.find((x) => x.id === +b.dataset.id);
    const reason = await ask(`Dlaczego akceptujesz: ${c.code} ${c.msg}?`, "celowo");
    if (reason) await editOp({ op: "accept", code: c.code, msg: c.msg, reason });
    return;
  }
  if (row) select(`iss:${row.dataset.id}`);
});
$("critic").addEventListener("keydown", (e) => {
  const row = e.target.closest(".issue");
  if (row && e.target === row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(`iss:${row.dataset.id}`); }
});
$("check").onclick = async () => {
  const r = await startJob("check", `/api/film/${fq()}/check`);
  $("log").textContent = ""; tab("t-critic");
  watchJob(r.job, async () => { await loadFilm(); tab("t-critic"); });
};
$("fix").onclick = async () => {
  const r = await startJob("fix", `/api/film/${fq()}/fix-preview`);
  $("log").textContent = "";
  watchJob(r.job, async (j) => {
    if (j.status !== "done") return;
    const diff = j.result.diff || [];
    if (!diff.length) return toast("Poprawiacz nie ma propozycji");
    const html = `<h3 id="modaltitle">Proponowane poprawki krytyka</h3><div class="muted">${esc((j.result.report.fixes || []).join(" · "))}</div><pre>${diff
      .map((l) => `<span class="${l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}">${esc(l)}</span>`).join("\n")}</pre>`;
    if (await modal(html, [["Zapisz poprawki", true], ["Odrzuć", false, "ghost"]])) {
      const s = await post(`/api/film/${fq()}/fix-apply?job=${j.id}`);
      await loadFilm(); watchJob(s.job, () => loadFilm()); toast("Poprawki zapisane (Ctrl+Z cofa)");
    }
  });
};

$("beats").onclick = async () => {
  const r = await startJob("beats", `/api/film/${fq()}/beats`);
  $("log").textContent = "";
  watchJob(r.job, async (j) => {
    if (j.status !== "done") return;
    const m = j.result.summary;
    const html = `<h3 id="modaltitle">Momenty montażu</h3><div>Rozpoznane z tekstu: <b>${m.beats}</b> (nowe: ${m.new}).
      Efekty dobierze gramatyka montażu; losowe najazdy z poprawiacza znikną, ręczne zostają.</div>
      ${m.beats ? "" : '<p class="badge warn">Nie rozpoznano żadnego momentu: po zastosowaniu film zostanie tylko z „rytmem” i ręcznymi najazdami. Zwykle lepiej odrzucić.</p>'}
      ${m.gfx.length ? `<div>Nowe grafiki z momentów: ${m.gfx.map(esc).join(" · ")}</div>` : ""}
      <div class="muted">Ręczne decyzje (zmieniony typ, wyłączone, wymuszone) zostają. Ctrl+Z cofa.</div>`;
    if (await modal(html, [["Zastosuj", true], ["Odrzuć", false, "ghost"]])) {
      const s = await post(`/api/film/${fq()}/beats-apply?job=${j.id}`);
      await loadFilm(); watchJob(s.job, () => loadFilm()); toast("Momenty zapisane (Ctrl+Z cofa)");
    }
  });
};

$("broll").onclick = async () => {
  if (!S.view.plan.vertical) return toast("B-roll stock jest włączony tylko dla shortów (style/grammar.json › broll)", true);
  const r = await startJob("broll", `/api/film/${fq()}/broll`);
  $("log").textContent = "";
  watchJob(r.job, async (j) => {
    if (j.status !== "done") return;
    const m = j.result.summary;
    if (m.format !== "short") return toast("B-roll stock jest włączony tylko dla shortów (style/grammar.json › broll)");
    const items = m.items.map((b) => `<li>${b.new ? "<b>nowy</b> " : ""}„${esc(b.said)}” → ${esc(b.query)}${b.author ? ` <span class="muted">(${esc(b.author)})</span>` : ""}</li>`).join("");
    const skipped = m.skipped.filter((s) => !s.down).map((s) => `<li>„${esc(s.said)}”: ${esc(s.reason)}</li>`).join("");
    const html = `<h3 id="modaltitle">B-roll</h3><div>Ujęć w filmie: <b>${m.broll}</b> (nowe: ${m.new}).</div>
      ${m.error ? `<p class="badge warn">${esc(m.error)}. Dobór przerwany: popraw i uruchom jeszcze raz.</p>` : ""}
      ${items ? `<ul>${items}</ul>` : m.error ? "" : '<p class="muted">Brak miejsc na b-roll: w tekście nie pada nic konkretnego z listy tematów albo miejsca zajmują grafiki i ważne momenty.</p>'}
      ${skipped ? `<div class="muted">Pominięte:</div><ul class="muted">${skipped}</ul>` : ""}
      ${m.credits.length ? `<div class="muted">Wklej do opisu filmu (wytyczne API Pexels proszą o link do Pexels i autorów):</div><pre>${esc(m.credits.join("\n"))}</pre>` : ""}
      <div class="muted">Każde ujęcie możesz potem zaznaczyć na osi (V4) i usunąć. Ctrl+Z cofa.</div>`;
    if (!m.new) return modal(html, [["OK", false]]);
    if (await modal(html, [["Zastosuj", true], ["Odrzuć", false, "ghost"]])) {
      const s = await post(`/api/film/${fq()}/broll-apply?job=${j.id}`);
      await loadFilm(); watchJob(s.job, () => loadFilm()); toast("B-roll zapisany (Ctrl+Z cofa)");
    }
  });
};

// ---------- preview / render ----------
$("preview").onclick = async () => {
  const r = await startJob("preview", `/api/film/${fq()}/preview?t=${S.playhead.toFixed(2)}`);
  tab("t-preview"); $("previewinfo").textContent = `Renderuję fragment wokół ${fmt(S.playhead)}…`;
  watchJob(r.job, (j) => {
    if (j.status !== "done") { $("previewinfo").textContent = `Podgląd: ${statusPl(j.status)} ${j.error || ""} (szczegóły w zakładce Log)`; return; }
    $("video").muted = true; $("video").src = `/api/jobs/${j.id}/video?ts=${Date.now()}`; $("video").play().catch(() => {});
    $("previewinfo").textContent = `Fragment ${fmt(j.result.from)}–${fmt(j.result.to)} · przygotowany w ${j.seconds} s`;
    loadFilm();
  });
};
$("render").onclick = async () => {
  const p = await api("/api/project"); showBattery(p.battery);
  const b = p.battery, warn = b && b.warn ? `<p class="badge warn">Bateria ${b.percent}% i bez ładowania. Render może trwać ponad 20 minut: podłącz zasilacz.</p>` : "";
  const ok = await modal(`<h3 id="modaltitle">Pełny render „${esc(S.film)}”?</h3>${warn}<div class="muted">run_v4.sh: grafiki → EDL → twarze → krytyk z poprawkami → muzyka → render → krytyk wideo. Wynik w ${esc(S.out)}. W trakcie renderu panel nie zapisuje zmian.</div>`,
    [["Renderuj", true, "danger"], ["Anuluj", false, "ghost"]]);
  if (!ok) return;
  const r = await startJob("render", `/api/film/${fq()}/render`);
  $("log").textContent = ""; tab("t-log");
  watchJob(r.job, (j) => { toast(`Render: ${statusPl(j.status)}`, j.status !== "done"); loadFilm(); });
};
$("stop").onclick = async () => {
  const ok = await modal(`<h3 id="modaltitle">Zatrzymać wszystko?</h3><div>Przerywa zadania panelu (render, podgląd, przeliczanie).
    Przy renderze uruchamia też <code>stop_render.py</code>, który zatrzymuje <b>każdy</b> render pipeline'u na tym laptopie, także spoza panelu.
    Przerwanego renderu nie da się wznowić: zacznie się od nowa.</div>`, [["Zatrzymaj", true, "danger"], ["Anuluj", false, "ghost"]]);
  if (!ok) return;
  const r = await post("/api/stop"); toast(`Zatrzymano: ${r.stopped.length} zadań. ${r.processes}`);
};

// ---------- globals ----------
function renderGlobals() {
  S.dirty.globals = false;
  const p = S.view.plan, f = $("globals");
  f.innerHTML = `
    ${field("Drift (powolny zoom)", num("gl_drift", p.drift ?? 0.05, 0.005, 'min="0" max="0.2"'))}
    ${field("Zoomy na cięciach", `<input id="gl_zooms" value="${esc((p.zooms || [1, 1.15]).join(", "))}" style="width:160px"> <span class="muted">np. 1.0, 1.12</span>`)}
    ${field("Ekran końcowy (s)", num("gl_tail", p.tail ?? 0, 0.5, 'min="0"'))}
    ${field("Pasek postępu", `<input type="checkbox" id="gl_progress"${p.progress ? " checked" : ""}>`)}
    ${field("Hak (tekst na starcie shorta)", `<input id="gl_hook" value="${esc(p.hook_text || "")}" style="width:100%">`)}
    ${field("Muzyka (dB)", num("gl_music", p.music_db ?? -13, 0.5, 'max="0" min="-40"'))}
    ${field("SFX (dB)", num("gl_sfx", p.sfx_db ?? -12, 0.5, 'max="0" min="-40"'))}
    ${field("Seed muzyki", num("gl_seed", p.music_seed ?? 7, 1, 'min="0"'))}
    <div></div><div><button type="submit">Zapisz ustawienia</button></div>`;
}
$("globals").onsubmit = (e) => {
  e.preventDefault();
  const values = { drift: +$("gl_drift").value, zooms: $("gl_zooms").value.split(",").map((x) => x.trim()).filter((x) => x !== "").map(Number), tail: +$("gl_tail").value,
    progress: $("gl_progress").checked, music_db: +$("gl_music").value, sfx_db: +$("gl_sfx").value, music_seed: +$("gl_seed").value };
  const hook = $("gl_hook").value.trim(); if (hook || S.view.plan.hook_text) values.hook_text = hook;
  editOp({ op: "globals", values });
};

// ---------- tabs, film switch ----------
function tab(id) {
  document.querySelectorAll(".tabs button").forEach((b) => { b.classList.toggle("on", b.dataset.tab === id); b.setAttribute("aria-selected", b.dataset.tab === id); });
  document.querySelectorAll("#lefttabs .pane").forEach((p) => p.classList.toggle("on", p.id === id));
}
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => tab(b.dataset.tab)));
// preview plays by itself, so EVERY preview starts muted (sound only after the user unmutes that one)
$("video").muted = true;
$("film").onchange = async () => { S.film = $("film").value; S.sel = null; S.playhead = 0; localStorage.setItem("mz.film", S.film); await loadFilm(true); };
window.addEventListener("resize", () => drawWave());
setInterval(async () => { try { showBattery((await api("/api/project")).battery); } catch { /* shown by api() */ } }, 60000);
loadProject().catch((e) => toast(`Nie mogę wczytać projektu: ${e.message}`, true));

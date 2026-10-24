"use strict";
// Live player (ADR-0007): plays the EDL in the browser, in real time, from any point, at low quality.
// Two <video> elements take turns (the next shot is already cued while the current one plays), the clock is the
// playing video's own time mapped to OUTPUT time, zoom/crop = CSS transform (LiveCore.cropRect = assemble.py's
// formulas), cards / b-roll / face cam / captions / progress bar are layers synced to that clock.
// The page owns the API helpers and the timeline: they come in through Live.init(hooks).
const Live = (() => {
  const C = window.LiveCore;
  const $ = (id) => document.getElementById(id);
  const FRAME = 1 / 30, PRELOAD = 2.0, JUMP = 5, PREROLL = 0.4;
  const L = { film: null, m: null, t: 0, playing: false, muted: true, active: 0, seg: 0, mode: "video", clock0: 0, now0: 0,
    seq: 0, job: null, watching: null, err: null, autoFor: null, recheck: 0, subKey: "", lastTick: 0, pct: null, theatre: false };
  const cards = new Map();
  let H = null, V = [], broll = null, music = null, sfx = null, pipCtx = null;

  const segs = () => (L.m ? L.m.segments : []);
  const total = () => (L.m ? L.m.total : 0);
  const srcUrl = (key) => L.m && L.m.sources[key] && L.m.sources[key].url;
  const ready = () => !!L.m && L.m.segments.length > 0 && Object.values(L.m.sources).every((s) => s.ready);
  const bedsOn = () => { try { return localStorage.getItem("mz.liveBeds") !== "0"; } catch { return true; } };
  const fq = () => encodeURIComponent(L.film);

  function init(hooks) {
    H = hooks;
    V = [$("lva"), $("lvb")];
    broll = $("lvbroll");
    music = new Audio(); sfx = new Audio();
    for (const a of [music, sfx]) { a.preload = "auto"; a.muted = true; }
    pipCtx = $("livepipcv").getContext("2d");
    for (const v of [...V, broll]) { v.muted = true; v.playsInline = true; v.preload = "auto"; }
    for (const v of V) v.addEventListener("error", () => {  // a missing or broken recording copy: say so, never a silent black frame
      if (!v._url || !L.m) return;
      pause(); L.err = `kopia nagrania nie gra w przeglądarce (${v.error ? v.error.message || `kod ${v.error.code}` : "?"})`;
      H.toast(`Odtwarzacz: ${L.err}`, true); status();
    });
    $("lplay").onclick = () => toggle();
    $("lmute").onclick = () => setMuted(!L.muted);
    $("lbeds").checked = bedsOn();
    $("lbeds").onchange = () => {
      try { localStorage.setItem("mz.liveBeds", $("lbeds").checked ? "1" : "0"); } catch { /* per-viewer convenience only */ }
      syncBeds(true); if ($("lbeds").checked) autoPrepare(true);
    };
    $("ltheatre").onclick = () => theatre(!L.theatre);
    $("lfull").onclick = fullscreen;
    $("livemsg").addEventListener("click", (e) => { const b = e.target.closest("button[data-act]"); if (b) act(b.dataset.act); });
    $("lstatus").addEventListener("click", (e) => { const b = e.target.closest("button[data-act]"); if (b) act(b.dataset.act); });
    const scrub = $("lscrub");
    let drag = false;
    const at = (e) => { const r = scrub.getBoundingClientRect(); return C.clip((e.clientX - r.left) / r.width, 0, 1) * total(); };
    scrub.addEventListener("pointerdown", (e) => { if (!L.m) return; drag = true; scrub.setPointerCapture(e.pointerId); seek(at(e)); });
    scrub.addEventListener("pointermove", (e) => { if (drag) seek(at(e)); });
    scrub.addEventListener("pointerup", () => { drag = false; });
    new ResizeObserver(layout).observe($("livestage"));
    document.addEventListener("fullscreenchange", () => { $("lfull").textContent = document.fullscreenElement ? "Zamknij pełny ekran" : "Pełny ekran"; layout(); });
    document.addEventListener("keydown", keys);
    requestAnimationFrame(tick);
  }

  // ---------- model ----------
  async function load(film, ready_) {
    const fresh = film !== L.film;
    if (fresh) {  // every film starts muted and at 0 (the page never remembers an unmute: CLAUDE.md)
      pause(); L.film = film; L.m = null; L.t = 0; L.err = null; L.autoFor = null; L.watching = null; setMuted(true);
      clearLayers(); for (const v of V) { v.removeAttribute("src"); v._url = null; v.load(); }
    }
    if (!ready_) { L.m = null; msg("Brak EDL tego filmu: kliknij „Sprawdź”, żeby go zbudować."); drawScrub(); return; }
    const seq = ++L.seq;
    let m;
    try { m = await H.api(`/api/film/${fq()}/live`); } catch (e) { if (seq === L.seq) msg(`Nie mogę wczytać odtwarzacza: ${H.esc(e.message)}`); return; }
    if (seq !== L.seq || film !== L.film) return;  // a newer load (another edit, another film) already won
    apply(m);
  }
  function apply(m) {
    const old = L.m, changed = !old || old.version !== m.version;
    const srcChanged = !old || JSON.stringify(old.sources) !== JSON.stringify(m.sources);
    L.m = m;
    if (changed || srcChanged || JSON.stringify(old.overlays) !== JSON.stringify(m.overlays)) clearCards();
    $("liveframe").dataset.vertical = m.vertical ? "1" : "0";
    $("liveframe").classList.toggle("graded", !!m.grade);
    layout(); buildSubStyles(); syncBeds(true);
    if (changed || srcChanged) seek(Math.min(L.t, m.total), L.playing);  // the cut under the clock may have moved
    if (m.job) watch(m.job);
    status(); drawScrub();
    autoPrepare(false);
    // a graphics render is writing cards (or one was written seconds ago): look again soon instead of waiting for an edit
    const waiting = m.gfx_busy || m.overlays.some((o) => o.state === "busy");
    clearTimeout(L.recheck);
    if (waiting && !m.job) { const film = L.film; L.recheck = setTimeout(() => { if (L.film === film) load(film, true); }, 5000); }
  }
  // cheap, small work starts by itself (cards after an edit, beds); the one-time recording copy waits for a click / Play
  function autoPrepare(force) {
    if (L.watching) return;
    // once per (EDL version, missing cards, beds): a card that always fails must not loop, but newly rendered cards
    // (same EDL, other files) and cards skipped while graphics were rendering get their own run
    const key = C.prepareKey(L.m, $("lbeds").checked);
    if (key === null || (!force && L.autoFor === key)) return;
    L.autoFor = key; prepare();
  }
  async function prepare() {
    try {
      const r = await H.post(`/api/film/${fq()}/live/prepare?beds=${$("lbeds").checked ? 1 : 0}`);
      L.err = null; watch(r.job);
    } catch { /* toasted by the page's api() */ }
  }
  function watch(id) {
    if (L.watching === id) return;
    L.watching = id; L.pct = null;
    let since = 0, fails = 0;
    const film = L.film;
    const step = async () => {
      if (L.watching !== id || L.film !== film) return;
      let j;
      try { j = await H.api(`/api/jobs/${id}?since=${since}`); fails = 0; } catch {
        // the panel restarted (its job table is empty) or is gone: stop following this id like app.js watchJob does
        if (++fails < C.WATCH_FAILS) { setTimeout(step, 3000); return; }
        L.watching = null; L.job = null; L.err = "utracono zadanie przygotowania (panel uruchomiony ponownie?)"; status(); return;
      }
      since = j.n;
      for (const ln of j.lines) {
        const p = /^postęp (\d+)%/.exec(ln); if (p) L.pct = +p[1];
        if (ln.startsWith("BŁĄD")) L.err = ln;
      }
      if (j.status === "queued" || j.status === "running") { L.job = j; status(); setTimeout(step, 1000); return; }
      L.watching = null; L.job = null;
      if (j.status === "failed") {
        L.err = L.err || `przygotowanie odtwarzacza: ${j.error}`;
        H.toast(`Odtwarzacz: ${L.err} (log zadania: zakładka Log / render/panel/jobs)`, true);
        H.log(j.id);
      } else if (j.status === "stopped") L.err = "przygotowanie odtwarzacza zatrzymane";
      load(L.film, true);
    };
    step();
  }
  async function act(a) {
    if (a === "prepare") { L.autoFor = null; return prepare(); }
    if (a === "gfx") {
      try { const r = await H.post(`/api/film/${fq()}/gfx`); H.toast("Renderuję brakujące grafiki…"); H.watchJob(r.job, () => H.reload()); } catch { /* toasted */ }
    }
  }

  // ---------- messages ----------
  function msg(html) { const el = $("livemsg"); el.innerHTML = html || ""; el.hidden = !html; }
  function status() {
    const m = L.m;
    if (!m) { $("lstatus").textContent = ""; return; }
    const fmt = H.fmt, parts = [];
    const running = L.job && (L.job.status === "running" || L.job.status === "queued");
    if (!ready()) {
      const bad = Object.values(m.sources).find((s) => s.error);
      if (bad) msg(`<p>${H.esc(bad.error)}</p>`);
      else if (running) msg(`<p>Przygotowuję lekką kopię nagrania${L.pct != null ? `: <b>${L.pct}%</b>` : "…"}</p><p class="muted">Jednorazowo dla tego nagrania (720p, ok. 1–2 min na 20 min materiału). Edycje działają w tym czasie normalnie.</p>`);
      else msg(`<p>Odtwarzacz potrzebuje jednorazowo lekkiej kopii nagrania (720p, w <code>render/panel/cache</code>).</p>
        ${L.err ? `<p class="err">${H.esc(L.err)}</p>` : ""}<button data-act="prepare">Przygotuj odtwarzacz</button>`);
    } else msg("");
    const cardsTodo = m.overlays.filter((o) => o.state === "todo" || o.state === "busy").length;
    const noRender = m.overlays.filter((o) => o.state === "no_render").length;
    if (running && ready()) parts.push(cardsTodo ? `przygotowuję karty (${cardsTodo})…` : "przygotowuję muzykę i SFX…");
    if (noRender) parts.push(`${noRender} ${noRender === 1 ? "grafika nie jest" : "grafik nie jest"} wyrenderowana <button class="ghost mini" data-act="gfx">Renderuj grafiki</button>`);
    if ($("lbeds").checked && m.beds && !m.beds.fresh) parts.push("muzyka i SFX z poprzedniej wersji");
    if (ready() && L.err && !running) parts.push(`<span class="err">${H.esc(L.err)}</span> <button class="ghost mini" data-act="prepare">Ponów</button>`);
    $("lstatus").innerHTML = parts.join(" · ");
    $("ltime").textContent = `${fmt(L.t)} / ${fmt(m.total)}`;
  }

  // ---------- layout ----------
  function layout() {
    const st = $("livestage"), fr = $("liveframe"), m = L.m;
    const aw = st.clientWidth, ah = st.clientHeight;
    if (!aw || !ah) return;
    const ar = m ? m.w / m.h : 16 / 9;
    const w = Math.min(aw, ah * ar), h = w / ar;
    fr.style.width = `${Math.floor(w)}px`; fr.style.height = `${Math.floor(h)}px`;
    if (m && m.subs) fr.style.setProperty("--ass", `${h / m.subs.res[1]}px`);
    const cv = $("livepipcv"), dpr = window.devicePixelRatio || 1;
    if (m && m.pip) {
      const [x, y, bw, bh] = m.pip.box, [fx, fy, fw, fh] = m.pip.frame_box;
      Object.assign($("livepip").style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${bw * 100}%`, height: `${bh * 100}%` });
      Object.assign($("livepipframe").style, { left: `${fx * 100}%`, top: `${fy * 100}%`, width: `${fw * 100}%`, height: `${fh * 100}%` });
      $("livepipframe").src = m.pip.frame || "";
      $("livepip").style.borderRadius = `${m.pip.radius * 100}%`;
      cv.width = Math.max(1, Math.round(bw * w * dpr)); cv.height = Math.max(1, Math.round(bh * h * dpr));
    }
    if (m && m.progress) $("liveprog").style.background = m.progress.color;
    $("liveprog").style.height = m ? `${(8 / m.h) * 100}%` : "0";
    drawScrub();
  }
  function theatre(on) {
    L.theatre = on; document.body.classList.toggle("theatre", on);
    $("ltheatre").textContent = on ? "Montaż" : "Duży podgląd";
    $("ltheatre").title = on ? "Wróć do układu montażu (T)" : "Odtwarzacz na większość ekranu (T)";
    if (H.onLayout) H.onLayout(on);
    layout();
  }
  function fullscreen() {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else $("live").requestFullscreen().catch((e) => H.toast(`Pełny ekran niedostępny: ${e.message}`, true));
  }

  // ---------- transport ----------
  function toggle() { if (L.playing) pause(); else play(); }
  function play() {
    if (!L.m) return;
    if (!ready()) { if (!L.watching) prepare(); return; }
    if (L.t >= total() - 0.01) seek(0);
    L.playing = true;
    if (L.mode === "video") { const v = V[L.active]; v.muted = L.muted; v.play().catch(stalled); }
    else { L.clock0 = L.t; L.now0 = performance.now(); }
    syncBeds(true); button();
  }
  function stalled(e) { if (e && e.name !== "AbortError") H.toast(`Odtwarzacz: ${e.message}`, true); }
  function pause() {
    L.playing = false;
    for (const v of [...V, broll]) if (!v.paused) v.pause();
    for (const el of cards.values()) if (el.tagName === "VIDEO" && !el.paused) el.pause();
    for (const a of [music, sfx]) if (a && !a.paused) a.pause();
    button();
  }
  function button() { $("lplay").textContent = L.playing ? "❚❚ Pauza" : "▶ Odtwórz"; }
  function setMuted(on) {
    L.muted = on;
    V.forEach((v, k) => { v.muted = on || k !== L.active; });
    for (const a of [music, sfx]) if (a) a.muted = on;
    $("lmute").textContent = on ? "Dźwięk: wył." : "Dźwięk: wł.";
    $("lmute").setAttribute("aria-pressed", String(!on));
  }
  // seek to output second t; playing goes on from there
  function seek(t, keep = L.playing, notify = true) {
    if (!L.m) return;
    t = C.clip(t, 0, total());
    L.t = t;
    if (notify && H.onTime) H.onTime(t);  // the timeline's playhead follows a seek made in the player (scrub, keys)
    const loc = C.locate(segs(), t);
    if (!loc || !ready()) { render(t); return; }
    if (loc.tail) {
      cue(L.active, loc.i, segs()[loc.i].out - FRAME); V[L.active].pause();
      L.seg = loc.i; L.mode = "clock"; L.clock0 = t; L.now0 = performance.now();
    } else {
      L.mode = "video"; L.seg = loc.i;
      cue(L.active, loc.i, loc.src);
      cue(1 - L.active, loc.i + 1);
      const v = V[L.active];
      if (keep) { v.muted = L.muted; v.play().catch(stalled); } else v.pause();
    }
    showActive(); syncBeds(true);
    L.playing = keep; button(); render(t);
  }
  // element e shows segment i from source second `at`; without `at` it is the standby: parked PREROLL s before the
  // in-point, so it can start playing (hidden, muted) before the cut and be running when the cut comes
  function cue(e, i, at) {
    const v = V[e], s = segs()[i];
    v._seg = i; v.playbackRate = 1;
    if (!s) return;
    const url = srcUrl(s.src);
    if (!url) return;
    if (v._url !== url) { v.src = url; v._url = url; }
    const to = at ?? Math.max(0, s.in - PREROLL);
    if (at == null && !v.paused) v.pause();
    if (Math.abs(v.currentTime - to) > 0.002) v.currentTime = to;
    v.style.transform = C.cssCrop(C.cropRect(s, to - s.in, L.m));
  }
  function showActive() {
    V[L.active].classList.add("on"); V[1 - L.active].classList.remove("on");
    V.forEach((v, k) => { v.muted = L.muted || k !== L.active; });
  }
  function advance() {
    const next = L.seg + 1, cur = V[L.active];
    if (next >= segs().length) {  // end screen: last frame held (= assemble tpad) on the clock
      cur.pause(); L.mode = "clock"; L.clock0 = segs()[L.seg].end; L.now0 = performance.now(); return;
    }
    const sb = 1 - L.active;
    if (V[sb]._seg !== next) cue(sb, next, segs()[next].in);
    else if (V[sb].paused || V[sb].currentTime < segs()[next].in - 0.25) V[sb].currentTime = segs()[next].in;  // pre-roll did not start
    L.active = sb; L.seg = next;
    V[sb].playbackRate = 1;
    showActive();
    if (V[sb].paused) V[sb].play().catch(stalled);
    cur.pause();
    cue(1 - sb, next + 1);  // the element just left cues the shot after this one
  }
  // the standby starts PREROLL s before the cut (hidden, muted) and is nudged with playbackRate to be exactly on its
  // in-point when the cut comes: play() takes ~70 ms to get going, which would otherwise freeze every cut
  function preroll(s, ct) {
    const sb = V[1 - L.active], n = segs()[L.seg + 1], rem = s.out - ct;
    if (!n || sb._seg !== L.seg + 1 || rem > PREROLL || sb.seeking) return;
    if (sb.paused) { if (sb.readyState >= 2) sb.play().catch(() => {}); return; }
    const lag = (n.in - (rem - FRAME / 2)) - sb.currentTime;  // > 0: behind (the swap comes half a frame before `out`)
    sb.playbackRate = 1 + C.clip(lag / Math.max(rem, 0.15), -0.5, 0.5);
  }
  function step(frames) { pause(); seek(L.t + frames * FRAME, false); }
  function jump(sec) { seek(L.t + sec); }
  function cut(dir) { seek(C.cutFrom(segs(), L.t, dir, total())); }

  // ---------- clock ----------
  function tick(now) {
    requestAnimationFrame(tick);
    const m = L.m;
    if (!m || !ready()) return;
    let t = L.t;
    if (L.playing) {
      if (L.mode === "video") {
        const s = segs()[L.seg], v = V[L.active];
        if (s) preroll(s, v.currentTime);
        // the copy can end a hair before the EDL's out (last shot, rounding): its end is the cut too
        if (s && (v.currentTime >= s.out - FRAME / 2 || v.ended || (v.duration && v.currentTime >= v.duration - 0.02))) advance();
        const s2 = segs()[L.seg], v2 = V[L.active];
        t = L.mode === "video" ? s2.start + C.clip(v2.currentTime - s2.in, 0, s2.out - s2.in) : L.clock0;
      } else t = L.clock0 + (now - L.now0) / 1000;
      if (t >= m.total) { t = m.total; pause(); }
      L.t = t;
    }
    render(t);
    if (now - L.lastTick > 100) {  // the page's playhead / timeline: 10 Hz is enough
      L.lastTick = now;
      $("ltime").textContent = `${H.fmt(t)} / ${H.fmt(m.total)}`;
      if (L.playing && H.onTime) H.onTime(t);
      drawScrub();
      if (L.playing) syncBeds(false);
    }
  }
  function render(t) {
    const m = L.m;
    if (!m) return;
    const s = segs()[L.seg];
    let rect = null;
    if (s) {
      const local = L.mode === "video" ? C.clip((L.playing ? V[L.active].currentTime : s.in + (t - s.start)) - s.in, 0, s.out - s.in) : s.out - s.in;
      rect = C.cropRect(s, local, m);
      V[L.active].style.transform = C.cssCrop(rect);
    }
    syncCards(t); syncBroll(t); drawPip(t, rect); drawSubs(t);
    const pg = $("liveprog");
    if (m.progress && t < m.body) { pg.hidden = false; pg.style.width = `${Math.min(t / m.body, 1) * 100}%`; } else pg.hidden = true;
  }

  // ---------- layers ----------
  function clearLayers() { clearCards(); broll.removeAttribute("src"); broll._k = null; broll.classList.remove("on"); $("livesubs").innerHTML = ""; L.subKey = ""; }
  function clearCards() { for (const el of cards.values()) el.remove(); cards.clear(); }
  const CARD_STATE = { todo: "czeka na kopię (przygotowuję)", busy: "właśnie się renderuje", no_render: "nie wyrenderowana: „Renderuj grafiki”",
    bad_id: "nazwa grafiki z niedozwolonymi znakami", missing: "brak w EDL" };
  function makeCard(o) {
    let el;
    if (o.url) {
      el = document.createElement("video");
      Object.assign(el, { muted: true, playsInline: true, preload: "auto", src: o.url });
      el.className = "lcard";
      el.style.width = `${o.fw * 100}%`; el.style.height = `${o.fh * 100}%`;
      el.onerror = () => { el._err = true; H.toast(`Karta ${o.id}: przeglądarka nie odtwarza kopii (${el.error ? el.error.message || el.error.code : "?"})`, true); };
    } else {
      el = document.createElement("div");
      el.className = "lcard missing";
      el.textContent = `${o.id}${o.type ? ` · ${o.type}` : ""}: ${o.label} (${CARD_STATE[o.state] || o.state})`;
    }
    el.style.zIndex = String(10 + o.k);  // EDL order: a later card is drawn on top (= assemble overlay chain)
    $("livecards").appendChild(el);
    return el;
  }
  function syncCards(t) {
    const keep = new Set();
    for (const o of L.m.overlays) {
      if (t < o.at - PRELOAD || t >= o.at + o.dur + 0.5) continue;
      keep.add(o.k);
      let el = cards.get(o.k);
      if (!el) { el = makeCard(o); cards.set(o.k, el); }
      const on = t >= o.at && t < o.at + o.dur;
      el.classList.toggle("on", on);
      if (el.tagName !== "VIDEO" || el._err) continue;
      const want = C.clip(t - o.at, 0, o.dur);
      if (on && L.playing) {
        if (el.paused) { el.currentTime = want; el.play().catch(() => {}); } else if (Math.abs(el.currentTime - want) > 0.15) el.currentTime = want;
      } else {
        if (!el.paused) el.pause();
        if (!el.seeking && Math.abs(el.currentTime - want) > 0.02) el.currentTime = want;
      }
    }
    for (const [k, el] of cards) if (!keep.has(k)) { el.remove(); cards.delete(k); }
  }
  function syncBroll(t) {
    const b = L.m.broll.find((x) => x.url && x.at <= t && t < x.at + x.dur);
    if (!b) { if (broll.classList.contains("on")) { broll.classList.remove("on"); broll.pause(); } return; }
    if (broll._k !== b.url) { broll.src = b.url; broll._k = b.url; }  // url carries the clip's version: an edit swaps it
    broll.classList.add("on");
    const want = b.ss + (t - b.at);
    if (L.playing) {
      if (broll.paused) { broll.currentTime = want; broll.play().catch(() => {}); } else if (Math.abs(broll.currentTime - want) > 0.2) broll.currentTime = want;
    } else { if (!broll.paused) broll.pause(); if (!broll.seeking && Math.abs(broll.currentTime - want) > 0.03) broll.currentTime = want; }
  }
  // face cam over slides: a square of the composed frame around the film's face (assemble.py crops the frame after zoom)
  function drawPip(t, rect) {
    const box = $("livepip"), p = L.m.pip;
    const on = !!p && !!rect && C.pipOn(p, t);
    box.classList.toggle("on", on); $("livepipframe").classList.toggle("on", on && !!p.frame);
    if (!on) return;
    const v = V[L.active];
    if (!v.videoWidth) return;
    const [cx, cy, cw, ch] = p.crop;
    const sx = (rect.x + cx * rect.w) * v.videoWidth, sy = (rect.y + cy * rect.h) * v.videoHeight;
    const cv = $("livepipcv");
    try { pipCtx.drawImage(v, sx, sy, cw * rect.w * v.videoWidth, ch * rect.h * v.videoHeight, 0, 0, cv.width, cv.height); } catch { /* frame not decoded yet */ }
  }
  function syncBeds(force) {
    const on = $("lbeds").checked && L.m && L.m.beds;
    for (const [a, key, db] of [[music, "music", "music_db"], [sfx, "sfx", "sfx_db"]]) {
      if (!on) { if (!a.paused) a.pause(); continue; }
      const url = L.m.beds[key];
      if (a._url !== url) { a.src = url; a._url = url; }
      a.volume = Math.min(1, 10 ** (L.m.beds[db] / 20));
      a.muted = L.muted;
      if (L.playing) {
        if (a.paused) { a.currentTime = L.t; a.play().catch(() => {}); } else if (force || Math.abs(a.currentTime - L.t) > 0.2) a.currentTime = L.t;
      } else { if (!a.paused) a.pause(); if (force) a.currentTime = L.t; }
    }
  }

  // ---------- captions (.ass of shorts, drawn as text; approximate) ----------
  const ALIGN_H = { 1: "left", 4: "left", 7: "left", 2: "center", 5: "center", 8: "center", 3: "right", 6: "right", 9: "right" };
  const FONT = 0.7;  // libass and CSS size fonts differently: calibrated on a short (caption width vs the rendered frame)
  function buildSubStyles() {
    const m = L.m, out = [];
    if (m && m.subs) {
      const [rx, ry] = m.subs.res;
      for (const [name, s] of Object.entries(m.subs.styles)) {
        const u = (v) => `calc(var(--ass) * ${v})`;
        const pos = s.align >= 7 ? `top: ${(s.mv / ry) * 100}%;` : s.align <= 3 ? `bottom: ${(s.mv / ry) * 100}%;` : "top: 50%; transform: translateY(-50%);";
        const box = s.border_style === 3;
        out.push(`#livesubs .ls-${CSS.escape(name)} { ${pos} left: ${(s.ml / rx) * 100}%; right: ${(s.mr / rx) * 100}%; text-align: ${ALIGN_H[s.align] || "center"};
          font-family: "${s.font.replace(/[^\p{L}\p{N} _-]/gu, "")}", sans-serif; font-size: ${u(s.size * FONT)}; font-weight: ${s.bold ? 700 : 400}; font-style: ${s.italic ? "italic" : "normal"}; color: ${s.color}; }
          #livesubs .ls-${CSS.escape(name)} > span { ${box ? `background: ${s.outline_color}; padding: ${u(`var(--bord, ${s.outline})`)};`
            : `-webkit-text-stroke: ${u(s.outline * 2)} ${s.outline_color}; paint-order: stroke fill;${s.shadow ? ` text-shadow: ${u(s.shadow)} ${u(s.shadow)} 0 ${s.back_color};` : ""}`} }`);
      }
    }
    $("livesubstyle").textContent = out.join("\n");
    $("livesubs").innerHTML = ""; L.subKey = "";
  }
  function drawSubs(t) {
    const m = L.m, box = $("livesubs");
    if (!m.subs) return;
    const evs = C.subsAt(m.subs.events, t);
    const key = evs.map((e) => `${e.start}|${e.style}|${e.layer}`).join(",");
    if (key !== L.subKey) {
      L.subKey = key;
      box.innerHTML = evs.map((e) => {
        const runs = e.runs.map((r) => `<span${r.c ? ` style="color:${r.c}"` : ""}>${H.esc(r.t).replace(/\n/g, "<br>")}</span>`).join("");
        const bord = e.bord != null ? ` style="--bord:${e.bord}"` : "";
        return `<div class="ls ls-${H.esc(e.style)}" data-l="${e.layer}"${bord}><span>${runs}</span></div>`;
      }).join("");
    }
    evs.forEach((e, k) => { if (e.fad) box.children[k].style.opacity = String(C.fadeAt(e, t)); });
  }

  // ---------- scrub bar (the only timeline in full screen) ----------
  function drawScrub() {
    const cv = $("lscrub"), m = L.m, dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(cv.clientWidth * dpr)), h = Math.max(1, Math.round(cv.clientHeight * dpr));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    const g = cv.getContext("2d");
    g.clearRect(0, 0, w, h);
    if (!m || !m.total) return;
    const x = (t) => (t / m.total) * w;
    g.fillStyle = "#2a2f37"; g.fillRect(0, 0, w, h);
    g.fillStyle = "rgba(16,185,129,.55)";
    for (const o of m.overlays) g.fillRect(x(o.at), 0, Math.max(1, x(o.dur)), h * 0.45);
    g.fillStyle = "rgba(255,255,255,.35)";
    for (const s of m.segments) g.fillRect(x(s.end), h * 0.5, Math.max(1, dpr), h * 0.5);
    g.fillStyle = "rgba(45,226,196,.35)"; g.fillRect(0, h * 0.5, x(L.t), h * 0.5);
    g.fillStyle = "#2de2c4"; g.fillRect(x(L.t) - dpr, 0, 2 * dpr, h);
  }

  // ---------- keys: Space / K / L / J, arrows = frame (Shift: 1 s), Up/Down = cut, F = full screen, T = big view ----------
  function keys(e) {
    if (!L.m || e.ctrlKey || e.metaKey || e.altKey) return;
    const tg = e.target;
    if (tg.matches && tg.matches("input, textarea, select")) return;
    if (tg.closest && tg.closest("#transcript, #modal, .issue, .gtag, [role=button]")) return;
    if (document.querySelector("#modal.on")) return;
    const k = e.key;
    const handled = {
      " ": () => toggle(), k: () => pause(), K: () => pause(), l: () => play(), L: () => play(), j: () => jump(-JUMP), J: () => jump(-JUMP),
      ArrowLeft: () => (e.shiftKey ? jump(-1) : step(-1)), ArrowRight: () => (e.shiftKey ? jump(1) : step(1)),
      ArrowUp: () => cut(-1), ArrowDown: () => cut(1), f: fullscreen, F: fullscreen, t: () => theatre(!L.theatre), T: () => theatre(!L.theatre),
      Home: () => seek(0),
    }[k];
    if (!handled) return;
    e.preventDefault();
    if (document.activeElement && document.activeElement.tagName === "BUTTON") document.activeElement.blur();  // Space must not click it
    handled();
  }

  // the page's playhead moved (timeline click, selected item, clicked word): when paused, show that frame
  function follow(t, force) { if (L.m && (force || !L.playing) && Math.abs(t - L.t) > 1e-3) seek(t, L.playing, false); }
  return { init, load, seek, follow, play, pause, toggle, get t() { return L.t; }, get playing() { return L.playing; }, _state: L };
})();

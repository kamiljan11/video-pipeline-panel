"use strict";
// Live player math, pure (no DOM): the same formulas assemble.py gives ffmpeg, evaluated at a time t (ADR-0007).
// Tested from Python through node (tests/test_live.py); loaded in the page as window.LiveCore.
(function (root, factory) {
  const m = factory();
  if (typeof module === "object" && module.exports) module.exports = m; else root.LiveCore = m;
})(typeof self !== "undefined" ? self : this, function () {
  const clip = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
  const smooth = (x) => x * x * (3 - 2 * x);

  // = assemble.keys_expr: smoothstep between neighbouring keys, held before the first and after the last
  function keyAt(keys, t) {
    if (t < keys[0][0]) return keys[0][1];
    for (let k = 0; k + 1 < keys.length; k++) {
      const [ta, za] = keys[k], [tb, zb] = keys[k + 1];
      if (t < tb) return za + (zb - za) * smooth(clip((t - ta) / Math.max(tb - ta, 0.001), 0, 1));
    }
    return keys[keys.length - 1][1];
  }

  // zoom of a segment `t` seconds after its start (= assemble.vf_for: number = static, {a,b,t0,t1} ramp, keys = several moves)
  function zoomAt(z, t) {
    if (typeof z !== "object" || z === null) return z ?? 1;
    if (z.keys && z.keys.length) return keyAt(z.keys, t);
    const t1 = Math.max(z.t1, z.t0 + 0.05);
    return z.a + (z.b - z.a) * smooth(clip((t - z.t0) / (t1 - z.t0), 0, 1));
  }

  // = assemble.track_expr: piecewise-linear crop centre x (0..1) of a short
  function trackX(track, t) {
    if (t < track[0][0]) return track[0][1];
    for (let k = 0; k + 1 < track.length; k++) {
      const [t0, x0] = track[k], [t1, x1] = track[k + 1];
      if (t < t1) return x0 + (x1 - x0) * (t - t0) / Math.max(t1 - t0, 0.001);
    }
    return track[track.length - 1][1];
  }

  // the part of the SOURCE frame shown at segment time t, as fractions {x, y, w, h} (= assemble.vf_for crop / perspective)
  function cropRect(seg, t, film) {
    const SW = film.src_w || 2560, SH = film.src_h || 1440, vertical = !!film.vertical;
    const [fx, fy] = seg.face || film.face || [0.5, 0.45];
    const TY = film.ty ?? null, z = seg.zoom ?? 1;
    const track = vertical && seg.track && seg.track.length ? seg.track : null;
    if (typeof z !== "object" || z === null) {  // static: crop around the face, clamped to the frame
      const cw = vertical ? SH * 9 / 16 / z : SW / z, ch = SH / z;
      const cx = track ? trackX(track, t) : fx;
      const x = clip(SW * cx - cw / 2, 0, SW - cw), y = clip(SH * fy - ch * (TY ?? fy), 0, SH - ch);
      return { x: x / SW, y: y / SH, w: cw / SW, h: ch / SH };
    }
    const zz = zoomAt(z, t);
    let rx = 0, Wp = SW, rfx = fx;
    if (vertical) {  // 9:16 strip first (rounded to even pixels like assemble), then the zoom inside it
      Wp = Math.floor(Math.floor(SH * 9 / 16) / 2) * 2;
      if (track) { rx = clip(trackX(track, t) * SW - Wp / 2, 0, SW - Wp); rfx = 0.5; }
      else { rx = Math.floor(Math.trunc(clip(fx * SW - Wp / 2, 0, SW - Wp)) / 2) * 2; rfx = (fx * SW - rx) / Wp; }
    }
    const w = Wp / zz, h = SH / zz;
    const x0 = clip(rfx * Wp - w / 2, 0, Wp - w), y0 = clip(fy * SH - h * (TY ?? fy), 0, SH - h);
    return { x: (rx + x0) / SW, y: y0 / SH, w: w / SW, h: h / SH };
  }

  // output second -> which segment and which source second; past the body: the tail (last frame held, tpad)
  function locate(segs, t) {
    if (!segs.length) return null;
    const last = segs.length - 1;
    if (t >= segs[last].end) return { i: last, local: segs[last].out - segs[last].in, src: segs[last].out, tail: true };
    let lo = 0, hi = last;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (segs[mid].start <= t) lo = mid; else hi = mid - 1; }
    const s = segs[lo], local = Math.max(0, t - s.start);
    return { i: lo, local, src: s.in + local, tail: false };
  }

  // cards on screen at t: from `at` for `dur` (= assemble overlay with -itsoffset, eof_action=pass)
  const overlaysAt = (ovs, t) => ovs.filter((o) => o.at <= t && t < o.at + o.dur);
  const pipOn = (pip, t) => !!pip && pip.windows.some(([a, b]) => a <= t && t <= b);  // = between(t, at+0.25, at+dur-0.3)
  const subsAt = (events, t) => events.filter((e) => e.start <= t && t < e.end);

  // previous / next cut from t (dir -1 / +1); the film's start and end are cuts too
  function cutFrom(segs, t, dir, total) {
    const cuts = [0, ...segs.map((s) => s.end), total];
    if (dir > 0) return cuts.find((c) => c > t + 1e-3) ?? total;
    return [...cuts].reverse().find((c) => c < t - 0.04) ?? 0;
  }

  // CSS transform showing `r` of a video that fills the frame (object-fit: fill): scale up, move the crop to 0,0
  const cssCrop = (r) => `scale(${1 / r.w}, ${1 / r.h}) translate(${-r.x * 100}%, ${-r.y * 100}%)`;

  // ASS fade (\fad(in, out) ms) -> opacity at t
  function fadeAt(e, t) {
    if (!e.fad) return 1;
    const [fi, fo] = e.fad;
    let o = 1;
    if (fi > 0) o = Math.min(o, (t - e.start) * 1000 / fi);
    if (fo > 0) o = Math.min(o, (e.end - t) * 1000 / fo);
    return clip(o, 0, 1);
  }

  return { clip, smooth, keyAt, zoomAt, trackX, cropRect, locate, overlaysAt, pipOn, subsAt, cutFrom, cssCrop, fadeAt };
});

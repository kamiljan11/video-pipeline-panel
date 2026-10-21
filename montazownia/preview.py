"""Cut a +-N s window (output time) out of an EDL, so assemble.py --proxy renders only that fragment.

Pure functions: the window never starts inside a graphic (it is moved back to the graphic's start, so its
animation plays from the beginning), zoom ramps and face tracks are re-based to the new segment start,
music/SFX beds get an offset, burned-in subtitles (ASS) are shifted by the same amount.
"""
from __future__ import annotations

import copy
import re
from itertools import pairwise

MAX_EXTRA = 8.0  # how far back the start may move to begin with a whole graphic


def window(edl: dict, t: float, half: float = 10.0) -> tuple[float, float]:
    body = sum(s["out"] - s["in"] for s in edl["segments"])
    total = body + edl.get("tail", 0)
    a, b = max(0.0, t - half), min(total, t + half)
    for o in edl.get("overlays", []):
        if o["at"] < a < o["at"] + o["dur"] and a - o["at"] <= MAX_EXTRA:
            a = max(0.0, o["at"])
    return round(a, 3), round(b, 3)


def _zoom_from(z, off: float, d: float):
    """Zoom of a segment whose first `off` seconds were cut away (d = new duration)."""
    if not isinstance(z, dict) or off <= 0:
        return z
    if z.get("keys"):  # several punches in one shot (editing grammar): keep the moves after the cut, from the value at it
        keys = [[0.0, round(_key_at(z["keys"], off), 4)]] + [[round(t - off, 3), v] for t, v in z["keys"] if t > off]
        if len(keys) == 1:
            return keys[0][1]
        return {**z, "a": keys[0][1], "t0": round(max(0.0, z["t0"] - off), 3), "t1": round(max(0.0, z["t1"] - off), 3),
                "keys": keys}
    t0, t1 = z["t0"] - off, z["t1"] - off
    if t1 <= 0:
        return z["b"]
    if t0 < 0:
        a = z["a"] + (z["b"] - z["a"]) * (-t0) / (z["t1"] - z["t0"])
        return {"a": round(a, 4), "b": z["b"], "t0": 0.0, "t1": round(min(t1, d), 3)}
    return {**z, "t0": round(t0, 3), "t1": round(t1, 3)}


def _key_at(keys: list, t: float) -> float:
    """Zoom at time t through keyframes, eased like assemble.keys_expr (smoothstep between neighbours, held outside)."""
    if t <= keys[0][0]:
        return keys[0][1]
    for (ta, za), (tb, zb) in pairwise(keys):
        if t < tb:
            x = min(max((t - ta) / max(tb - ta, 0.001), 0.0), 1.0)
            return za + (zb - za) * x * x * (3 - 2 * x)
    return keys[-1][1]


def _track_from(track, off: float, d: float):
    if not track or off <= 0:
        return track
    def x_at(t):
        for (ta, xa), (tb, xb) in pairwise(track):
            if ta <= t <= tb:
                return xa + (xb - xa) * (t - ta) / max(tb - ta, 1e-6)
        return track[-1][1] if t > track[-1][0] else track[0][1]
    keys = [[0.0, round(x_at(off), 4)]] + [[round(t - off, 3), x] for t, x in track if off < t < off + d]
    return keys if len(keys) > 1 else keys + [[round(d, 3), keys[0][1]]]


def slice_edl(edl: dict, a: float, b: float) -> dict:
    out = copy.deepcopy(edl)
    segs, acc = [], 0.0
    for s in edl["segments"]:
        d = s["out"] - s["in"]
        s0, s1 = max(a, acc), min(b, acc + d)
        if s1 - s0 > 0.04:  # shorter pieces cannot be cut cleanly; the window just ends a little early
            off = s0 - acc
            n = {**copy.deepcopy(s), "in": round(s["in"] + off, 3), "out": round(s["in"] + off + (s1 - s0), 3)}
            n["zoom"] = _zoom_from(s["zoom"], off, s1 - s0)
            if s.get("track"):
                n["track"] = _track_from(s["track"], off, s1 - s0)
            segs.append(n)
        acc += d
    body = acc
    out["segments"] = segs
    out["overlays"] = [{**o, "at": round(o["at"] - a, 3)} for o in edl.get("overlays", []) if o["at"] < b and o["at"] + o["dur"] > a and o["at"] >= a - 1e-6]
    out["tail"] = round(max(0.0, min(b, body + edl.get("tail", 0)) - max(a, body)), 3)
    out["progress"] = False  # the bar would measure the fragment, not the film
    if a > 0:
        out["music_offset"] = a
    return out


_ASS_TIME = re.compile(r"(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def _ass_s(t: str) -> float:
    m_ = _ASS_TIME.fullmatch(t)
    if m_ is None:
        raise ValueError(f"bad ASS time {t!r}")
    h, m, s = m_.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def _ass_t(x: float) -> str:
    cs = round(max(0.0, x) * 100)  # round first: 59.996 s is 0:01:00.00, never 0:00:60.00
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def shift_ass(text: str, a: float, b: float) -> str:
    """Subtitles of the window [a, b]: events outside dropped, the rest moved to start at 0."""
    lines = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            lines.append(line)
            continue
        head, rest = line.split(":", 1)
        f = rest.split(",", 9)
        st, en = _ass_s(f[1].strip()), _ass_s(f[2].strip())
        if en <= a or st >= b:
            continue
        f[1], f[2] = _ass_t(st - a), _ass_t(min(en, b) - a)
        lines.append(head + ":" + ",".join(f))
    return "\n".join(lines) + "\n"

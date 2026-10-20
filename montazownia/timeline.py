"""Read-only view of a film for the UI: everything in OUTPUT time (seconds of the finished video).

Built from the pipeline's own output (edl_<film>.json after build_edl + face_track), the plan, the
transcript and gfx_specs. Nothing here decides the edit; it only places what the pipeline produced.
"""
from __future__ import annotations

import re
from typing import Any

from .planops import EMPH_WINDOW, GFX_WINDOW, kept_indices, resolve

BOX_TYPES = ("kw", "list", "slide")  # side boxes: build_edl keeps zoom at 1.0 under them


def src2out(segments: list[dict], t: float) -> float:
    """Source second -> output second (same walk as build_edl.py src2out)."""
    acc = 0.0
    for s in segments:
        if t < s["in"]:
            return acc
        if t <= s["out"]:
            return acc + t - s["in"]
        acc += s["out"] - s["in"]
    return acc


def zoom_kind(z: Any) -> str:
    if isinstance(z, dict):
        return "drift" if z["t0"] <= 0.01 else "emph"
    return "static" if z <= 1.001 else "zoom"


def segments_view(edl: dict, plan: dict) -> list[dict]:
    overrides = plan.get("seg_zoom", {})
    out, acc = [], 0.0
    for i, s in enumerate(edl["segments"]):
        d = s["out"] - s["in"]
        out.append({"i": i, "start": round(acc, 3), "end": round(acc + d, 3), "src_in": s["in"], "src_out": s["out"],
                    "zoom": s["zoom"], "kind": zoom_kind(s["zoom"]), "zmax": s.get("zmax"), "head": s.get("head"),
                    "face": s.get("face"), "override": f"{s['in']:.2f}" in overrides})
        acc += d
    return out


def overlays_view(edl: dict, specs: dict[str, dict]) -> list[dict]:
    res = []
    for o in edl.get("overlays", []):
        sp = specs.get(o["id"], {})
        res.append({"id": o["id"], "start": o["at"], "end": round(o["at"] + o["dur"], 3), "type": sp.get("type"),
                    "pip": bool(o.get("pip")), "text": sp.get("text") or sp.get("items") or o["id"]})
    return res


def sfx_events(edl: dict, specs: dict[str, dict]) -> list[dict]:
    """Where music/make_beds.py puts sound effects (display only)."""
    ev = []
    for o in edl.get("overlays", []):
        sp = specs.get(o["id"], {}); ty = sp.get("type")
        if ty in ("kw", "lower"):
            ev.append((o["at"] + 0.03, "pop"))
        if ty == "list":
            ev.append((o["at"], "whoosh"))
            ev += [(o["at"] + dl + 0.05, "pop") for dl in sp.get("delays", [])]
        if ty in ("chap", "slide", "end"):
            ev.append((o["at"] - 0.3, "whoosh"))
        if ty == "cta":
            ev.append((o["at"] + 0.1, "ding"))
    acc = 0.0
    for s in edl["segments"]:
        z = s.get("zoom")
        if isinstance(z, dict) and z["t0"] > 0.01 and z["b"] > z["a"] + 0.1:
            ev.append((acc + z["t0"] - 0.05, "whoosh"))
        acc += s["out"] - s["in"]
    return [{"t": round(max(0.0, t), 3), "kind": k} for t, k in sorted(ev)]


def words_view(plan: dict, words: list[dict], edl: dict | None) -> list[dict]:
    texts = [w["text"] for w in words]
    kept = kept_indices(plan)
    emph = {}
    for pos, e in enumerate(plan.get("emph", [])):
        i = resolve(e, texts, EMPH_WINDOW)
        if i is not None:
            emph[i] = pos
    gfx: dict[int, list[str]] = {}
    for g in plan.get("gfx", []):
        if g.get("word") == "END":
            continue
        i = resolve(g, texts, GFX_WINDOW)
        if i is not None:
            gfx.setdefault(i, []).append(g["id"])
    segs = edl["segments"] if edl else []
    res = []
    for i, w in enumerate(words):
        t = w["offsets"]["from"] / 1000
        live = None
        if i in emph and segs:
            # a punch-in only shows if build_edl kept it AND face_track's head-safety clamp (zmax) left a ramp
            seg = next((s for s in segs if s["in"] <= t <= s["out"]), None)
            live = bool(seg) and zoom_kind(seg["zoom"]) == "emph"
        res.append({"i": i, "text": w["text"], "src": t, "kept": i in kept,
                    "out": round(src2out(segs, t), 3) if (i in kept and segs) else None,
                    "emph": emph.get(i), "emph_live": live, "gfx": gfx.get(i, [])})
    return res


_T_PAREN = re.compile(r"\((\d+(?:\.\d+)?)s\)")
_T_AT = re.compile(r"w (\d+(?:\.\d+)?)s")
_T_SRC = re.compile(r"przy (\d+(?:\.\d+)?)s źródła")


def issue_time(issue: dict, edl: dict, total: float) -> float | None:
    """Output second a critic finding points at (critic.py puts it in `at` or in the message)."""
    if "at" in issue:
        return issue["at"]
    msg = issue.get("msg", "")
    if m := _T_SRC.search(msg):
        return src2out(edl["segments"], float(m.group(1)))
    if m := _T_PAREN.search(msg) or _T_AT.search(msg):
        return float(m.group(1))
    if issue.get("code") == "C6":
        return total
    if issue.get("code") == "C9":
        gid = msg.split()[1] if len(msg.split()) > 1 else ""
        return next((o["at"] for o in edl.get("overlays", []) if o["id"] == gid), None)
    return None


def build(plan: dict, words: list[dict], specs_list: list[dict], edl: dict | None, issues: list[dict] | None) -> dict:
    specs = {s["id"]: s for s in specs_list}
    if edl is None:
        return {"ready": False, "words": words_view(plan, words, None)}
    body = sum(s["out"] - s["in"] for s in edl["segments"])
    total = body + edl.get("tail", 0)
    marks = []
    for k, it in enumerate(issues or []):
        t = issue_time(it, edl, total)
        marks.append({**it, "id": k, "t": None if t is None else round(t, 3)})
    return {
        "ready": True, "body": round(body, 3), "total": round(total, 3), "vertical": bool(edl.get("vertical")),
        "w": edl.get("w"), "h": edl.get("h"),
        "segments": segments_view(edl, plan), "overlays": overlays_view(edl, specs), "sfx": sfx_events(edl, specs),
        "music": {"seed": edl.get("music_seed", 7), "db": edl.get("music_db", -13), "sfx_db": edl.get("sfx_db", -12),
                  "file": edl.get("music")},
        "issues": marks, "words": words_view(plan, words, edl),
    }

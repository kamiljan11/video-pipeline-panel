"""Read-only view of a film for the UI: everything in OUTPUT time (seconds of the finished video).

Built from the pipeline's own output (edl_<film>.json after build_edl + face_track), the plan, the
transcript and gfx_specs. Nothing here decides the edit; it only places what the pipeline produced.
"""
from __future__ import annotations

import os
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


def broll_view(edl: dict, plan: dict) -> list[dict]:
    """Stock b-roll where build_edl.py placed it; pos = index in plan["broll"] (for removal), credits per clip."""
    got = plan.get("broll")
    own: list = got if isinstance(got, list) else []  # plans arrive via Syncthing: any shape
    pos: dict[tuple, int] = {}  # (word, real file) -> first such entry; (word, None) -> first entry of the word
    for k, b in enumerate(own):
        if isinstance(b, dict) and type(b.get("word")) is int:
            real = os.path.realpath(b["file"]) if isinstance(b.get("file"), str) else None
            pos.setdefault((b["word"], real), k)
            pos.setdefault((b["word"], None), k)
    res = []
    for b in edl.get("broll", []):
        # place() lays out the first PLACEABLE entry of a word (EDL file = its real path): match it, not just the word
        f = b.get("file") if isinstance(b.get("file"), str) else None
        k = pos.get((b["word"], f), pos.get((b["word"], None))) if f else pos.get((b["word"], None))
        it = own[k] if k is not None else {}
        res.append({"pos": k, "word": b["word"], "start": b["at"], "end": round(b["at"] + b["dur"], 3),
                    "query": b.get("query", ""), "said": it.get("said", ""), "author": it.get("author", ""), "url": it.get("url", "")})
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
        if "beat_fx" not in edl and isinstance(z, dict) and z["t0"] > 0.01 and z["b"] > z["a"] + 0.1:
            ev.append((acc + z["t0"] - 0.05, "whoosh"))
        acc += s["out"] - s["in"]
    for r in edl.get("beat_fx", []):  # editing grammar: a punch sounds only when its moment asks for it
        if r.get("fx") == "punch" and r.get("sfx") in ("pop", "whoosh", "ding"):
            ev.append((r["at"] - (0.05 if r["sfx"] == "whoosh" else 0.0), r["sfx"]))
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


def beats_view(plan: dict, words: list[dict], edl: dict | None, grammar: dict | None) -> list[dict]:
    """Moments of the plan with what build_edl/beatfx did with each (edl beat_fx), plus "pace" rhythm punches."""
    kinds = (grammar or {}).get("beats", {})
    rows = {(r["word"], r["type"]): r for r in (edl or {}).get("beat_fx", [])}
    segs = edl["segments"] if edl else []
    res = []
    for pos, b in enumerate(plan.get("beats", [])):
        r = rows.get((b["word"], b["type"]), {})
        t = words[b["word"]]["offsets"]["from"] / 1000 if b["word"] < len(words) else 0.0
        res.append({"pos": pos, "type": b["type"], "label": kinds.get(b["type"], {}).get("label", b["type"]),
                    "word": b["word"], "text": words[b["word"]]["text"].strip() if b["word"] < len(words) else "",
                    "at": r.get("at", round(src2out(segs, t), 3) if segs else None), "fx": r.get("fx"),
                    "gfx": r.get("gfx"), "sfx": r.get("sfx"), "broll": r.get("broll"), "src": b.get("src"),
                    "off": bool(b.get("off")), "force": bool(b.get("force"))})
    for r in (edl or {}).get("beat_fx", []):
        if r["type"] == "pace":
            res.append({"pos": None, "type": "pace", "label": "rytm", "word": r["word"],
                        "text": words[r["word"]]["text"].strip() if r["word"] < len(words) else "", "at": r["at"],
                        "fx": r["fx"], "gfx": None, "sfx": None, "broll": None, "src": "beatfx", "off": False, "force": False})
    return sorted(res, key=lambda x: (x["at"] is None, x["at"] or 0.0))


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


def build(plan: dict, words: list[dict], specs_list: list[dict], edl: dict | None, issues: list[dict] | None,
          grammar: dict | None = None) -> dict:
    specs = {s["id"]: s for s in specs_list}
    if edl is None:
        return {"ready": False, "words": words_view(plan, words, None), "beats": beats_view(plan, words, None, grammar)}
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
        "broll": broll_view(edl, plan), "broll_skipped": edl.get("broll_skipped", []),
        "music": {"seed": edl.get("music_seed", 7), "db": edl.get("music_db", -13), "sfx_db": edl.get("sfx_db", -12),
                  "file": edl.get("music")},
        "issues": marks, "words": words_view(plan, words, edl), "beats": beats_view(plan, words, edl, grammar),
    }

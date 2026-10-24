"""What the live player plays: the EDL in OUTPUT time plus the URLs of browser-playable copies (pure, no I/O).

The player (ui/live_core.js + ui/live.js) composes the film in the browser the way assemble.py does with ffmpeg:
segments in order with their zoom/crop, cards on top, picture-in-picture over slides, b-roll under the cards,
burned-in captions (the .ass, parsed here) on top of everything, progress bar, end screen (tail).
Everything geometric is passed through as the EDL has it; the JS core mirrors assemble.py's formulas (ADR-0007).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

PIP_MARGIN = 72  # = assemble.py M
PIP_FRAME_PAD = 40  # = assemble.py: the frame PNG sits 40 px up-left of the face cam
DEFAULT_GRADE = "eq=contrast=1.04:saturation=1.07:gamma=0.98,unsharp=5:5:0.35"  # = assemble.py default


def body_of(edl: dict) -> float:
    return sum(s["out"] - s["in"] for s in edl["segments"])


def segments(edl: dict) -> list[dict]:
    out, acc = [], 0.0
    for s in edl["segments"]:
        d = s["out"] - s["in"]
        out.append({"src": s.get("src", "A"), "in": s["in"], "out": s["out"], "start": round(acc, 4), "end": round(acc + d, 4),
                    "zoom": s.get("zoom", 1.0), **{k: s[k] for k in ("face", "track") if s.get(k)}})
        acc += d
    return out


def pip(edl: dict) -> dict | None:
    """Face cam over full-screen slides (assemble.py: a square crop of the composed frame around the film's face,
    rounded mask, theme frame), enabled between at+0.25 and at+dur-0.3 of every pip overlay. Fractions of W x H."""
    wins = [[round(o["at"] + 0.25, 3), round(o["at"] + o["dur"] - 0.3, 3)] for o in edl.get("overlays", []) if o.get("pip")]
    if not wins:
        return None
    W, H = edl.get("w", 2560), edl.get("h", 1440)
    SW, SH = edl.get("src_w", 2560), edl.get("src_h", 1440)
    fx, fy = edl.get("face", [0.5, 0.45])
    ps = edl.get("pip_size", 600)
    cs = int(SH * 0.62) // 2 * 2
    px0 = int(min(max(fx * SW - cs / 2, 0), SW - cs))
    py0 = int(min(max(fy * SH - cs * 0.42, 0), SH - cs))
    x, y = W - ps - PIP_MARGIN, H - ps - PIP_MARGIN - 20
    fs = ps + 2 * PIP_FRAME_PAD
    return {"windows": wins, "box": [x / W, y / H, ps / W, ps / H], "crop": [px0 / W, py0 / H, cs / W, cs / H],
            "frame_box": [(x - PIP_FRAME_PAD) / W, (y - PIP_FRAME_PAD) / H, fs / W, fs / H], "radius": 30 / 600}


# ---- captions: the .ass build_edl.py writes for shorts, as data the browser draws as text ----
_TAG = re.compile(r"\{([^}]*)\}")
_COLOR = re.compile(r"\\1?c&H([0-9A-Fa-f]{1,8})&?")
_FAD = re.compile(r"\\fad\((\d+),(\d+)\)")
_BORD = re.compile(r"\\bord([\d.]+)")


def ass_color(v: str) -> str:
    """&HAABBGGRR / &HBBGGRR -> CSS rgba(); ASS alpha 00 = opaque."""
    h = v.strip().upper().removeprefix("&H").rstrip("&").rjust(8, "0")[-8:]
    a, b, g, r = (int(h[k:k + 2], 16) for k in range(0, 8, 2))
    return f"rgba({r},{g},{b},{round(1 - a / 255, 3)})"


def ass_time(t: str) -> float:
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _num(v: str, default: float = 0.0) -> float:
    try:
        return float(v)
    except ValueError:
        return default


def ass_runs(text: str) -> tuple[list[dict], dict]:
    """Dialogue text -> [{"t": text, "c": css colour or None}] + effects {fad: [in_ms, out_ms], bord: px}.
    Only what build_edl.py writes is understood (colour, fade, border); other tags are dropped, their text kept."""
    runs: list[dict] = []
    fx: dict[str, Any] = {}
    color = None
    pos = 0
    for m in _TAG.finditer(text):
        if m.start() > pos:
            runs.append({"t": text[pos:m.start()], "c": color})
        tags = m.group(1)
        if c := _COLOR.search(tags):
            color = ass_color(c.group(1))
        if f := _FAD.search(tags):
            fx["fad"] = [int(f.group(1)), int(f.group(2))]
        if b := _BORD.search(tags):
            fx["bord"] = _num(b.group(1))  # a broken tag must not take the whole player down (libass skips it too)
        pos = m.end()
    if pos < len(text):
        runs.append({"t": text[pos:], "c": color})
    for r in runs:
        r["t"] = r["t"].replace("\\N", "\n").replace("\\n", "\n").replace("\\h", "\u00a0")
    return [r for r in runs if r["t"]], fx


def parse_ass(text: str) -> dict:
    res = [1080, 1920]
    styles: dict[str, dict] = {}
    events: list[dict] = []
    section, sfmt, efmt = "", [], []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
            continue
        key, _, val = line.partition(":")
        if section == "[script info]" and key in ("PlayResX", "PlayResY"):
            res[0 if key == "PlayResX" else 1] = int(_num(val, res[0 if key == "PlayResX" else 1]))
        elif section.startswith("[v4") and key == "Format":
            sfmt = [f.strip() for f in val.split(",")]
        elif section.startswith("[v4") and key == "Style" and sfmt:
            f = dict(zip(sfmt, (v.strip() for v in val.split(",", len(sfmt) - 1)), strict=False))
            styles[f.get("Name", "Default")] = {
                "font": f.get("Fontname", "sans-serif"), "size": _num(f.get("Fontsize", "48"), 48),
                "color": ass_color(f.get("PrimaryColour", "&H00FFFFFF")), "outline_color": ass_color(f.get("OutlineColour", "&H00000000")),
                "back_color": ass_color(f.get("BackColour", "&H00000000")), "bold": f.get("Bold", "0") not in ("0", ""),
                "italic": f.get("Italic", "0") not in ("0", ""), "border_style": int(_num(f.get("BorderStyle", "1"), 1)),
                "outline": _num(f.get("Outline", "0")), "shadow": _num(f.get("Shadow", "0")), "align": int(_num(f.get("Alignment", "2"), 2)),
                "ml": _num(f.get("MarginL", "0")), "mr": _num(f.get("MarginR", "0")), "mv": _num(f.get("MarginV", "0"))}
        elif section == "[events]" and key == "Format":
            efmt = [f.strip() for f in val.split(",")]
        elif section == "[events]" and key == "Dialogue" and efmt:
            f = dict(zip(efmt, (v.strip() if k < len(efmt) - 1 else v for k, v in enumerate(val.split(",", len(efmt) - 1))), strict=False))
            try:
                st, en = ass_time(f["Start"]), ass_time(f["End"])
            except (KeyError, ValueError):
                continue  # a broken line: libass skips it too
            if en <= st:
                continue
            runs, fx = ass_runs(f.get("Text", "").lstrip())
            events.append({"start": st, "end": en, "style": f.get("Style", "Default"), "layer": int(_num(f.get("Layer", "0"))),
                           "runs": runs, **fx})
    events.sort(key=lambda e: (e["start"], e["layer"]))
    return {"res": res, "styles": styles, "events": events}


def model(film: str, edl: dict, version: str, media: dict, specs: dict[str, dict], subs: dict | None) -> dict:
    """media = livemedia.state(): {"sources": {key: {...}}, "cards": {id: {...}}, "broll": [stamp|None], "beds": {...}|None,
    "pip_frame": stamp|None}. URLs carry a version (?v=) so a new copy is never served from the browser cache."""
    f = quote(film, safe="")
    base = f"/api/film/{f}/live"
    body = body_of(edl)
    sources = {}
    for key, st in media["sources"].items():
        sources[key] = {"ready": st["ready"], "error": st.get("error"),
                        "url": f"{base}/src/{quote(key, safe='')}?v={st['stamp']}" if st["ready"] else None}
    overlays = []
    for k, o in enumerate(edl.get("overlays", [])):
        st = media["cards"].get(o["id"], {"state": "missing"})
        sp = specs.get(o["id"], {})
        text = sp.get("text") or sp.get("find") or (" / ".join(sp["items"]) if isinstance(sp.get("items"), list) else "") or o["id"]
        meta = st.get("meta") or {}
        dur = min(o["dur"], meta["dur"]) if meta.get("dur") else o["dur"]
        overlays.append({"k": k, "id": o["id"], "at": o["at"], "dur": round(dur, 3), "pip": bool(o.get("pip")), "state": st["state"],
                         "type": sp.get("type"), "label": str(text)[:120], "fw": meta.get("fw", 1.0), "fh": meta.get("fh", 1.0),
                         "url": f"{base}/card/{quote(o['id'], safe='')}?v={st['stamp']}" if st["state"] == "ready" else None})
    broll = [{"k": k, "at": b["at"], "dur": b["dur"], "ss": b.get("ss", 0.0), "query": b.get("query", ""),
              "url": f"{base}/broll/{k}?v={v}" if v else None} for k, (b, v) in enumerate(zip(edl.get("broll", []), media["broll"], strict=False))]
    beds = media.get("beds")
    theme = edl.get("theme") if isinstance(edl.get("theme"), dict) else None
    acc = theme.get("acc") if theme else None
    bar = acc if isinstance(acc, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", acc) else "#2de2c4"  # = themes.bar_hex
    vertical = bool(edl.get("vertical"))
    pp = pip(edl)
    if pp is not None:
        pp["frame"] = f"{base}/pipframe?v={media['pip_frame']}" if media.get("pip_frame") else None
    return {
        "film": film, "version": version, "w": edl.get("w", 2560), "h": edl.get("h", 1440), "vertical": vertical,
        "src_w": edl.get("src_w", 2560), "src_h": edl.get("src_h", 1440), "face": edl.get("face", [0.5, 0.45]),
        "ty": edl.get("face_target_y", 0.40 if vertical else None), "body": round(body, 4),
        "tail": edl.get("tail", 0) or 0, "total": round(body + (edl.get("tail", 0) or 0), 4),
        "segments": segments(edl), "sources": sources, "overlays": overlays, "broll": broll, "pip": pp,
        "progress": {"color": bar} if edl.get("progress") else None,
        "grade": edl.get("grade", DEFAULT_GRADE) == DEFAULT_GRADE,  # a custom grade is not imitated (shown without one)
        "subs": subs,
        "beds": None if not beds else {"music": f"{base}/bed/music?v={beds['stamp']}", "sfx": f"{base}/bed/sfx?v={beds['stamp']}",
                                       "fresh": beds["fresh"], "music_db": edl.get("music_db", -13), "sfx_db": edl.get("sfx_db", -12)},
    }

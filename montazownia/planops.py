"""Pure edits of a pipeline plan (plan_<film>.json) and of gfx_specs.json.

Every function takes the current data and returns a NEW object (inputs are never mutated), so the
server can keep the before/after pair for undo. Word indices are positions in the transcript
(*.cw.json "transcription"), the same indices build_edl.py uses in keeps/gfx/emph.
Formats: see ../build_edl.py docstring; plan keys this panel adds are listed in ../README.md (section "Klucze planu").
"""
from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Callable
from typing import Any

Plan = dict[str, Any]
Specs = list[dict[str, Any]]

# build_edl.py searches find+near anchors in these windows (gfx: +-25 words, emph: +-30 words)
GFX_WINDOW, EMPH_WINDOW = 25, 30
NO_FACE_LIMIT = 2.0  # face_track sets no zmax when it found no face in a shot: no head to keep in frame (UI says so)
SPEC_TYPES = ("kw", "list", "lower", "chap", "cta", "slide", "end")
SPEC_FIELDS = {"text", "sub", "icon", "icons", "items", "delays", "type", "dur", "html", "src"}


class EditError(ValueError):
    """An edit that would produce an invalid plan; the message is shown to the user (Polish)."""


def resolve(anchor: dict, words: list[str], window: int) -> int | None:
    """Word index an anchor points at: {"word": i} or {"find": text, "near": i} (same rule as build_edl.py)."""
    if isinstance(anchor.get("word"), int):
        return anchor["word"]
    if "find" not in anchor or "near" not in anchor:
        return None
    near = anchor["near"]
    hits = [i for i in range(max(0, near - window), min(len(words), near + window)) if anchor["find"].lower() in words[i].lower()]
    return min(hits, key=lambda i: abs(i - near)) if hits else None


def kept_indices(plan: Plan) -> set[int]:
    return {i for k in plan["keeps"] for i in range(k[0], k[1] + 1)}


def _opts(keep: list) -> dict:
    return dict(keep[2]) if len(keep) > 2 else {}


def _keep(a: int, b: int, opts: dict) -> list:
    return [a, b, opts] if opts else [a, b]


def toggle_word(plan: Plan, i: int, n_words: int) -> Plan:
    """Cut word i if it is kept, restore it if it is cut. Keeps stay in their list order
    (the order is the edit order, so a reordered hook stays first)."""
    if not 0 <= i < n_words:
        raise EditError(f"słowo {i} poza transkrypcją")
    p = copy.deepcopy(plan)
    keeps = p["keeps"]
    for pos, k in enumerate(keeps):
        a, b, o = k[0], k[1], _opts(k)
        if a <= i <= b:  # cut
            left = {x: o[x] for x in ("t0",) if x in o}
            right = {x: o[x] for x in ("t1",) if x in o}
            parts = []
            if a <= i - 1:
                parts.append(_keep(a, i - 1, left))
            if i + 1 <= b:
                parts.append(_keep(i + 1, b, right))
            keeps[pos:pos + 1] = parts
            return p
    # restore: grow the neighbour that ends right before / starts right after, keep order
    left = next((pos for pos, k in enumerate(keeps) if k[1] == i - 1), None)
    right = next((pos for pos, k in enumerate(keeps) if k[0] == i + 1), None)
    if left is not None and right is not None and right == left + 1:  # i was the only gap between consecutive keeps: join
        lo, ro = _opts(keeps[left]), _opts(keeps[right])
        merged = {**{x: lo[x] for x in ("t0",) if x in lo}, **{x: ro[x] for x in ("t1",) if x in ro}}
        keeps[left:right + 1] = [_keep(keeps[left][0], keeps[right][1], merged)]
    elif left is not None:
        o = {x: v for x, v in _opts(keeps[left]).items() if x != "t1"}
        keeps[left] = _keep(keeps[left][0], i, o)
    elif right is not None:
        o = {x: v for x, v in _opts(keeps[right]).items() if x != "t0"}
        keeps[right] = _keep(i, keeps[right][1], o)
    else:
        after = [pos for pos, k in enumerate(keeps) if k[1] < i]
        keeps.insert(max(after) + 1 if after else 0, [i, i])
    return p


def emph_index(plan: Plan, words: list[str], i: int) -> int | None:
    """Position in plan["emph"] of the punch-in that lands on word i, if any."""
    for pos, e in enumerate(plan.get("emph", [])):
        if resolve(e, words, EMPH_WINDOW) == i:
            return pos
    return None


def add_emph(plan: Plan, words: list[str], i: int, amt: float | None = None, speed: float = 0.3) -> Plan:
    if i not in kept_indices(plan):
        raise EditError("najazd tylko na słowie, które zostaje w filmie")
    if emph_index(plan, words, i) is not None:
        raise EditError("na tym słowie już jest najazd")
    p = copy.deepcopy(plan)
    amount = amt if amt is not None else (0.1 if p.get("vertical") else 0.15)
    p.setdefault("emph", []).append({"word": i, "amt": _bounded(amount, 0.02, 0.38, "siła"), "speed": _bounded(speed, 0.1, 2.0, "szybkość")})
    return p


def update_emph(plan: Plan, pos: int, amt: float | None = None, speed: float | None = None) -> Plan:
    p = copy.deepcopy(plan)
    e = _at(p.get("emph", []), pos, "najazd")
    if amt is not None:
        e["amt"] = _bounded(amt, 0.02, 0.38, "siła")
    if speed is not None:
        e["speed"] = _bounded(speed, 0.1, 2.0, "szybkość")
    e.pop("auto", None)  # touched by a human: no longer the fixer's guess
    return p


def remove_emph(plan: Plan, pos: int) -> Plan:
    p = copy.deepcopy(plan)
    _at(p.get("emph", []), pos, "najazd")
    del p["emph"][pos]
    return p


def anchor_gfx(plan: Plan, gfx_id: str, i: int, n_words: int) -> Plan:
    """Start graphic gfx_id on word i (replaces a find/near anchor with an exact word)."""
    if not 0 <= i < n_words:
        raise EditError(f"słowo {i} poza transkrypcją")
    p = copy.deepcopy(plan)
    g = _gfx_ref(p, gfx_id)
    for k in ("find", "near"):
        g.pop(k, None)
    g["word"] = i
    return p


def set_gfx_lead(plan: Plan, gfx_id: str, lead: float) -> Plan:
    p = copy.deepcopy(plan)
    _gfx_ref(p, gfx_id)["lead"] = _bounded(lead, -10.0, 10.0, "przesunięcie")
    return p


def remove_gfx(plan: Plan, specs: Specs, gfx_id: str) -> tuple[Plan, Specs]:
    """Remove the graphic from this film; its spec is deleted only when no other plan uses it (caller checks)."""
    p = copy.deepcopy(plan)
    _gfx_ref(p, gfx_id)
    p["gfx"] = [g for g in p["gfx"] if g["id"] != gfx_id]
    return p, copy.deepcopy(specs)


def add_gfx(plan: Plan, specs: Specs, spec: dict, i: int, n_words: int) -> tuple[Plan, Specs]:
    """New graphic on word i. spec must have a unique id, a known type and a duration."""
    if not 0 <= i < n_words:
        raise EditError(f"słowo {i} poza transkrypcją")
    gid = str(spec.get("id", "")).strip()
    if not gid or not all(c.isalnum() or c in "_-" for c in gid):
        raise EditError("identyfikator grafiki: litery, cyfry, _ i -")
    if any(s["id"] == gid for s in specs):
        raise EditError(f"grafika {gid} już istnieje")
    unknown = set(spec) - SPEC_FIELDS - {"id"}
    if unknown:
        raise EditError(f"nieznane pola grafiki: {', '.join(sorted(unknown))}")
    clean = _clean_spec({"type": "kw", "text": "", "dur": 4.0, **spec, "id": gid})
    p = copy.deepcopy(plan)
    p.setdefault("gfx", []).append({"id": gid, "word": i})
    return p, [*copy.deepcopy(specs), clean]


def duplicate_gfx(plan: Plan, specs: Specs, gfx_id: str, new_id: str, i: int, n_words: int) -> tuple[Plan, Specs]:
    src = next((s for s in specs if s["id"] == gfx_id), None)
    if src is None:
        raise EditError(f"brak grafiki {gfx_id}")
    return add_gfx(plan, specs, {**copy.deepcopy(src), "id": new_id}, i, n_words)


def update_spec(specs: Specs, gfx_id: str, fields: dict) -> Specs:
    unknown = set(fields) - SPEC_FIELDS
    if unknown:
        raise EditError(f"nieznane pola grafiki: {', '.join(sorted(unknown))}")
    out = copy.deepcopy(specs)
    for s in out:
        if s["id"] == gfx_id:
            s.update(_clean_spec({**s, **fields}))
            return out
    raise EditError(f"brak grafiki {gfx_id}")


def set_segment_zoom(plan: Plan, seg_in: float, zoom: float | dict | None, zmax: float | None) -> Plan:
    """Override the zoom of the segment that starts at seg_in (source seconds); None = back to automatic.
    Stored in plan["seg_zoom"], applied by build_edl.py after its own zoom rules; never above zmax."""
    p = copy.deepcopy(plan)
    key = f"{seg_in:.2f}"
    overrides = p.setdefault("seg_zoom", {})
    if zoom is None:
        overrides.pop(key, None)
        if not overrides:
            p.pop("seg_zoom")
        return p
    limit = zmax if zmax is not None else NO_FACE_LIMIT
    if isinstance(zoom, dict):
        if set(zoom) != {"a", "b", "t0", "t1"}:
            raise EditError("ruch zoomu: potrzebne a, b, t0, t1")
        z = {k: _bounded(zoom[k], 0, 600, k) for k in ("a", "b", "t0", "t1")}
        for k in ("a", "b"):
            _zoom_ok(z[k], limit)
        if not 0 <= z["t0"] < z["t1"]:
            raise EditError("najazd: początek musi być przed końcem")
        overrides[key] = z
    else:
        overrides[key] = _zoom_ok(_bounded(zoom, 1.0, 2.0, "zoom"), limit)
    return p


def _flag(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v in ("true", "false"):
        return v == "true"
    raise EditError("pasek postępu: tak albo nie")


def _text(v: Any) -> str:
    if not isinstance(v, str) or len(v) > 300:
        raise EditError("tekst haka: najwyżej 300 znaków")
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in v):  # a line break would start its own lines in the .ass subtitles
        raise EditError("tekst haka: jedna linia, bez znaków sterujących")
    return v


def _seed(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, str)) or not str(v).isdigit() or int(v) > 1_000_000:
        raise EditError("seed muzyki: liczba całkowita od 0 do 1000000")
    return int(v)


def _zooms(v: Any) -> list[float]:
    zs = [_bounded(x, 1.0, 1.6, "zoomy na cięciach") for x in (v if isinstance(v, list) else [])]
    if not zs:
        raise EditError("zoomy na cięciach: od 1.0 do 1.6")
    return zs


# every global the panel may set, with its validator (a new plan key = one line here + one field in ui/app.js renderGlobals)
GLOBALS: dict[str, Callable[[Any], Any]] = {
    "drift": lambda v: _bounded(v, 0, 0.2, "drift"), "tail": lambda v: _bounded(v, 0, 60, "ekran końcowy (s)"),
    "progress": _flag, "hook_text": _text, "zooms": _zooms,
    "music_db": lambda v: _bounded(v, -40, 0, "głośność muzyki (dB)"), "sfx_db": lambda v: _bounded(v, -40, 0, "głośność SFX (dB)"),
    "music_seed": _seed,
}


def set_globals(plan: Plan, values: dict) -> Plan:
    p = copy.deepcopy(plan)
    for k, v in values.items():
        if k not in GLOBALS:
            raise EditError(f"nieznane ustawienie: {k}")
        p[k] = GLOBALS[k](v)
    return p


def accept_issue(plan: Plan, code: Any, msg: Any, reason: Any) -> Plan:
    """Mark a critic finding as accepted. critic.py skips EXACT code+msg matches, and many messages contain times,
    so an edit that moves the problem brings it back as a new finding (README, "Akceptuję")."""
    if not isinstance(code, str) or not re.fullmatch(r"C\d{1,2}", code):
        raise EditError("kod uwagi krytyka: C1…C99")
    if not isinstance(msg, str) or not msg or len(msg) > 500:
        raise EditError("treść uwagi: tekst do 500 znaków")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
        raise EditError("podaj powód akceptacji (do 300 znaków)")
    p = copy.deepcopy(plan)
    acc = p.setdefault("critic_accept", [])
    if not any(a["code"] == code and a["msg"] == msg for a in acc):
        acc.append({"code": code, "msg": msg, "reason": reason.strip()})
    return p


def _zoom_ok(z: float, limit: float) -> float:
    if z < 1.0:
        raise EditError("zoom nie może być mniejszy niż 1.0")
    if z > limit + 1e-9:
        raise EditError(f"zoom {z:.3f} przekracza bezpieczny limit {limit:.3f} (głowa wyszłaby z kadru)")
    return round(z, 3)


def _bounded(v: Any, lo: float, hi: float, name: str) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError) as e:
        raise EditError(f"{name}: to nie jest liczba") from e
    if not lo <= v <= hi:  # also rejects NaN
        raise EditError(f"{name}: od {lo} do {hi}")
    return v


def _at(items: list, pos: int, name: str) -> dict:
    if not 0 <= pos < len(items):
        raise EditError(f"brak elementu: {name} {pos}")
    return items[pos]


def _gfx_ref(plan: Plan, gfx_id: str) -> dict:
    for g in plan.get("gfx", []):
        if g["id"] == gfx_id:
            return g
    raise EditError(f"grafika {gfx_id} nie jest użyta w tym filmie")


ICON_NAME = re.compile(r"[a-z0-9-]{1,64}")  # gfx/render.mjs reads lucide-static/icons/<name>.svg: a name, never a path


def _str(v: Any, name: str, limit: int = 300) -> str:
    if not isinstance(v, str) or len(v) > limit:
        raise EditError(f"{name}: tekst do {limit} znaków")
    return v


def _icon(v: Any) -> str:
    if not isinstance(v, str) or not (v == "" or ICON_NAME.fullmatch(v)):
        raise EditError("ikona: nazwa z listy (małe litery, cyfry, -)")
    return v


def _clean_spec(s: dict) -> dict:
    """gfx_specs.json is shared by every film of the project: check each field the way gfx/render.mjs uses it,
    so one bad save cannot break the graphics of all films (fields the panel does not edit pass through as they were)."""
    if s.get("type") not in SPEC_TYPES:
        raise EditError(f"typ grafiki: {', '.join(SPEC_TYPES)}")
    out = {**s, "dur": _bounded(s.get("dur", 0), 0.5, 60, "długość grafiki (s)")}
    if isinstance(s.get("text"), list):  # some specs keep alternative lines as a list
        out["text"] = [_str(x, "tekst grafiki") for x in s["text"]]
    elif "text" in s:
        out["text"] = _str(s["text"], "tekst grafiki")
    for k in ("sub", "src"):
        if k in s:
            out[k] = _str(s[k], k)
    if "html" in s:
        out["html"] = _str(s["html"], "html slajdu", 20000)
    if "icon" in s:
        out["icon"] = _icon(s["icon"])
    if "icons" in s:
        out["icons"] = [_icon(x) for x in _list(s["icons"], "ikony")]
    if "delays" in s:
        out["delays"] = [_bounded(x, 0, 60, "opóźnienia") for x in _list(s["delays"], "opóźnienia")]
    if s["type"] == "list" or "items" in s:
        items = _list(s.get("items"), "punkty listy")
        if s["type"] == "list" and not items:
            raise EditError("lista: dodaj co najmniej jeden punkt")
        out["items"] = [_str(x, "punkt listy") for x in items]
    return out


def _list(v: Any, name: str) -> list:
    if not isinstance(v, list) or len(v) > 50:
        raise EditError(f"{name}: lista (najwyżej 50)")
    return v

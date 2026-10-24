"""Live player (ADR-0007): the pure model (live.py), the cache rules (livemedia.py), the converter (mediaconv.py) and the
JS math (ui/live_core.js, run through node). The crop/zoom math is checked against assemble.py ITSELF: its vf_for /
keys_expr / track_expr are lifted out of the script and the ffmpeg expressions they build are evaluated here, so a change
of the film's framing in assemble.py fails this test until the player follows."""
import ast
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from itertools import pairwise
from pathlib import Path
from unittest import mock

from montazownia import live, livemedia, pipeline
from montazownia.jobs import Cmd, Job, StepFailed
from montazownia.preview import _key_at

PANEL = Path(__file__).resolve().parents[1]
PIPE = PANEL.parent
CORE = PANEL / "ui" / "live_core.js"


# ---------- assemble.py as the reference: its own expression builders + a small ffmpeg expression evaluator ----------
def assemble_vf(z, face=None, track=None, *, vertical=False, face0=(0.53, 0.42), ty=None, sw=2560, sh=1440):
    src = (PIPE / "assemble.py").read_text(encoding="utf-8")
    fns: list[ast.stmt] = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name in ("vf_for", "keys_expr", "track_expr")]
    ns = {"W": 1080 if vertical else 2560, "H": 1920 if vertical else 1440, "SW": sw, "SH": sh, "FX0": face0[0], "FY0": face0[1],
          "vertical": vertical, "TY": ty, "E": "\\", "pairwise": pairwise}
    with tempfile.TemporaryDirectory() as d:
        mod = Path(d) / "assemble_vf.py"
        mod.write_text(ast.unparse(ast.Module(body=fns, type_ignores=[])), encoding="utf-8")
        ns = runpy.run_path(str(mod), init_globals=ns)
    return ns["vf_for"](z, face, track)


TOKEN = re.compile(r"\s*(?:(\d+\.?\d*(?:e-?\d+)?)|([A-Za-z_]\w*)|(.))")


def parse_expr(text):
    toks = [(m.group(1), m.group(2), m.group(3)) for m in TOKEN.finditer(text) if m.group(0).strip()]
    pos = 0

    def peek():
        return toks[pos][2] if pos < len(toks) else None

    def take(op=None):
        nonlocal pos
        t = toks[pos]
        if op is not None and t[2] != op:
            raise ValueError(f"expected {op} at {pos} in {text}")
        pos += 1
        return t

    def seq():
        parts = [add()]
        while peek() == ";":
            take()
            parts.append(add())
        return ("seq", parts)

    def add():
        node = mul()
        while peek() in ("+", "-"):
            node = (take()[2], node, mul())
        return node

    def mul():
        node = unary()
        while peek() in ("*", "/"):
            node = (take()[2], node, unary())
        return node

    def unary():
        if peek() == "-":
            take()
            return ("neg", unary())
        if peek() == "+":
            take()
            return unary()
        return primary()

    def primary():
        num, name, op = take()
        if num is not None:
            return ("num", float(num))
        if op == "(":
            node = seq()
            take(")")
            return node
        if pos < len(toks) and toks[pos][2] == "(":
            take("(")
            args = [seq()]
            while peek() == ",":
                take()
                args.append(seq())
            take(")")
            return ("call", name, args)
        return ("var", name)

    node = seq()
    if pos != len(toks):
        raise ValueError(f"trailing tokens in {text}")
    return node


def ev(node, env, reg):
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "var":
        return env[node[1]]
    if kind == "neg":
        return -ev(node[1], env, reg)
    if kind == "seq":
        v = 0.0
        for p in node[1]:
            v = ev(p, env, reg)
        return v
    if kind == "call":
        name, a = node[1], node[2]
        if name == "if":  # lazy, like ffmpeg: the branch not taken must not run its st()
            return ev(a[1], env, reg) if ev(a[0], env, reg) else ev(a[2], env, reg)
        vals = [ev(x, env, reg) for x in a] if name != "st" else [ev(a[0], env, reg), ev(a[1], env, reg)]
        if name == "st":
            reg[int(vals[0])] = vals[1]
            return vals[1]
        return {"ld": lambda: reg.get(int(vals[0]), 0.0), "max": lambda: max(vals), "min": lambda: min(vals),
                "clip": lambda: min(max(vals[0], vals[1]), vals[2]), "lt": lambda: float(vals[0] < vals[1])}[name]()
    x, y = ev(node[1], env, reg), ev(node[2], env, reg)
    return x + y if kind == "+" else x - y if kind == "-" else x * y if kind == "*" else x / y


def calc(expr, **env):
    return ev(parse_expr(re.sub(r"\\(.)", r"\1", expr)), env, {})


def assemble_rect(vf, t, sw=2560, sh=1440):
    """The part of the source frame assemble.py shows at segment time t, as fractions (x, y, w, h)."""
    filters = re.split(r"(?<!\\),", vf)
    rx, pw = 0.0, float(sw)
    for f in filters:
        name, _, args = f.partition("=")
        opts = re.split(r"(?<!\\):", args)
        if name == "crop" and not any("perspective" in g for g in filters):  # static framing
            cw, ch, x, y = (calc(o, iw=sw, ih=sh, t=t) for o in opts)
            return x / sw, y / sh, cw / sw, ch / sh
        if name == "crop":  # the 9:16 strip in front of a perspective zoom
            pw, rx = calc(opts[0], iw=sw, ih=sh, t=t), calc(opts[2], iw=sw, ih=sh, t=t)
        if name == "perspective":
            kv = dict(o.split("=", 1) for o in opts)
            env = {"W": pw, "H": float(sh), "in": t * 30}
            x0, y0, x1, y2 = (calc(kv[k], **env) for k in ("x0", "y0", "x1", "y2"))
            return (rx + x0) / sw, y0 / sh, (x1 - x0) / sw, (y2 - y0) / sh
    raise AssertionError(f"no crop in {vf}")


def node_core(calls):
    """[[fn, *args]] -> results of LiveCore.fn(*args), evaluated by node."""
    js = ("const C=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
          "process.stdout.write(JSON.stringify(JSON.parse(s).map(([f,...a])=>C[f](...a))));});")
    r = subprocess.run(["node", "-e", js, str(CORE)], input=json.dumps(calls), capture_output=True, text=True, timeout=30, check=False)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "no node")
class CoreMatchesAssembleTest(unittest.TestCase):
    """cropRect(seg, t, film) == the window assemble.py's ffmpeg filter shows, within a pixel."""
    FACE = (0.53, 0.42)

    def check(self, cases, vertical, ty):
        film = {"src_w": 2560, "src_h": 1440, "vertical": vertical, "face": list(self.FACE), "ty": ty}
        calls = [["cropRect", seg, t, film] for seg, t in cases]
        for (seg, t), got in zip(cases, node_core(calls), strict=True):
            vf = assemble_vf(seg.get("zoom", 1.0), seg.get("face"), seg.get("track"), vertical=vertical, face0=self.FACE, ty=ty)
            want = assemble_rect(vf, t)
            with self.subTest(seg=seg, t=t):
                for k, (g, w) in enumerate(zip((got["x"], got["y"], got["w"], got["h"]), want, strict=True)):
                    self.assertAlmostEqual(g, w, delta=1.0 / 1440, msg=f"{'xywh'[k]}: player {g} vs assemble {w} ({vf[:120]})")

    def test_horizontal(self):
        ramp = {"a": 1.0, "b": 1.2, "t0": 0.3, "t1": 1.1}
        keys = {"a": 1.0, "b": 1.15, "t0": 0.0, "t1": 2.0, "keys": [[0.4, 1.0], [0.9, 1.15], [2.0, 1.15], [2.6, 1.05]]}
        cases = [({"zoom": 1.0}, 0.0), ({"zoom": 1.3}, 1.0), ({"zoom": 1.3, "face": [0.9, 0.1]}, 0.5)]
        cases += [({"zoom": ramp}, t) for t in (0.0, 0.3, 0.7, 1.1, 3.0)]
        cases += [({"zoom": keys}, t) for t in (0.0, 0.6, 1.5, 2.3, 4.0)]
        cases += [({"zoom": ramp, "face": [0.98, 0.95]}, 0.9)]  # clamped at the frame edge
        self.check(cases, vertical=False, ty=None)

    def test_vertical_short(self):
        ramp = {"a": 1.05, "b": 1.25, "t0": 0.0, "t1": 0.8}
        track = [[0.0, 0.40], [1.0, 0.60], [2.0, 0.55]]
        cases = [({"zoom": 1.0}, 0.0), ({"zoom": 1.2}, 0.4), ({"zoom": 1.0, "track": track}, 0.5),
                 ({"zoom": 1.1, "track": track}, 1.7), ({"zoom": 1.0, "face": [0.05, 0.5]}, 0.0)]
        cases += [({"zoom": ramp}, t) for t in (0.0, 0.4, 1.0)]
        cases += [({"zoom": ramp, "track": track}, t) for t in (0.2, 1.4, 3.0)]
        self.check(cases, vertical=True, ty=0.40)


@unittest.skipUnless(shutil.which("node"), "no node")
class CoreTest(unittest.TestCase):
    def test_prepare_key_says_what_is_left_and_nothing_when_it_cannot_start(self):
        def m(**kw):
            return {"version": "v7", "job": None, "gfx_busy": False, "sources": {"a": {"ready": True}},
                    "todo": {"cards": ["c1", "c2"], "beds": True}} | kw
        got = node_core([
            ["prepareKey", m(), True],
            ["prepareKey", m(), False],                                           # beds box off
            ["prepareKey", m(gfx_busy=True), True],                               # cards wait for the render
            ["prepareKey", m(gfx_busy=True), False],                              # ... and then nothing is left
            ["prepareKey", m(job={"id": "j"}), True],                             # a job runs already
            ["prepareKey", m(sources={"a": {"ready": True}, "b": {"ready": False}}), True],  # recording not copied yet
            ["prepareKey", m(todo={"cards": [], "beds": False}), True],           # all done
            ["prepareKey", None, True],
        ])
        self.assertEqual(got, ["v7|c1,c2|1", "v7|c1,c2|0", "v7||1", None, None, None, None, None])

    def test_a_lost_job_is_given_up_after_a_bounded_number_of_polls(self):
        js = "process.stdout.write(String(require(process.argv[1]).WATCH_FAILS))"
        n = int(subprocess.run(["node", "-e", js, str(CORE)], capture_output=True, text=True, timeout=30, check=True).stdout)
        self.assertTrue(3 <= n <= 20, n)
        self.assertIn("C.WATCH_FAILS", (PANEL / "ui" / "live.js").read_text(encoding="utf-8"))

    def test_zoom_keys_match_the_python_mirror(self):
        keys = [[0.5, 1.0], [1.0, 1.2], [1.0, 1.3], [3.0, 1.1]]
        ts = [0.0, 0.5, 0.75, 0.999, 1.0, 2.0, 3.0, 9.0]
        got = node_core([["zoomAt", {"a": 1, "b": 1, "t0": 0, "t1": 1, "keys": keys}, t] for t in ts])
        for t, g in zip(ts, got, strict=True):
            self.assertAlmostEqual(g, _key_at(keys, t), places=9, msg=t)
        self.assertEqual(node_core([["zoomAt", 1.15, 4], ["zoomAt", None, 0]]), [1.15, 1])

    def test_output_time_to_segment(self):
        segs = live.segments({"segments": [{"in": 10, "out": 12}, {"in": 3, "out": 4.5}, {"in": 20, "out": 21}]})
        got = node_core([["locate", segs, t] for t in (0, 1.999, 2.0, 3.0, 4.4, 4.5, 99)] + [["locate", [], 1]])
        self.assertEqual([(g["i"], round(g["src"], 3), g["tail"]) for g in got[:-1]],
                         [(0, 10, False), (0, 11.999, False), (1, 3, False), (1, 4, False), (2, 20.9, False), (2, 21, True), (2, 21, True)])
        self.assertIsNone(got[-1])
        cuts = node_core([["cutFrom", segs, 0.0, 1, 5.5], ["cutFrom", segs, 2.0, 1, 5.5], ["cutFrom", segs, 2.02, -1, 5.5],
                          ["cutFrom", segs, 2.1, -1, 5.5], ["cutFrom", segs, 5.0, 1, 5.5], ["cutFrom", segs, 0.01, -1, 5.5]])
        self.assertEqual(cuts, [2.0, 3.5, 0, 2.0, 5.5, 0])  # a cut just behind (< 40 ms) is skipped: Up repeats go back

    def test_what_is_on_screen(self):
        ovs = [{"id": "a", "at": 1.0, "dur": 2.0}, {"id": "b", "at": 2.5, "dur": 1.0}]
        got = node_core([["overlaysAt", ovs, t] for t in (0.99, 1.0, 2.6, 3.0, 3.5)])
        self.assertEqual([[o["id"] for o in g] for g in got], [[], ["a"], ["a", "b"], ["b"], []])
        pp = live.pip({"overlays": [{"at": 10, "dur": 5, "pip": True, "id": "s"}, {"at": 0, "dur": 9, "id": "k"}]})
        self.assertEqual(node_core([["pipOn", pp, t] for t in (10.2, 10.25, 14.7, 14.71)] + [["pipOn", None, 1]]),
                         [False, True, True, False, False])  # = assemble between(t, at+0.25, at+dur-0.3)
        e = {"start": 1.0, "end": 2.0, "fad": [100, 200]}
        self.assertEqual([round(x, 3) for x in node_core([["fadeAt", e, t] for t in (1.0, 1.05, 1.5, 1.9, 2.0)])],
                         [0, 0.5, 1, 0.5, 0])
        self.assertEqual(node_core([["fadeAt", {"start": 0, "end": 1}, 0.5]]), [1])


class AssTest(unittest.TestCase):
    ASS = """[Script Info]
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Montserrat,82,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,3,6,0,2,60,60,520,1
Style: Hook,Montserrat,64,&H0000FFFF,&H000000FF,&H00000000,&H66FFFFFF,1,0,0,0,100,100,0,0,1,4,0,8,40,40,400,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,0:00:02.00,0:00:03.00,Hook,,0,0,0,,{\\fad(120,80)\\bord9}95% FIRM,\\Nnie {\\1c&H0000FF&}widzi{\\c&HFFFFFF&}\\hzwrotu
Dialogue: 0,0:00:01.00,0:00:02.50,Cap,,0,0,0,,TAK, WYSZŁO {\\b1}W{\\b0} BADANIU
Dialogue: 0,0:00:05.00,0:00:04.00,Cap,,0,0,0,,koniec przed początkiem
Dialogue: 0,0:0x:05.00,0:00:06.00,Cap,,0,0,0,,zły czas
"""

    def test_a_broken_border_tag_is_skipped_not_fatal(self):  # one hand-edited line must not kill the whole player (500)
        for tag in ("{\\bord.}x", "{\\bord1.2.3}x"):
            with self.subTest(tag=tag):
                self.assertEqual("".join(r["t"] for r in live.ass_runs(tag)[0]), "x")

    def test_a_broken_style_colour_falls_back_not_fatal(self):
        a = live.parse_ass("[V4+ Styles]\nFormat: Name, PrimaryColour\nStyle: Cap,&HZZ0000\n")
        self.assertEqual(a["styles"]["Cap"]["color"], "rgba(255,255,255,1.0)")
        self.assertEqual(live.ass_color("&H000000FF"), "rgba(255,0,0,1.0)")

    def test_parse(self):
        a = live.parse_ass(self.ASS)
        self.assertEqual(a["res"], [1080, 1920])
        cap, hook = a["styles"]["Cap"], a["styles"]["Hook"]
        self.assertEqual((cap["border_style"], cap["outline"], cap["align"], cap["mv"], cap["bold"]), (3, 6, 2, 520, True))
        self.assertEqual((cap["back_color"], hook["color"], hook["back_color"]), ("rgba(0,0,0,0.498)", "rgba(255,255,0,1.0)", "rgba(255,255,255,0.6)"))
        self.assertEqual([e["start"] for e in a["events"]], [1.0, 2.0])  # sorted; empty and broken lines skipped like libass
        cap_e, hook_e = a["events"]
        self.assertEqual(cap_e["runs"], [{"t": "TAK, WYSZŁO ", "c": None}, {"t": "W", "c": None}, {"t": " BADANIU", "c": None}])
        self.assertEqual((hook_e["fad"], hook_e["bord"], hook_e["layer"]), ([120, 80], 9.0, 1))
        self.assertEqual(hook_e["runs"], [{"t": "95% FIRM,\nnie ", "c": None}, {"t": "widzi", "c": "rgba(255,0,0,1.0)"},
                                          {"t": " zwrotu", "c": "rgba(255,255,255,1.0)"}])

    def test_colour(self):
        self.assertEqual(live.ass_color("&H00336699"), "rgba(153,102,51,1.0)")
        self.assertEqual(live.ass_color("&HFF000000&"), "rgba(0,0,0,0.0)")
        self.assertEqual(live.ass_color("&H0000FF"), "rgba(255,0,0,1.0)")
        self.assertEqual(live.ass_time("1:02:03.50"), 3723.5)


MODEL_EDL = {"sources": {"A": "/x/a b.mp4"}, "w": 1080, "h": 1920, "vertical": True, "tail": 2, "progress": True,
             "theme": {"name": "cetus", "acc": "#123abc"}, "grade": "eq=contrast=1.2",
             "segments": [{"src": "A", "in": 1, "out": 3, "zoom": 1.1, "track": [[0, 0.5]], "face": [0.4, 0.4]},
                          {"src": "A", "in": 7, "out": 8.5}],
             "overlays": [{"id": "g 1", "at": 0.5, "dur": 4, "file": "/g.mov"}, {"id": "s1", "at": 1, "dur": 2, "pip": True}],
             "broll": [{"at": 1, "dur": 1, "file": "/b.mp4", "query": "q"}]}
MODEL_MEDIA = {"sources": {"A": {"ready": True, "stamp": "abc123abc123"}},
               "cards": {"g 1": {"state": "ready", "stamp": "fff000fff000", "meta": {"dur": 3.0, "fw": 1, "fh": 1}},
                         "s1": {"state": "no_render", "stamp": ""}},
               "broll": ["b0b0b0b0b0b0"], "beds": {"stamp": "v1", "fresh": False}, "pip_frame": "f1f1f1f1f1f1"}


class ModelTest(unittest.TestCase):
    def test_model(self):
        m = live.model("short 1", MODEL_EDL, "ver", MODEL_MEDIA, {"g 1": {"text": "jeden"}}, None)
        self.assertEqual((m["body"], m["total"], m["ty"], m["vertical"]), (3.5, 5.5, 0.40, True))
        self.assertEqual(m["segments"][0], {"src": "A", "in": 1, "out": 3, "start": 0.0, "end": 2.0, "zoom": 1.1,
                                            "face": [0.4, 0.4], "track": [[0, 0.5]]})
        self.assertEqual(m["sources"]["A"]["url"], "/api/film/short%201/live/src/A?v=abc123abc123")
        g1, s1 = m["overlays"]
        self.assertEqual((g1["dur"], g1["label"], g1["url"]), (3.0, "jeden", "/api/film/short%201/live/card/g%201?v=fff000fff000"))
        self.assertEqual((s1["url"], s1["pip"], s1["label"]), (None, True, "s1"))
        self.assertEqual(m["broll"][0]["url"], "/api/film/short%201/live/broll/0?v=b0b0b0b0b0b0")  # a swapped clip = new URL
        self.assertEqual((m["progress"], m["grade"]), ({"color": "#123abc"}, False))  # a custom grade is not imitated
        self.assertEqual(m["beds"]["music"], "/api/film/short%201/live/bed/music?v=v1")
        self.assertEqual(m["pip"]["frame"], "/api/film/short%201/live/pipframe?v=f1f1f1f1f1f1")
        bad = live.model("f", {**MODEL_EDL, "theme": {"acc": "red;x"}, "grade": live.DEFAULT_GRADE, "progress": True}, "v",
                         {**MODEL_MEDIA, "beds": None, "broll": [None], "pip_frame": None}, {}, None)
        self.assertEqual((bad["progress"], bad["grade"], bad["beds"]), ({"color": "#2de2c4"}, True, None))
        self.assertEqual((bad["broll"][0]["url"], bad["pip"]["frame"]), (None, None))

    def test_pip_geometry_is_assembles(self):
        p = live.pip({"overlays": [{"at": 2, "dur": 4, "pip": True}], "face": [0.5, 0.45]})
        assert p is not None
        self.assertEqual(p["windows"], [[2.25, 5.7]])
        x, y, w, h = (round(v * d) for v, d in zip(p["box"], (2560, 1440, 2560, 1440), strict=True))
        self.assertEqual((x, y, w, h), (2560 - 600 - 72, 1440 - 600 - 72 - 20, 600, 600))
        cs = int(1440 * 0.62) // 2 * 2
        self.assertEqual([round(v * d) for v, d in zip(p["crop"], (2560, 1440, 2560, 1440), strict=True)],
                         [int(0.5 * 2560 - cs / 2), int(0.45 * 1440 - cs * 0.42), cs, cs])
        self.assertIsNone(live.pip({"overlays": [{"at": 1, "dur": 2}]}))


class LiveMediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="montaz-live-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        mock.patch.dict(os.environ, {"MONTAZ_PANEL_DIR": str(self.tmp / "panel")}).start()
        self.addCleanup(mock.patch.stopall)
        self.work = self.tmp / "work"
        (self.work / "gfx").mkdir(parents=True)
        self.src = self.work / "rec.mp4"
        self.src.write_bytes(b"rec")
        old = time.time() - 60
        for g in ("g1", "g1_s1280", "fresh"):
            (self.work / "gfx" / f"{g}.mov").write_bytes(b"mov")
            if g != "fresh":
                os.utime(self.work / "gfx" / f"{g}.mov", (old, old))
        self.edl = {"sources": {"A": str(self.src)}, "segments": [{"in": 0, "out": 1}], "w": 2560, "h": 1440,
                    "overlays": [{"id": g, "file": str(self.work / "gfx" / f"{g}.mov"), "at": 0, "dur": 1} for g in ("g1", "g1_s1280", "fresh")]
                    + [{"id": "gone", "file": str(self.work / "gfx" / "gone.mov"), "at": 0, "dur": 1}, {"id": "../x", "at": 0, "dur": 1}]}
        self.edl_p = self.work / "edl_a.json"
        self.edl_p.write_text(json.dumps(self.edl))
        (self.work / "plan_a.json").write_text(json.dumps({"edl_out": "edl_a.json"}))

    def test_paths_and_targets(self):
        self.assertEqual(livemedia.card_target({"w": 1080, "h": 1920, "vertical": True}), {"mode": "fit", "w": 720, "h": 1280})
        self.assertEqual(livemedia.card_target({"w": 2560, "h": 1440}), {"mode": "scale", "k": 0.5, "fullw": 1280, "fullh": 720})
        self.assertEqual(livemedia.version_prefix("card_g1_s1280_0123456789ab.webm"), "card_g1_s1280_")
        self.assertNotEqual(livemedia.version_prefix("card_g1_s1280_s1280_0123456789ab.webm"), "card_g1_s1280_")
        for bad in ("rel.mp4", str(self.work), str(self.work / "nope.mp4"), "/x\0y", 5, None, ""):
            with self.subTest(bad=bad):
                self.assertIsNone(livemedia.local_file(bad))
        self.assertTrue(livemedia.cache_dir(self.work).is_relative_to(self.tmp / "panel"))
        v1 = livemedia.source_file(self.work, self.src)
        self.src.write_bytes(b"another recording")
        self.assertNotEqual(livemedia.source_file(self.work, self.src), v1)  # a new stamp: converted again

    def test_edl_version_is_the_content(self):
        v = livemedia.edl_version(self.edl_p)
        # run_v4.sh's make_beds.py re-saves the project EDL with the bed paths (and another indent): the same film
        self.edl_p.write_text(json.dumps({**self.edl, "music": "/r/music.wav", "sfx": "/r/sfx.wav"}, indent=1))
        self.assertEqual(livemedia.edl_version(self.edl_p), v)
        self.edl_p.write_text(json.dumps({**self.edl, "tail": 3}))
        self.assertNotEqual(livemedia.edl_version(self.edl_p), v)
        self.assertEqual(len(livemedia.version_of(b"{not json")), 12)

    def test_broll_only_from_the_stock_cache(self):
        root = self.tmp / "broll"
        root.mkdir()
        (root / "ok.mp4").write_bytes(b"x")
        (self.tmp / "evil.mp4").write_bytes(b"x")
        with mock.patch.object(pipeline, "BROLL_DIR", root):
            edl = {"broll": [{"file": str(root / "ok.mp4")}, {"file": str(root / ".." / "evil.mp4")}, {"file": "/etc/passwd"}, "x"]}
            self.assertEqual([livemedia.broll_file(edl, k) for k in range(5)], [root / "ok.mp4", None, None, None, None])
            self.assertIsNone(livemedia.broll_file(edl, -1))
            (root / "link.mp4").symlink_to(self.tmp / "evil.mp4")  # a link inside the cache pointing out of it
            self.assertIsNone(livemedia.broll_file({"broll": [{"file": str(root / "link.mp4")}]}, 0))
            st = livemedia.state(self.work, "a", self.edl_p, {**self.edl, **edl})
            self.assertEqual([bool(v) for v in st["broll"]], [True, False, False, False])

    def test_state_and_todo(self):
        st = livemedia.state(self.work, "a", self.edl_p, self.edl)
        self.assertEqual({g: c["state"] for g, c in st["cards"].items()},
                         {"g1": "todo", "g1_s1280": "todo", "fresh": "busy", "gone": "no_render", "../x": "bad_id"})
        self.assertFalse(st["sources"]["A"]["ready"])
        self.assertEqual(livemedia.todo(st, beds=False), {"proxies": ["A"], "cards": ["g1", "g1_s1280"], "beds": False})
        self.assertTrue(livemedia.todo(st, beds=True)["beds"])
        bd = livemedia.beds_dir(self.work, "a")
        bd.mkdir(parents=True)
        for f in ("music.wav", "sfx.wav"):
            (bd / f).write_bytes(b"w")
        (bd / "edl.version").write_text(livemedia.edl_version(self.edl_p))
        self.assertTrue(livemedia.state(self.work, "a", self.edl_p, self.edl)["beds"]["fresh"])
        self.edl_p.write_text(json.dumps({**self.edl, "tail": 3}))  # an edit: the beds are from the previous version
        st = livemedia.state(self.work, "a", self.edl_p, self.edl)
        self.assertFalse(st["beds"]["fresh"])
        self.assertTrue(livemedia.todo(st, beds=True)["beds"])
        card = Path(st["cards"]["g1"]["file"])
        card.write_bytes(b"webm")
        self.assertEqual(livemedia.state(self.work, "a", self.edl_p, self.edl)["cards"]["g1"]["state"], "todo")  # no meta yet
        card.with_suffix(".json").write_text('{"w": 1, "h": 1, "dur": 1}')
        self.assertEqual(livemedia.state(self.work, "a", self.edl_p, self.edl)["cards"]["g1"]["state"], "ready")

    def run_plan(self, beds=True, gfx_busy=False):
        steps = livemedia.prepare_steps(self.work, "a", beds, lambda: gfx_busy)
        plan, conv, cleanup = steps
        assert isinstance(conv, Cmd) and callable(plan) and callable(cleanup)
        self.assertEqual(conv.timeout, livemedia.LIMIT)
        job = Job(1, "live", "a", steps)
        plan(job)
        tasks = json.loads((livemedia.cache_dir(self.work) / "tasks_a.json").read_text())
        return job, tasks, cleanup

    def test_prepare_decides_at_run_time(self):
        job, tasks, _ = self.run_plan()
        self.assertEqual([p["src"] for p in tasks["proxies"]], [str(self.src)])
        self.assertEqual([c["id"] for c in tasks["cards"]], ["g1", "g1_s1280"])  # not the one still being written
        self.assertEqual(tasks["cards"][0]["mode"], "scale")
        self.assertEqual(json.loads(Path(tasks["beds"]["edl"]).read_text()), self.edl)  # make_beds gets a COPY of the EDL
        self.assertEqual(Path(tasks["beds"]["edl"]).parent, Path(tasks["beds"]["dir"]))
        self.assertEqual(tasks["beds"]["final"], str(livemedia.beds_dir(self.work, "a")))  # swapped in whole when done
        self.assertTrue(Path(tasks["beds"]["dir"]).is_relative_to(self.tmp / "panel"))
        self.assertEqual(tasks["beds"]["version"], livemedia.edl_version(self.edl_p))
        self.assertEqual(tasks["cards"][0]["mtime_ns"], (self.work / "gfx" / "g1.mov").stat().st_mtime_ns)
        self.assertEqual(job.result["todo"], {"proxies": 1, "cards": 2, "beds": True})
        job, tasks, _ = self.run_plan(beds=False, gfx_busy=True)
        self.assertEqual((tasks["cards"], tasks["beds"]), ([], None))
        self.assertTrue(any("grafiki" in ln for ln in job.lines))

    def test_prepare_fails_with_a_reason(self):
        self.src.unlink()
        with self.assertRaisesRegex(StepFailed, "brak nagrania rec.mp4"):
            self.run_plan()
        self.edl_p.unlink()
        with self.assertRaisesRegex(StepFailed, "brak EDL"):
            self.run_plan()

    def test_cleanup_keeps_current_and_other_inputs(self):
        cdir = livemedia.cache_dir(self.work)
        _, tasks, cleanup = self.run_plan()
        cur = [Path(tasks["proxies"][0]["out"]), Path(tasks["cards"][0]["out"]), Path(tasks["cards"][1]["out"])]
        old = [re.sub(r"_[0-9a-f]{12}\.", "_0123456789ab.", p.name) for p in cur]
        for name in [p.name for p in cur] + old:
            (cdir / name).write_bytes(b"x")
        (cdir / old[1]).with_suffix(".json").write_text("{}")
        (cdir / "src_other_0123456789ab.mp4").write_bytes(b"another recording's copy")
        cleanup(Job(1, "live", "a", []))
        self.assertTrue(all(p.exists() for p in cur))
        self.assertEqual([(cdir / n).exists() for n in old], [False, False, False])
        self.assertFalse((cdir / old[1]).with_suffix(".json").exists())
        self.assertTrue((cdir / "src_other_0123456789ab.mp4").exists())


@unittest.skipUnless(shutil.which("ffmpeg"), "no ffmpeg")
class MediaconvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="montaz-conv-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def ff(self, *args):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=60)

    def conv(self, tasks):
        p = self.tmp / "tasks.json"
        p.write_text(json.dumps(tasks))
        return subprocess.run([sys.executable, str(PANEL / "montazownia" / "mediaconv.py"), str(p)],
                              capture_output=True, text=True, timeout=120, check=False)

    def test_proxy_and_alpha_card(self):
        rec, mov = self.tmp / "rec.mp4", self.tmp / "card.mov"
        self.ff("-f", "lavfi", "-i", "testsrc2=s=640x360:d=2:r=30", "-f", "lavfi", "-i", "sine=d=2", "-shortest",
                "-c:v", "libx264", "-c:a", "aac", str(rec))
        self.ff("-f", "lavfi", "-i", "color=c=red@0.5:s=320x180:d=1,format=rgba", "-c:v", "prores_ks", "-profile:v", "4444",
                "-pix_fmt", "yuva444p10le", str(mov))
        out_p, out_c = self.tmp / "p.mp4", self.tmp / "c.webm"
        r = self.conv({"proxies": [{"src": str(rec), "out": str(out_p), "dur": 2}],
                       "cards": [{"id": "g1", "src": str(mov), "out": str(out_c), "mtime_ns": mov.stat().st_mtime_ns,
                                  "mode": "scale", "k": 0.5, "fullw": 1280, "fullh": 720}]})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("postęp 100%", r.stdout)
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out_p)],
                                          capture_output=True, text=True, check=True).stdout)["streams"]
        self.assertEqual([(s["codec_name"], s.get("height")) for s in probe], [("h264", 720), ("aac", None)])
        meta = json.loads(out_c.with_suffix(".json").read_text())
        self.assertEqual((meta["w"], meta["h"], meta["fw"]), (160, 90, 0.125))
        tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream_tags=alpha_mode", "-of", "csv=p=0", str(out_c)],
                              capture_output=True, text=True, check=True).stdout
        self.assertEqual(tags.strip(), "1")  # VP9 alpha: cards stay transparent in the browser
        self.assertEqual(list(self.tmp.glob("*.part")), [])

    def test_failure_is_reported_and_leaves_no_half_file(self):
        bad = self.tmp / "bad.mp4"
        bad.write_bytes(b"not a video")
        r = self.conv({"proxies": [{"src": str(bad), "out": str(self.tmp / "p.mp4"), "dur": 1}],
                       "cards": [{"id": "g1", "src": str(bad), "out": str(self.tmp / "c.webm"), "mtime_ns": bad.stat().st_mtime_ns,
                                  "mode": "fit", "w": 72, "h": 128}]})
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stdout, r"BŁĄD kopia nagrania bad\.mp4: .+")
        self.assertRegex(r.stdout, r"BŁĄD karta g1: .+")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["bad.mp4", "tasks.json"])

    def test_card_changed_since_planning_waits_for_the_next_run(self):
        mov = self.tmp / "card.mov"
        mov.write_bytes(b"half written")
        r = self.conv({"cards": [{"id": "g1", "src": str(mov), "out": str(self.tmp / "c.webm"), "mtime_ns": 1, "mode": "fit", "w": 72, "h": 128},
                                 {"id": "g2", "src": str(self.tmp / "gone.mov"), "out": str(self.tmp / "d.webm"), "mtime_ns": 1,
                                  "mode": "fit", "w": 72, "h": 128}]})
        self.assertEqual(r.returncode, 0, r.stdout)  # not a failure: the next run converts the new file
        self.assertIn("karta g1 zmieniła się w trakcie", r.stdout)
        self.assertIn("karta g2 zmieniła się w trakcie", r.stdout)
        self.assertFalse((self.tmp / "c.webm").exists())

    def beds_task(self, script):
        final = self.tmp / "a.live"
        final.mkdir()
        (final / "music.wav").write_text("old")
        (final / "edl.version").write_text("v0")
        new = self.tmp / "a.live.new"
        new.mkdir()
        (new / "edl.json").write_text("{}")
        fake = self.tmp / "fake_beds.py"
        fake.write_text(script)
        return final, new, {"beds": {"python": sys.executable, "script": str(fake), "edl": str(new / "edl.json"), "dir": str(new),
                                     "final": str(final), "version": "v1", "cwd": str(self.tmp)}}

    def test_beds_replace_the_old_ones_only_when_complete(self):
        final, new, tasks = self.beds_task("import sys, pathlib\nd = pathlib.Path(sys.argv[2])\n"
                                           "(d / 'music.wav').write_text('new'); (d / 'sfx.wav').write_text('new')\n")
        r = self.conv(tasks)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(((final / "music.wav").read_text(), (final / "edl.version").read_text()), ("new", "v1"))
        self.assertFalse(new.exists())
        self.assertFalse(final.with_name("a.live.old").exists())

    def test_failed_beds_keep_the_previous_mix(self):
        final, new, tasks = self.beds_task("import sys, pathlib\n(pathlib.Path(sys.argv[2]) / 'music.wav').write_text('half')\n"
                                           "sys.exit('make_beds: brak muzyki')\n")
        r = self.conv(tasks)
        self.assertEqual(r.returncode, 1)
        self.assertIn("BŁĄD muzyka i SFX: make_beds: brak muzyki", r.stdout)
        self.assertEqual(((final / "music.wav").read_text(), (final / "edl.version").read_text()), ("old", "v0"))
        self.assertFalse(new.exists())

    def test_beds_over_the_time_limit_are_reported_with_the_other_errors(self):
        final, new, tasks = self.beds_task("import time\ntime.sleep(30)\n")
        r = self.conv({"beds": {**tasks["beds"], "timeout": 1},
                       "proxies": [{"src": str(self.tmp / "missing.mp4"), "out": str(self.tmp / "p.mp4"), "dur": 1}]})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("BŁĄD kopia nagrania missing.mp4", r.stdout)  # the error collected earlier is not lost
        self.assertIn("BŁĄD muzyka i SFX: TimeoutExpired", r.stdout)
        self.assertEqual((final / "edl.version").read_text(), "v0")
        self.assertFalse(new.exists())


if __name__ == "__main__":
    unittest.main()

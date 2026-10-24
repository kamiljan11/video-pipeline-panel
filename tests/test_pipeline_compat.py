"""The panel's pipeline additions must not change anything when their keys/flags are absent.

Runs the scripts as they were before the panel (tests/pipeline_baseline/*.py.orig, kept in this repo because
~/video-pipeline itself is not in git) and the current scripts side by side on a copy of the sample project,
in a temp dir (never in ~/Videos: absolute output paths are rewritten).
"""
import array
import itertools
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from typing import ClassVar

PIPE = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).resolve().parent / "pipeline_baseline"  # build_edl, critic, assemble before the panel; make_beds, face_track before beats
SAMPLE = Path.home() / "Videos" / "Montaz" / "2026-10-20" / "work"
FILM = "short3_jedno_zlecenie"

FAKE_FFMPEG = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
open(sys.argv[-1], "wb").close()
"""


class SteadyXTest(unittest.TestCase):
    """plan "steady_x" on a made-up film: the crop aim formula, the fallback and the vertical no-op (no sample needed)"""

    def run_ft(self, faces, zooms, extra):
        d = Path(tempfile.mkdtemp(prefix="steady-"))
        self.addCleanup(shutil.rmtree, d, True)
        src = d / "src.mp4"  # face_track reads the frame size from the source; every face comes from the cache
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=2560x1440:d=1", "-c:v", "libx264", str(src)], check=True)
        segs, cache = [], {}
        for i, ((x, w), z) in enumerate(zip(faces, zooms)):
            a, b = 2.0 * i, 2.0 * i + 2.0
            segs.append({"src": "A", "in": a, "out": b, "zoom": z})
            for t in (a + 0.1, a + 1.0, b - 0.1):
                cache[f"{t:.2f}"] = [x, 0.3, w, 0.2]
        (d / "faces_cache.json").write_text(json.dumps(cache))
        (d / "edl.json").write_text(json.dumps({"sources": {"A": str(src)}, "segments": segs, **extra}))
        r = subprocess.run([str(PIPE / ".venv" / "bin" / "python"), str(PIPE / "face_track.py"), str(d / "edl.json")],
                           capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((d / "edl.json").read_text())["segments"]

    def test_face_keeps_its_full_frame_screen_x_on_every_zoom(self):
        segs = self.run_ft([(0.30, 0.1)] * 3, [1.0, 1.2, 1.0], {"steady_x": True})
        self.assertAlmostEqual(segs[1]["face"][0], 0.35 + (0.5 - 0.35) / 1.2, places=3)  # 0.475: face stays at x 0.35 on screen
        x0 = segs[1]["face"][0] - 0.5 / 1.2
        self.assertAlmostEqual((0.35 - x0) * 1.2, 0.35, places=3)

    def test_a_shot_the_anchor_would_cost_zoom_keeps_its_own_aim(self):
        # the speaker moved right in the last shot: aiming at the film's median face would push the head out at 1.3
        segs = self.run_ft([(0.30, 0.1)] * 3 + [(0.72, 0.1)], [1.0, 1.2, 1.0, 1.3], {"steady_x": True})
        self.assertAlmostEqual(segs[3]["face"][0], 0.77, places=3)
        self.assertEqual(segs[3]["zoom"], 1.3)

    def test_vertical_films_and_a_bad_window(self):
        v = {"vertical": True}
        self.assertEqual(self.run_ft([(0.30, 0.1)] * 2, [1.0, 1.2], {**v, "steady_x": True}),
                         self.run_ft([(0.30, 0.1)] * 2, [1.0, 1.2], v))
        with self.assertRaises(AssertionError):  # a named error (exit 1), never a silent 1-second window from "true"
            self.run_ft([(0.30, 0.1)] * 2, [1.0, 1.2], {"steady_x": "true"})


class EdgeFixTest(unittest.TestCase):
    """plan "edge_fix" (edges.py) and critic C12/C13 on a made-up 10 s recording: tones where speech is, silence elsewhere.

    words (whisper times):  Raz 1.0-1.5 | dwa 1.6-2.0 | eee 2.1-2.85 (really 2.1-2.3) | trzy 3.0-6.5 (squashed: really starts at 6.0) |
                            cztery 6.6-7.2 | yyy 7.3-7.6 | pięć 7.7-8.3
    sound: 1.0-2.0, 2.1-2.3, 6.0-7.68 without a break (whisper ends "yyy" too early), a dip, 7.76-8.3; .sil = the long gaps
    """
    WORDS: ClassVar = [(1.0, 1.5, " Raz"), (1.6, 2.0, " dwa"), (2.1, 2.85, " eee"), (3.0, 6.5, " trzy"), (6.6, 7.2, " cztery"),
             (7.3, 7.6, " yyy"), (7.7, 8.3, " pięć")]
    SOUND: ClassVar = [(1.0, 2.0), (2.1, 2.3), (6.0, 7.68), (7.76, 8.3)]

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="edges-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        (self.d / "rec.cw.json").write_text(json.dumps({"transcription": [
            {"offsets": {"from": round(a * 1000), "to": round(b * 1000)}, "text": t} for a, b, t in self.WORDS]}))
        (self.d / "rec.sil").write_text("".join(f"silence_start: {a}\nsilence_end: {b}\n" for a, b in ((0, 1.0), (2.4, 6.0), (8.3, 10))))
        sr = 16000
        pcm = array.array("h", (int(8000 * math.sin(2 * math.pi * 220 * i / sr)) if any(a <= i / sr < b for a, b in self.SOUND) else 0
                                for i in range(10 * sr)))
        with wave.open(str(self.d / "rec.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())

    def run_build(self, keeps, script=PIPE, **extra):
        plan = {"words": "rec.cw.json", "sil": "rec.sil", "src": "rec.mp4", "keeps": keeps, "edl_out": "edl.json", **extra}
        (self.d / "plan.json").write_text(json.dumps(plan))
        return subprocess.run([sys.executable, str(script / "build_edl.py"), "plan.json"], cwd=self.d, capture_output=True, text=True, check=False)

    def build(self, keeps, script=PIPE, **extra):
        r = self.run_build(keeps, script, **extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r, json.loads((self.d / "edl.json").read_text())["segments"]

    def critic(self, *flags):
        r = subprocess.run([sys.executable, str(PIPE / "critic.py"), "plan.json", "--json", "c.json", *flags], cwd=self.d,
                           capture_output=True, text=True, check=False)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return [(i["code"], i["msg"]) for i in json.loads((self.d / "c.json").read_text())["issues"] if i["code"] in ("C12", "C13")]

    def test_a_start_inside_a_long_silence_moves_to_the_sound(self):
        r, old = self.build([[3, 4]])
        self.assertAlmostEqual(old[0]["in"], 2.9)  # without the key: 3.1 s of dead air (the bug of 2026-10-23)
        self.assertEqual(self.critic("--edges")[0], ("C12", "0:00.00 początek ujęcia: 3.1 s ciszy przy cięciu (źródło 2.90s)"))
        c12 = next(i for i in json.loads((self.d / "c.json").read_text())["issues"] if i["code"] == "C12")
        self.assertEqual(c12["at"], 0.0)  # output time: the panel puts the marker on the timeline from it
        r, new = self.build([[3, 4]], edge_fix=True)
        self.assertAlmostEqual(new[0]["in"], 6.0 - 0.08)
        self.assertIn("edge_fix moved 1 keep edges", r.stdout)
        self.assertNotIn("C12", [c for c, _ in self.critic()])  # the key alone turns the checks on

    def test_an_end_inside_a_long_silence_moves_back_to_the_sound(self):
        _, old = self.build([[0, 1, {"t1": 3.5}]])  # explicit, without the key: re-snapped by nothing, 1.1 s of dead air
        self.assertEqual(old[0]["out"], 3.5)
        _, old = self.build([[0, 2]])  # whisper stretched "eee" to 2.85: the computed end 2.97 is 0.57 s into the 2.4-6.0 gap
        self.assertAlmostEqual(old[-1]["out"], 2.97)
        _, new = self.build([[0, 2]], edge_fix=True)  # back to just after the sound
        self.assertAlmostEqual(new[-1]["out"], 2.4 + 0.12)

    def test_an_explicit_t0_t1_is_final_with_the_key(self):
        _, old = self.build([[0, 1, {"t1": 2.6}]])
        self.assertAlmostEqual(old[0]["out"], 2.52)  # the old silence snap pulls the editor's value back
        _, new = self.build([[0, 1, {"t1": 2.6}]], edge_fix=True)
        self.assertEqual(new[0]["out"], 2.6)
        _, new = self.build([[3, 4, {"t0": 2.9}]], edge_fix={"dips": True})  # not even the dead-air trim touches it
        self.assertEqual(new[0]["in"], 2.9)

    def test_dips_move_a_loud_cut_to_the_nearest_quiet_spot_or_leave_it_and_report_it(self):
        _, off = self.build([[3, 4], [6, 6]], edge_fix=True)
        self.assertAlmostEqual(off[1]["in"], 7.62)  # in the tail of "yyy" (sound until 7.68)
        self.assertEqual([c for c, _ in self.critic()], ["C13", "C13"])  # end of "cztery" and start of "pięć" both chop sound
        _, on = self.build([[3, 4], [6, 6]], edge_fix={"dips": True})
        self.assertAlmostEqual(on[1]["in"], 7.695)  # first spot where 30 ms around the cut are quiet
        self.assertAlmostEqual(on[0]["out"], off[0]["out"])  # continuous sound up to the next word's start + 60 ms: no dip, left alone
        found = self.critic()
        self.assertEqual(len(found), 1)
        self.assertRegex(found[0][1], r"^0:01\.\d\d cięcie w głośnej mowie \(-\d+ dB, koniec ujęcia, źródło 7\.27s\)$")

    def test_no_key_or_false_is_the_old_build(self):
        base = self.d / "base"; base.mkdir()
        shutil.copy(BASELINE / "build_edl.py.orig", base / "build_edl.py")
        for keeps in ([[3, 4]], [[0, 1, {"t1": 2.6}]], [[3, 4], [6, 6]], [[0, 1], [3, 6]], [[0, 3], [1, 2]]):
            old_r, old = self.build(keeps, script=base)
            for r, new in (self.build(keeps), self.build(keeps, edge_fix=False)):
                self.assertEqual((new, r.stdout), (old, old_r.stdout), keeps)
        self.assertEqual(self.critic(), [])  # edge_fix false: no C12/C13 either (only with --edges)

    def test_theme_colors_the_captions_and_nothing_else_without_it(self):
        base = self.d / "base"; base.mkdir()
        shutil.copy(BASELINE / "build_edl.py.orig", base / "build_edl.py")
        keeps = [[0, 1], [3, 6]]
        self.build(keeps, script=base, ass_out="old.ass", hook_text="Hak")
        _, old_segs = self.build(keeps, ass_out="new.ass", hook_text="Hak")
        self.assertEqual((self.d / "new.ass").read_text(), (self.d / "old.ass").read_text())  # no theme.json: same captions
        self.assertNotIn("theme", json.loads((self.d / "edl.json").read_text()))
        (self.d / "theme.json").write_text(json.dumps({"theme": "cetus"}))
        _, segs = self.build(keeps, ass_out="new.ass", hook_text="Hak")
        ass = (self.d / "new.ass").read_text()
        self.assertIn("Style: Hook,Montserrat,84,&H00FFFFFF,&H00FFFFFF,&H0030140A,&H00000000,", ass)  # hook box (OutlineColour) in --ink #0A1430
        self.assertIn("{\\c&HECA76F&}", ass)  # spoken word in the Cetus accent #6FA7EC
        self.assertNotIn("&HC4E22D", ass)
        self.assertEqual(json.loads((self.d / "edl.json").read_text())["theme"], {"name": "cetus", "acc": "#6FA7EC", "ink": "#0A1430"})
        self.assertEqual(segs, old_segs)  # the cut itself never depends on the theme

    def test_short_tail_only_when_the_plan_asks(self):
        base = self.d / "base"; base.mkdir()
        shutil.copy(BASELINE / "build_edl.py.orig", base / "build_edl.py")
        (self.d / "specs.json").write_text(json.dumps([{"id": "v_end", "type": "end", "canvas": "v", "text": "x", "dur": 3}]))
        short = {"vertical": True, "ass_out": "s.ass", "gfx_specs": "specs.json", "gfx_dir": "g"}
        edl = lambda: json.loads((self.d / "edl.json").read_text())
        self.build([[0, 1], [3, 6]], script=base, **short); old = edl()
        self.build([[0, 1], [3, 6]], **short)
        self.assertEqual(edl(), old)  # no "tail" in the plan: a short as before (tail 0)
        self.build([[0, 1], [3, 6]], **short, tail=3, gfx=[{"id": "v_end", "word": "END"}])
        new = edl(); body = sum(s["out"] - s["in"] for s in new["segments"])
        self.assertEqual(new["tail"], 3)
        self.assertEqual(new["overlays"][0]["at"], round(body, 3))  # the end card starts when the speech ends

    def test_caption_box_and_pill_card_change_only_the_captions(self):
        base = self.d / "base"; base.mkdir()
        shutil.copy(BASELINE / "build_edl.py.orig", base / "build_edl.py")
        keeps = [[0, 1], [3, 6]]
        (self.d / "specs.json").write_text(json.dumps([{"id": "v_hook", "type": "maxim", "canvas": "v", "pill": True, "text": "x", "dur": 1.0}]))
        short = {"vertical": True, "gfx_specs": "specs.json", "gfx_dir": "g"}
        _, old_segs = self.build(keeps, script=base, ass_out="old.ass", **short)
        self.build(keeps, ass_out="new.ass", caption_box=False, **short)
        self.assertEqual((self.d / "new.ass").read_text(), (self.d / "old.ass").read_text())  # no pill on screen, box off: as before
        self.build(keeps, ass_out="box.ass", caption_box=True, **short)
        old, box = (self.d / "old.ass").read_text().splitlines(), (self.d / "box.ass").read_text().splitlines()
        self.assertEqual([i for i, (a, b) in enumerate(zip(old, box, strict=True)) if a != b], [old.index(next(x for x in old if x.startswith("Style: Cap")))])
        self.assertIn(",&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,16,0,2,70,70,650,1", box[old.index(next(x for x in old if x.startswith("Style: Cap")))])
        cap = lambda f: [ln.split(",")[1:3] for ln in (self.d / f).read_text().splitlines() if ln.startswith("Dialogue: 0,")]
        self.assertLess(cap("old.ass")[0][0], "0:00:01.00")  # "Raz" starts under where the pill will be
        _, pill_segs = self.build(keeps, ass_out="pill.ass", gfx=[{"id": "v_hook", "word": 0}], **short)
        self.assertEqual(pill_segs, old_segs)  # the cut never depends on captions
        lines = cap("pill.ass")
        self.assertTrue(all(st >= "0:00:01.00" for st, _ in lines), lines)  # nothing starts under the 1 s pill
        self.assertEqual(len(lines), len(cap("old.ass")) - sum(st < "0:00:01.00" for st, _ in cap("old.ass")))
        (self.d / "specs.json").write_text(json.dumps([{"id": "v_hook", "type": "maxim", "canvas": "v", "pill": True, "text": "x", "dur": 0.7}]))
        self.build(keeps, ass_out="short_pill.ass", gfx=[{"id": "v_hook", "word": 0}], **short)
        old_lines = cap("old.ass")
        self.assertEqual(old_lines[1], ["0:00:00.68", "0:00:01.25"])  # fixture: the 2nd line is still on when a 0.7 s pill leaves (0.78)
        self.assertEqual(cap("short_pill.ass"), [["0:00:00.78", "0:00:01.25"]] + old_lines[2:])  # shown from the pill's exit: no blank gap
        (self.d / "specs.json").write_text(json.dumps([{"id": "v_hook", "type": "maxim", "canvas": "v", "pill": True, "text": "x", "dur": 1.0}]))
        for bad in ("yes", 1):
            r = self.run_build(keeps, ass_out="x.ass", caption_box=bad, **short)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("caption_box: true/false", r.stderr)
        for spec in ({"pill": "false"}, {"pill": True, "canvas": None}, {"pill": True, "type": "kw"}):
            sp = {k: v for k, v in {"id": "v_hook", "type": "maxim", "canvas": "v", "text": "x", "dur": 1.0, **spec}.items() if v is not None}
            (self.d / "specs.json").write_text(json.dumps([sp]))
            r = self.run_build(keeps, ass_out="x.ass", gfx=[{"id": "v_hook", "word": 0}], **short)
            self.assertNotEqual(r.returncode, 0, spec)
            self.assertIn("v_hook: pill = true/false, only on a maxim", r.stderr)

    def test_graphics_without_their_files_stop_with_a_named_error(self):
        (self.d / "specs.json").write_text(json.dumps([{"id": "v_end", "type": "end", "canvas": "v", "text": "x", "dur": 3}]))
        for extra, msg in (({}, 'needs "gfx_specs"'), ({"gfx_specs": "specs.json"}, 'and "gfx_dir"'),
                           ({"gfx_specs": "specs.json", "gfx_dir": "g", "gfx": [{"id": "nope", "word": 0}]}, "'nope': no such card")):
            r = self.run_build([[0, 1]], **{"gfx": [{"id": "v_end", "word": "END"}], **extra})
            self.assertNotEqual(r.returncode, 0, extra)
            self.assertIn(msg, r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_overlapping_keeps_never_replay_a_computed_start_and_never_invert(self):
        _, segs = self.build([[0, 1], [1, 2]], edge_fix=True)  # next keep repeats "dwa": its start moves to where the first ended
        self.assertEqual(segs[1]["in"], segs[0]["out"])
        _, segs = self.build([[0, 1], [1, 2, {"t0": 1.2}]], edge_fix=True)  # the editor's explicit t0 stays, even replaying
        self.assertEqual(segs[1]["in"], 1.2)
        r = self.run_build([[0, 3], [1, 2]], edge_fix=True)  # a keep inside the previous one: a named error, never in > out
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("lies inside the previous keep", r.stderr)
        r = self.run_build([[0, 1, {"t0": 5.0, "t1": 4.0}]], edge_fix=True)  # the editor's own inverted values: named as such
        self.assertIn("check its t0/t1", r.stderr)

    def test_bad_values_and_a_missing_or_unreadable_wav_are_named(self):
        for bad in ("yes", {"win": 0}, {"loud_db": "x"}, {"dips": "false"}):
            r = self.run_build([[3, 4]], edge_fix=bad)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("edge_fix", r.stderr)
        with wave.open(str(self.d / "rec.wav"), "wb") as w:  # 24-bit: build stops by name, the critic still reports the rest
            w.setnchannels(1); w.setsampwidth(3); w.setframerate(16000); w.writeframes(b"\0" * 3 * 16000)
        r = self.run_build([[3, 4]], edge_fix={"dips": True})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("must be 16-bit PCM", r.stderr)
        self.build([[3, 4]])
        found = self.critic("--edges")
        self.assertIn("C12", [c for c, _ in found])
        self.assertTrue(any(c == "C13" and "nieczytelny" in m for c, m in found), found)
        with wave.open(str(self.d / "rec.wav"), "wb") as w:  # header for 1 s, the file cut short: never read as silence
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0" * 2 * 16000)
        (self.d / "rec.wav").write_bytes((self.d / "rec.wav").read_bytes()[:-4000])
        r = self.run_build([[3, 4]], edge_fix={"dips": True})
        self.assertIn("shorter than its header says", r.stderr)
        (self.d / "rec.wav").unlink()
        r = self.run_build([[3, 4]], edge_fix={"dips": True})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("edge_fix.dips: no 16-bit wav", r.stderr)
        self.build([[3, 4]], edge_fix=True)
        self.assertIn(("C13", "brak wav nagrania: cięcia w głośnej mowie niesprawdzone"), self.critic())


# no sample project = these tests FAIL (they are the "without beats nothing changed" guarantee); a machine that
# really has no sample opts out explicitly with MONTAZ_NO_SAMPLE=1
@unittest.skipIf(os.environ.get("MONTAZ_NO_SAMPLE") == "1", "MONTAZ_NO_SAMPLE=1: compatibility with the old scripts not checked")
class CompatTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SAMPLE.is_dir(), f"needs the sample project {SAMPLE} (or MONTAZ_NO_SAMPLE=1 to skip on purpose)")
        # the baseline IS the guarantee: without it these tests must fail loudly, never pass by skipping
        origs = sorted(BASELINE.glob("*.py.orig"))
        self.assertEqual([o.name for o in origs], ["assemble.py.orig", "build_edl.py.orig", "critic.py.orig", "face_track.py.orig", "make_beds.py.orig"])
        self.tmp = Path(tempfile.mkdtemp(prefix="montaz-compat-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = self.tmp / "baseline"  # stored as .orig so linters skip the old code; run as .py from here
        self.base.mkdir()
        for o in origs:
            shutil.copy(o, self.base / o.name.removesuffix(".orig"))
        self.work = self.tmp / "work"
        (self.tmp / "out").mkdir()
        self.work.mkdir()
        plan = json.loads((SAMPLE / f"plan_{FILM}.json").read_text())
        for k in ("words", "sil"):
            shutil.copy(SAMPLE / plan[k], self.work / plan[k])
        if plan.get("gfx_specs"):
            shutil.copy(SAMPLE / plan["gfx_specs"], self.work / plan["gfx_specs"])
        if plan.get("ass_out"):
            plan["ass_out"] = str(self.work / Path(plan["ass_out"]).name)
        self.plan = plan

    def write_plan(self, plan, film=FILM):
        (self.work / f"plan_{film}.json").write_text(json.dumps(plan, ensure_ascii=False))

    def run_script(self, root, script, *args, env=None):
        return subprocess.run([sys.executable, str(root / script), *args], cwd=self.work, capture_output=True, text=True,
                              env=env, check=False)

    def edl_from(self, root):
        r = self.run_script(root, "build_edl.py", f"plan_{FILM}.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.work / self.plan["edl_out"]).read_text(), r.stdout

    def test_build_edl_unchanged_without_panel_keys(self):
        self.write_plan(self.plan)
        self.assertEqual(self.edl_from(self.base), self.edl_from(PIPE))

    def test_build_edl_applies_segment_zoom_and_mix_levels(self):
        self.write_plan(self.plan)
        first_in = json.loads(self.edl_from(PIPE)[0])["segments"][0]["in"]
        self.write_plan({**self.plan, "seg_zoom": {f"{first_in:.2f}": 1.05}, "music_db": -18, "sfx_db": -20,
                         "audio": "highpass=f=70,loudnorm=I=-14", "steady_x": True})
        edl = json.loads(self.edl_from(PIPE)[0])
        self.assertEqual(edl["segments"][0]["zoom"], 1.05)
        self.assertEqual((edl["music_db"], edl["sfx_db"]), (-18, -20))
        self.assertEqual((edl["audio"], edl["steady_x"]), ("highpass=f=70,loudnorm=I=-14", True))  # for assemble.py / face_track.py

    def test_critic_unchanged_and_accept_hides_finding(self):
        self.write_plan(self.plan)
        self.edl_from(PIPE)
        old = self.run_script(self.base, "critic.py", f"plan_{FILM}.json")
        new = self.run_script(PIPE, "critic.py", f"plan_{FILM}.json")
        self.assertEqual((old.stdout, old.returncode), (new.stdout, new.returncode))
        out = self.tmp / "c.json"
        self.run_script(PIPE, "critic.py", f"plan_{FILM}.json", "--json", str(out))
        rep = json.loads(out.read_text())
        if not rep["issues"]:
            self.skipTest("sample film has no findings to accept")
        first = rep["issues"][0]
        self.write_plan({**self.plan, "critic_accept": [{"code": first["code"], "msg": first["msg"], "reason": "test"}]})
        self.run_script(PIPE, "critic.py", f"plan_{FILM}.json", "--json", str(out))
        rep2 = json.loads(out.read_text())
        self.assertNotIn((first["code"], first["msg"]), {(i["code"], i["msg"]) for i in rep2["issues"]})
        self.assertEqual(len(rep2["accepted"]), 1)

    def test_accepted_c6_is_not_auto_fixed(self):
        long = json.loads((SAMPLE / "plan_long.json").read_text())
        for k in ("words", "sil", "gfx_specs"):
            if long.get(k):
                shutil.copy(SAMPLE / long[k], self.work / long[k])
        long = {**long, "tail": 0, "srt_out": None, "ass_out": None}
        msg = "brak min. 5 s ekranu końcowego"
        self.write_plan(long, "long")
        self.assertEqual(self.run_script(PIPE, "build_edl.py", "plan_long.json").returncode, 0)
        self.run_script(PIPE, "critic.py", "plan_long.json", "--fix")
        self.assertEqual(json.loads((self.work / "plan_long.json").read_text())["tail"], 8)  # not accepted: fixed
        self.write_plan({**long, "critic_accept": [{"code": "C6", "msg": msg, "reason": "test"}]}, "long")
        self.run_script(PIPE, "critic.py", "plan_long.json", "--fix")
        self.assertEqual(json.loads((self.work / "plan_long.json").read_text())["tail"], 0)  # accepted: left alone

    def beds(self, script, edl, name):
        edl_p = self.tmp / f"edl-{name}.json"
        edl_p.write_text(json.dumps(edl))
        out = self.tmp / f"beds-{name}"
        py = PIPE / ".venv" / "bin" / "python"  # make_beds needs NumPy: the pipeline venv, as in run_v4.sh
        r = subprocess.run([str(py), str(script), str(edl_p), str(out)], cwd=self.work, capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (out / "sfx.wav").read_bytes()

    def test_make_beds_unchanged_without_beat_fx(self):
        shutil.copy(PIPE / "music" / "gen_music.py", self.base / "gen_music.py")  # make_beds calls it from its own dir
        edl = json.loads((SAMPLE / f"edl_{FILM}.json").read_text())
        edl = {k: v for k, v in edl.items() if k != "beat_fx"}
        self.assertEqual(self.beds(self.base / "make_beds.py", edl, "old"), self.beds(PIPE / "music" / "make_beds.py", edl, "new"))

    def test_beat_fx_silences_punches_unless_the_moment_has_a_sound(self):
        punch = {"src": "A", "in": 0.0, "out": 4.0, "zoom": {"a": 1.0, "b": 1.2, "t0": 1.0, "t1": 1.3}}
        edl = {"segments": [punch], "tail": 0}
        mk = PIPE / "music" / "make_beds.py"
        self.assertTrue(any(self.beds(mk, edl, "plain")[44:]))  # old behaviour: whoosh on every punch
        self.assertFalse(any(self.beds(mk, {**edl, "beat_fx": [{"at": 1.0, "fx": "punch", "sfx": None}]}, "silent")[44:]))
        self.assertTrue(any(self.beds(mk, {**edl, "beat_fx": [{"at": 1.0, "fx": "punch", "sfx": "ding"}]}, "ding")[44:]))

    def test_card_sfx_key_sounds_at_its_moments_from_the_card_start(self):
        specs = self.tmp / "specs-sfx.json"
        specs.write_text(json.dumps([{"id": "n", "type": "note", "sfx": [[1.0, "pen"], [2.0, "nosuch"]]}]))
        edl = {"segments": [{"src": "A", "in": 0.0, "out": 4.0, "zoom": 1.0}], "tail": 0, "gfx_specs": str(specs),
               "overlays": [{"id": "n", "at": 0.5, "dur": 3.0}]}
        pcm = self.beds(PIPE / "music" / "make_beds.py", edl, "card-sfx")[44:]
        frame = 4  # 16-bit stereo
        loud = [i // frame / 44100 for i in range(0, len(pcm), frame) if pcm[i:i + 2] != b"\0\0"]
        self.assertTrue(loud)
        self.assertAlmostEqual(loud[0], 1.5, delta=0.02)  # card at 0.5 s + its moment at 1.0 s; unknown names stay silent
        self.assertLess(loud[-1], 2.0)
        specs.write_text(json.dumps([{"id": "n", "type": "note"}]))
        self.assertFalse(any(self.beds(PIPE / "music" / "make_beds.py", edl, "card-no-sfx")[44:]))

    def test_beats_key_drives_punches_and_critic_never_adds_random_ones(self):
        self.write_plan({**self.plan, "emph": [*self.plan.get("emph", []), {"word": 1300, "amt": 0.1, "auto": True}]})
        r = self.run_script(PIPE, "beats.py", f"plan_{FILM}.json", "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        edl = json.loads(self.edl_from(PIPE)[0])
        rows = edl["beat_fx"]
        self.assertTrue(rows and all(r["fx"] in {"punch", "spacing", "quiet", "none", "box", "gfx", "cut", "off", "manual", "unknown"} for r in rows))
        punches = [s for s in edl["segments"] if isinstance(s["zoom"], dict) and s["zoom"]["t0"] > 0.01]
        manual = [e for e in self.plan.get("emph", []) if not e.get("auto")]
        self.assertLessEqual(len(punches), sum(r["fx"] == "punch" for r in rows) + len(manual))
        self.run_script(PIPE, "critic.py", f"plan_{FILM}.json", "--fix")
        plan = json.loads((self.work / f"plan_{FILM}.json").read_text())
        self.assertFalse([e for e in plan.get("emph", []) if e.get("auto")])

    def critic_issues(self, specs, gfx, **extra):
        (self.work / "specs.json").write_text(json.dumps(specs))
        self.write_plan({**self.plan, "gfx_specs": "specs.json", "gfx_dir": str(self.work / "gfx"), "gfx": gfx, **extra})
        self.edl_from(PIPE)
        r = self.run_script(PIPE, "critic.py", f"plan_{FILM}.json", "--json", str(self.tmp / "c.json"))
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return json.loads((self.tmp / "c.json").read_text())["issues"]

    def test_critic_quote_card_numbers_must_be_said_while_it_is_on_screen(self):
        card = {"type": "kw", "sub": "ekranów", "dur": 2.5, "icon": "hash", "quote": True, "canvas": "v"}
        specs = [{**card, "id": "said", "text": "~60"}, {**card, "id": "elsewhere", "text": "40"}]
        # "60" is spoken at word 1151 (~5 s); "40" only at word 1286 (~38 s), so a 40 card at 5 s quotes nothing
        issues = self.critic_issues(specs, [{"id": "said", "word": 1151, "lead": 0.0}, {"id": "elsewhere", "word": 1158, "lead": 0.0}],
                                    hook_text="")
        c9 = {i["msg"].split()[1].rstrip(":"): i["sev"] for i in issues if i["code"] == "C9"}
        self.assertEqual(c9, {"said": "info", "elsewhere": "error"})

    def test_web_card_is_full_screen_with_the_face_in_the_corner(self):
        specs = [{"id": "w1", "type": "web", "dur": 6.0, "url": "https://example.org/", "find": "przykładowe zdanie ze strony"}]
        issues = self.critic_issues(specs, [{"id": "w1", "word": 1151, "lead": 0.0}])
        edl = json.loads(self.edl_from(PIPE)[0])
        self.assertEqual([(o["id"], o["pip"]) for o in edl["overlays"]], [("w1", True)])
        self.assertFalse([i for i in issues if i["code"] == "C2" and "w1" in i["msg"] and i["sev"] == "error"], issues)

    def test_count_card_is_a_side_box_like_kw(self):
        # a 16:9 count card covers the face side like kw: build_edl skips an emphasis punch-in under it (gfx_types.SIDE_BOX)
        for ty, skipped in (("kw", True), ("count", True), ("lower", False)):
            with self.subTest(type=ty):
                (self.work / "specs.json").write_text(json.dumps([{"id": "c1", "type": ty, "text": "95%", "sub": "firm", "dur": 3.0}]))
                self.write_plan({**self.plan, "gfx_specs": "specs.json", "gfx_dir": str(self.work / "gfx"),
                                 "gfx": [{"id": "c1", "word": 1151, "lead": 0.0}], "emph": [{"word": 1152}]})
                self.assertEqual("emph skipped (box on screen)" in self.edl_from(PIPE)[1], skipped)

    def test_critic_vertical_card_under_the_hook_title_is_an_error(self):
        specs = [{"id": "early", "type": "kw", "text": "Start", "sub": "", "dur": 2.0, "icon": "hash", "canvas": "v"}]
        issues = self.critic_issues(specs, [{"id": "early", "word": 1145, "lead": 0.0}])  # hook_text shows for 3 s
        self.assertTrue([i for i in issues if i["code"] == "C2" and "early" in i["msg"] and i["sev"] == "error"], issues)

    def test_critic_c10_moment_without_its_graphic(self):
        self.write_plan(self.plan)
        self.assertEqual(self.run_script(PIPE, "beats.py", f"plan_{FILM}.json", "--write").returncode, 0)
        beats = json.loads((self.work / f"plan_{FILM}.json").read_text())["beats"]
        issues = self.critic_issues([], [], beats=beats)
        c10 = [i for i in issues if i["code"] == "C10"]
        self.assertTrue(any("sugerowana grafika" in i["msg"] for i in c10), c10)  # e.g. the closing maxim asks for a kw card
        self.assertTrue(all(i["sev"] == "info" for i in c10))

    def test_beats_cli_without_cards_leaves_gfx_keys_alone(self):
        plan = {k: v for k, v in self.plan.items() if k not in ("gfx_specs", "gfx_dir")}
        plan["keeps"] = [[1141, 1149]]  # the first phrase: no number, so no card
        self.write_plan(plan)
        r = self.run_script(PIPE, "beats.py", f"plan_{FILM}.json", "--gfx", "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        saved = json.loads((self.work / f"plan_{FILM}.json").read_text())
        self.assertNotIn("gfx_specs", saved)  # pointing at a file that was never written would break build_edl
        self.assertTrue(saved["beats"])
        edl = json.loads(self.edl_from(PIPE)[0])
        self.assertEqual(sorted(edl["beat_grammar"]), ["sha", "version"])

    def test_face_track_unchanged_without_beat_fx(self):
        edl = json.loads((SAMPLE / f"edl_{FILM}.json").read_text())
        edl = {k: v for k, v in edl.items() if k != "beat_fx"}
        for s in edl["segments"]:  # as build_edl wrote it: face_track adds these
            for k in ("face", "head", "zmax", "track"):
                s.pop(k, None)
        py = PIPE / ".venv" / "bin" / "python"
        out = []
        for root, name in ((self.base, "old"), (PIPE, "new")):
            d = self.tmp / f"ft-{name}"
            d.mkdir()
            shutil.copy(SAMPLE / "faces_cache.json", d / "faces_cache.json")  # same cache: no video decoding differences
            (d / "edl.json").write_text(json.dumps(edl))
            r = subprocess.run([str(py), str(root / "face_track.py"), str(d / "edl.json")], capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            out.append((d / "edl.json").read_text())
        self.assertEqual(out[0], out[1])

    def test_steady_x_stops_sideways_jumps_on_cuts_without_losing_zoom(self):
        edl = json.loads((SAMPLE / "edl_long.json").read_text())  # 16:9: the crop follows each shot's face x
        for s in edl["segments"]:
            for k in ("face", "head", "zmax"):
                s.pop(k, None)
        py = PIPE / ".venv" / "bin" / "python"
        res = {}
        for name, extra in (("plain", {}), ("steady", {"steady_x": True})):
            d = self.tmp / f"ft-{name}"
            d.mkdir()
            shutil.copy(SAMPLE / "faces_cache.json", d / "faces_cache.json")
            (d / "edl.json").write_text(json.dumps({**edl, **extra}))
            r = subprocess.run([str(py), str(PIPE / "face_track.py"), str(d / "edl.json")], capture_output=True, text=True, check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            res[name] = json.loads((d / "edl.json").read_text())["segments"]
        jumps = {n: sum(abs(b["face"][0] - a["face"][0]) for a, b in itertools.pairwise(segs)) for n, segs in res.items()}
        self.assertLess(jumps["steady"], jumps["plain"] / 2)  # the aim moves far less from shot to shot
        def peak(z):  # a dict zoom: its highest point
            return z if not isinstance(z, dict) else max([z["a"], z["b"], *(v for _, v in z.get("keys", []))])
        for st, pl in zip(res["steady"], res["plain"]):  # no shot zooms less
            self.assertGreaterEqual(peak(st["zoom"]), peak(pl["zoom"]) - 1e-9)

    def test_face_track_keeps_beat_punches_visible_under_the_head_limit(self):
        edl = json.loads((SAMPLE / f"edl_{FILM}.json").read_text())
        edl["beat_fx"] = []
        segs = edl["segments"]
        segs[1]["zoom"] = {"a": 1.3, "b": 1.38, "t0": 0.0, "t1": 0.2}  # punch on the shot's first word, far over any zmax
        segs[2]["zoom"] = {"a": 1.3, "b": 1.38, "t0": 0.0, "t1": round(segs[2]["out"] - segs[2]["in"], 3)}  # slow drift
        d = self.tmp / "ft-beats"
        d.mkdir()
        shutil.copy(SAMPLE / "faces_cache.json", d / "faces_cache.json")
        (d / "edl.json").write_text(json.dumps(edl))
        py = PIPE / ".venv" / "bin" / "python"
        r = subprocess.run([str(py), str(PIPE / "face_track.py"), str(d / "edl.json")], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        punch, drift = json.loads((d / "edl.json").read_text())["segments"][1:3]
        self.assertIsInstance(punch["zoom"], dict)
        self.assertGreater(punch["zoom"]["b"] - punch["zoom"]["a"], 0.005)  # still a move, not flattened
        self.assertLessEqual(punch["zoom"]["b"], punch["zmax"])
        self.assertEqual(drift["zoom"], drift["zmax"])  # drift clamped (flattened) as before

    def test_zoom_keyframes_render_several_moves_in_one_shot(self):
        keys = [[0, 1.0], [0.2, 1.0], [0.4, 1.2], [0.966, 1.2], [1.0, 1.0], [1.3, 1.3]]
        edl = {"sources": {"A": "src.mp4"}, "segments": [{"src": "A", "in": 0.0, "out": 2.0,
               "zoom": {"a": 1.0, "b": 1.3, "t0": 0.2, "t1": 0.4, "keys": keys}}], "overlays": [], "w": 640, "h": 360,
               "src_w": 640, "src_h": 360, "out_name": "x.mp4"}
        p = self.tmp / "edl_keys.json"
        p.write_text(json.dumps(edl))
        cut = next(json.loads(c) for c in self.ffmpeg_calls(PIPE, p) if "-vf" in json.loads(c))
        self.assertIn("_k", cut[-1])  # the segment cache name follows the keyframes
        vf = cut[cut.index("-vf") + 1]
        frames = self.tmp / "kf"
        frames.mkdir()
        r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=2,drawgrid=w=40:h=40:t=2:c=white",
                            "-vf", vf, "-frames:v", "60", str(frames / "f%02d.png")], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        f = {n: (frames / f"f{n:02d}.png").read_bytes() for n in (1, 5, 15, 25, 55, 60)}
        self.assertEqual(f[1], f[5])  # wide before the first moment
        self.assertNotEqual(f[15], f[1])  # first punch
        self.assertEqual(f[15], f[25])  # held
        self.assertNotIn(f[55], (f[1], f[25]))  # second punch, after the snap back
        self.assertEqual(f[55], f[60])

    def broll_edl(self, items, **extra):
        """EDL of the sample with plan["broll"]; clips are looked up in a temp cache (VP_BROLL_DIR), never render/broll."""
        self.write_plan({**self.plan, "broll": items, **extra})
        r = self.run_script(PIPE, "build_edl.py", f"plan_{FILM}.json", env={**os.environ, "VP_BROLL_DIR": str(self.cache)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.work / self.plan["edl_out"]).read_text())

    def broll_setup(self):
        self.cache = self.tmp / "broll"
        self.cache.mkdir()
        self.clip = self.cache / "pexels_1_1080x1920.mp4"
        self.clip.write_bytes(b"x")
        outside = self.tmp / "outside.mp4"
        outside.write_bytes(b"x")
        (self.cache / "link.mp4").symlink_to(outside)  # a link out of the cache is a file outside it
        self.write_plan(self.plan)
        segs = json.loads(self.edl_from(PIPE)[0])["segments"]
        words = json.loads((self.work / self.plan["words"]).read_text())["transcription"]
        starts = [w["offsets"]["from"] / 1000 for w in words]
        kept = [i for i, t in enumerate(starts) if any(s["in"] <= t <= s["out"] for s in segs)]
        cut = next(i for i in range(kept[0], kept[-1]) if i not in kept)
        return segs, starts, kept, cut, outside

    def test_broll_key_places_clips_and_drops_unsafe_or_colliding_ones(self):
        segs, starts, kept, cut, outside = self.broll_setup()
        total = sum(s["out"] - s["in"] for s in segs)
        def out_t(i):
            acc = 0.0
            for s in segs:
                if s["in"] <= starts[i] <= s["out"]:
                    return acc + starts[i] - s["in"]
                acc += s["out"] - s["in"]
            raise AssertionError(i)
        free, card = (next(i for i in kept if lo < out_t(i) < lo + 5) for lo in (8.0, 25.0))
        clip = {"file": str(self.clip), "dur": 2.5, "lead": 0.0, "query": "q", "clip_dur": 10.0}
        items = [{**clip, "word": free}, {**clip, "word": cut}, {**clip, "word": card},
                 {**clip, "word": free, "file": str(outside)}, {**clip, "word": free, "file": str(self.cache / "link.mp4")},
                 {**clip, "word": free, "dur": 99}, {"word": "1"}, "x"]
        (self.work / "specs.json").write_text(json.dumps([{"id": "k1", "type": "kw", "dur": 3.0, "canvas": "v"}]))
        edl = self.broll_edl(items, gfx_specs="specs.json", gfx_dir=str(self.work / "gfx"), gfx=[{"id": "k1", "word": card}])
        self.assertEqual(edl["broll"], [{"file": str(self.clip), "at": round(out_t(free), 3), "dur": 2.5, "ss": 1.0, "word": free,
                                         "query": "q", "id": f"broll_{free}"}])
        self.assertLess(edl["broll"][0]["at"] + 2.5, total)
        reasons = sorted(s["reason"] for s in edl["broll_skipped"])
        self.assertEqual(reasons, sorted(["słowo wycięte", "pod grafiką", "brak pliku b-rollu w render/broll",
                                          "brak pliku b-rollu w render/broll", "zły czas b-rollu", "zły wpis b-rollu", "zły wpis b-rollu"]))
        r = self.run_script(PIPE, "critic.py", f"plan_{FILM}.json", "--json", str(self.tmp / "c.json"))
        self.assertIn(r.returncode, (0, 1), r.stderr)
        self.assertEqual(edl["broll_removed"], 0)
        c11 = [i for i in json.loads((self.tmp / "c.json").read_text())["issues"] if i["code"] == "C11"]
        self.assertEqual(len(c11), len(reasons))
        self.assertTrue(all(i["sev"] == "warn" for i in c11))
        said = [s.get("said") for s in edl["broll_skipped"] if isinstance(s.get("word"), int) and s["reason"] != "zły wpis b-rollu"]
        self.assertTrue(said and all(said), edl["broll_skipped"])  # the editor sees the spoken word, not its index
        self.assertTrue(any("„" in i["msg"] for i in c11))

    def test_broll_key_of_any_shape_never_stops_the_build(self):
        self.broll_setup()
        for odd in (None, 5, "x"):
            with self.subTest(broll=odd):
                edl = self.broll_edl(odd, broll_rejected=None)
                self.assertEqual((edl["broll"], edl["broll_removed"]), ([], 0))
                self.assertTrue(edl["broll_skipped"])  # the critic (C11) tells the editor
        r = self.run_script(PIPE, "critic.py", f"plan_{FILM}.json", "--json", str(self.tmp / "c.json"))
        self.assertIn(r.returncode, (0, 1), r.stderr)
        c11 = [i["msg"] for i in json.loads((self.tmp / "c.json").read_text())["issues"] if i["code"] == "C11"]
        self.assertEqual(c11, ["b-roll pominięty: zły klucz broll w planie (nie lista)"])
        edl = self.broll_edl([], broll_rejected=[{"word": 1, "provider": "pexels", "id": 2}, "junk", 3])
        self.assertEqual(edl["broll_removed"], 1)  # the ADR-0003 metric counts real removals only
        r = subprocess.run([sys.executable, str(PIPE / "beatfx.py"), str(self.work / self.plan["edl_out"])], capture_output=True,
                           text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        row = json.loads(r.stdout.split(" ", 3)[3])
        self.assertEqual((row["broll_clips"], row["broll_share"], row["broll_removed"]), (0, 0.0, 1))

    def test_broll_renders_under_graphics_and_captions_without_moving_other_inputs(self):
        kept = self.broll_setup()[2]
        edl = self.broll_edl([{"file": str(self.clip), "dur": 2.5, "word": kept[len(kept) // 2], "query": "q"}])
        b = edl["broll"][0]
        self.assertEqual(b["ss"], 0.0)  # clip length unknown: play it from its start
        p = self.tmp / "edl_b.json"
        p.write_text(json.dumps(edl))
        final = json.loads(self.ffmpeg_calls(PIPE, p)[-1])
        i = final.index(str(self.clip))
        self.assertEqual(final[i - 7:i], ["-protocol_whitelist", "file", "-f", "mov", "-t", "2.500", "-i"])  # downloaded file: MP4 only
        fc = final[final.index("-filter_complex") + 1]
        n_in = final.count("-i")
        self.assertIn(f"[{n_in - 1}:v]trim=start=0.000:duration=2.500,setpts=PTS-STARTPTS+{b['at']:.3f}/TB,"
                      "scale=1080:1920:force_original_aspect_ratio=increase", fc)  # short: no music in the sample EDL
        self.assertLess(fc.index("[vb0]"), fc.index("subtitles="))
        long = json.loads((SAMPLE / "edl_long.json").read_text())
        n_ov = len(long["overlays"])
        p2 = self.tmp / "edl_long_b.json"
        p2.write_text(json.dumps({**long, "broll": [{**b, "file": str(self.clip)}]}))
        fc2 = json.loads(self.ffmpeg_calls(PIPE, p2)[-1])
        fc2 = fc2[fc2.index("-filter_complex") + 1]
        self.assertIn(f"[{1 + n_ov}:v]format=gray", fc2)  # pip mask keeps its input number
        self.assertRegex(fc2, rf"\[{3 + n_ov}:v\]trim=[^;]*scale={long['w']}:{long['h']}:force_original_aspect_ratio=increase")
        self.assertLess(fc2.index("[vb0]"), fc2.index("[g0]overlay"))

    def ffmpeg_calls(self, root, edl_path, *flags):
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        fake = bindir / "ffmpeg"
        fake.write_text(FAKE_FFMPEG)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        log = self.tmp / f"ff-{root.name}-{edl_path.stem}-{len(flags)}.log"
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_LOG": str(log)}
        outdir = self.tmp / f"render-{root.name}-{edl_path.stem}-{len(flags)}"
        r = self.run_script(root, "assemble.py", str(edl_path), str(outdir), *flags, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [line.replace(str(outdir), "OUT") for line in log.read_text().splitlines()]

    def test_assemble_full_render_commands_identical(self):
        for film in (FILM, "long"):  # vertical short with subtitles; long with graphics, PiP slides, music
            with self.subTest(film=film):
                self.assert_same_render(film)

    def assert_same_render(self, film):
        edl = json.loads((SAMPLE / f"edl_{film}.json").read_text())
        p = self.tmp / f"edl_{film}.json"
        p.write_text(json.dumps(edl))
        # segments are cut by 6 threads: same commands, any order; the final mix is the last call in both
        old, new = self.ffmpeg_calls(self.base, p), self.ffmpeg_calls(PIPE, p)
        self.assertEqual(sorted(old), sorted(new))
        self.assertEqual(old[-1], new[-1])

    def test_assemble_proxy_is_fast_and_small(self):
        edl = json.loads((SAMPLE / f"edl_{FILM}.json").read_text())
        p = self.tmp / "edl.json"
        p.write_text(json.dumps({**edl, "music_offset": 12.5}))
        calls = self.ffmpeg_calls(PIPE, p, "--proxy")
        final = json.loads(calls[-1])
        self.assertIn("ultrafast", final)
        self.assertIn("scale=360:640", final[final.index("-filter_complex") + 1])
        self.assertEqual(final.count("12.500"), 2)  # music and sfx both start at the slice
        self.assertTrue(all("ultrafast" in json.loads(c) for c in calls if "-ss" in json.loads(c)[:4]))


if __name__ == "__main__":
    unittest.main()

"""The panel's pipeline additions must not change anything when their keys/flags are absent.

Runs the scripts as they were before the panel (tests/pipeline_baseline/*.py.orig, kept in this repo because
~/video-pipeline itself is not in git) and the current scripts side by side on a copy of the sample project,
in a temp dir (never in ~/Videos: absolute output paths are rewritten).
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
        self.write_plan({**self.plan, "seg_zoom": {f"{first_in:.2f}": 1.05}, "music_db": -18, "sfx_db": -20})
        edl = json.loads(self.edl_from(PIPE)[0])
        self.assertEqual(edl["segments"][0]["zoom"], 1.05)
        self.assertEqual((edl["music_db"], edl["sfx_db"]), (-18, -20))

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

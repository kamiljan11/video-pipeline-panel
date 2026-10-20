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
BASELINE = Path(__file__).resolve().parent / "pipeline_baseline"  # build_edl.py, critic.py, assemble.py before the panel
SAMPLE = Path.home() / "Videos" / "Montaz" / "2026-10-20" / "work"
FILM = "short3_jedno_zlecenie"

FAKE_FFMPEG = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
open(sys.argv[-1], "wb").close()
"""


@unittest.skipUnless(SAMPLE.is_dir(), "needs the sample project ~/Videos/Montaz/2026-10-20")
class CompatTest(unittest.TestCase):
    def setUp(self):
        # the baseline IS the guarantee: without it these tests must fail loudly, never pass by skipping
        origs = sorted(BASELINE.glob("*.py.orig"))
        self.assertEqual([o.name for o in origs], ["assemble.py.orig", "build_edl.py.orig", "critic.py.orig"])
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

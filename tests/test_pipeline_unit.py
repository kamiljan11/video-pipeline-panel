"""pipeline.py without running the pipeline: render detection on real processes, battery, critic report steps, caches."""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest import mock

from montazownia import pipeline, store
from montazownia.jobs import Cmd, Job, Runner, StepFailed


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="montaz-unit-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        mock.patch.dict(os.environ, {"MONTAZ_PANEL_DIR": str(self.tmp / "panel")}).start()
        self.addCleanup(mock.patch.stopall)
        self.work = self.tmp / "work"
        self.work.mkdir()


class ForeignRenderTest(Tmp):
    def fake_render(self, work_arg: str, cwd: Path) -> subprocess.Popen:
        # argv looks like `bash run_v4.sh WORK OUT FILM` (bash -c names $0 run_v4.sh; "; true" stops bash exec-ing sleep)
        p = subprocess.Popen(["bash", "-c", "sleep 20; true", "run_v4.sh", work_arg, "out", "f"], cwd=cwd, start_new_session=True)
        self.addCleanup(lambda: (os.killpg(p.pid, signal.SIGKILL), p.wait()))  # the whole group: bash AND its sleep
        return p

    def wait_for(self, pids, own=frozenset()):
        for _ in range(50):
            found = pipeline.foreign_render(self.work, set(own))
            if set(pids) <= set(found):
                return found
            time.sleep(0.05)
        return pipeline.foreign_render(self.work, set(own))

    def test_absolute_relative_and_trailing_slash_are_found(self):
        ps = [self.fake_render(str(self.work), self.tmp), self.fake_render(".", self.work), self.fake_render(f"{self.work}/", self.tmp)]
        self.assertEqual(sorted(self.wait_for([p.pid for p in ps])), sorted(p.pid for p in ps))

    def test_other_project_and_own_jobs_are_ignored(self):
        other = self.tmp / "other"
        other.mkdir()
        self.fake_render(str(other), self.tmp)
        own = self.fake_render(str(self.work), self.tmp)
        time.sleep(0.2)
        self.assertEqual(pipeline.foreign_render(self.work, {os.getpgid(own.pid)}), [])


class BatteryTest(Tmp):
    def battery(self, capacity, status):
        bat = self.tmp / "BAT0"
        bat.mkdir(exist_ok=True)
        (bat / "capacity").write_text(f"{capacity}\n")
        (bat / "status").write_text(f"{status}\n")
        with mock.patch.object(pipeline, "BATTERY", bat):
            return pipeline.battery()

    def test_warns_only_when_low_and_not_charging(self):
        self.assertTrue(self.battery(30, "Discharging")["warn"])
        self.assertFalse(self.battery(30, "Charging")["warn"])
        self.assertFalse(self.battery(80, "Discharging")["warn"])
        self.assertTrue(self.battery(30, "Unknown")["warn"])  # unknown status: assume on battery

    def test_no_battery(self):
        with mock.patch.object(pipeline, "BATTERY", self.tmp / "none"):
            self.assertEqual(pipeline.battery(), {"percent": None, "charging": None, "warn": False})


class CriticStepsTest(Tmp):
    def test_stale_report_never_survives_a_crashed_critic(self):
        (self.work / "plan_f.json").write_text(json.dumps({"edl_out": "edl_f.json"}))
        steps = pipeline.check_steps(self.work, "f")
        drop_old, load = cast(Callable[[Job], None], steps[2]), cast(Callable[[Job], None], steps[4])
        report = pipeline.critic_report(self.work, "f")
        report.write_text('{"issues": ["old"]}')
        job = Job(1, "check", "f", steps)
        drop_old(job)
        with self.assertRaises(StepFailed):
            load(job)
        report.write_text('{"issues": []}')
        load(job)
        self.assertEqual(job.result["report"], {"issues": []})
        self.assertTrue(str(report).startswith(str(self.tmp / "panel")))


class PreviewMixTest(Tmp):
    def test_failing_make_beds_fails_the_job_with_its_name(self):
        (self.work / "plan_f.json").write_text(json.dumps({"edl_out": "edl_f.json"}))
        (self.work / "edl_f.json").write_text(json.dumps({"segments": []}))
        fake = self.tmp / "make_beds_fail.py"
        fake.write_text("import sys; print('brak pliku muzyki'); sys.exit(2)")
        real = pipeline._s
        with mock.patch.object(pipeline, "_s", lambda n: str(fake) if n == "music/make_beds.py" else real(n)):
            steps = pipeline.preview_steps(self.work, "f", 1.0)
        runner = Runner(self.tmp / "jobs")
        self.addCleanup(runner.shutdown)
        job = runner.submit("preview", "f", [s for s in steps if not (isinstance(s, Cmd) and "build_edl.py" in s.argv[1])
                                             and not (isinstance(s, Cmd) and "face_track.py" in s.argv[1])])
        for _ in range(100):
            if job.status not in ("queued", "running"):
                break
            time.sleep(0.05)
        self.assertEqual(job.status, "failed")
        self.assertIn("make_beds_fail.py zakończył się kodem 2", job.error)
        self.assertIn("brak pliku muzyki", "\n".join(job.lines))
        mix = next(s for s in steps if isinstance(s, Cmd) and s.argv[1] == str(fake))
        self.assertEqual(mix.timeout, 300)  # Stop and the limit reach it like any command


class UnfinishedRunTest(Tmp):
    def setUp(self):
        super().setUp()
        self.segs, self.gfx = self.tmp / "out" / "segs", self.work / "gfx"
        self.segs.mkdir(parents=True)
        self.gfx.mkdir()
        (self.work / "gfx_specs.json").write_text(json.dumps([{"id": "g1"}, {"id": "g2"}, {"id": "../x"}]))
        (self.gfx / "g1.mov").write_text("ok")  # rendered before: not part of the next run
        self.old = self.segs / "0000_old.mkv"
        self.old.write_text("ok")
        os.utime(self.old, (time.time() - 60, time.time() - 60))
        self.marker = self.tmp / "m" / "unfinished.json"
        self.begin, self.end = pipeline.unfinished_guard(self.work, self.marker, self.segs, "gfx_specs.json")
        self.job = Job(1, "render", "f", [])

    def run_cut_short(self):
        cast(Callable[[Job], None], self.begin)(self.job)
        (self.segs / "0001_cut.mkv").write_text("half")  # what a Stop leaves under the final names
        (self.gfx / "g2.mov").write_text("half")

    def test_next_run_drops_what_the_stopped_run_wrote(self):
        self.run_cut_short()
        cast(Callable[[Job], None], self.begin)(self.job)
        self.assertEqual(sorted(q.name for q in self.segs.iterdir()), ["0000_old.mkv"])
        self.assertEqual(sorted(q.name for q in self.gfx.iterdir()), ["g1.mov"])
        self.assertEqual(len(list((self.tmp / "panel").glob("stale-gfx/*/g2.*.mov"))), 1)
        self.assertIn("nie skończyło", self.job.lines[-1])

    def test_marker_cut_short_still_counts_as_unfinished(self):
        self.run_cut_short()
        st = self.marker.stat()
        self.marker.write_text('{"gfx": ["g')  # power loss while the run started: same time, half the text
        os.utime(self.marker, ns=(st.st_atime_ns, st.st_mtime_ns))
        cast(Callable[[Job], None], self.begin)(self.job)
        self.assertEqual(sorted(q.name for q in self.segs.iterdir()), ["0000_old.mkv"])
        self.assertEqual(json.loads(self.marker.read_text()), {"gfx": ["../x"]})  # rewritten whole for this run (unsafe id never retired)

    def test_finished_run_keeps_its_files(self):
        self.run_cut_short()
        cast(Callable[[Job], None], self.end)(self.job)
        cast(Callable[[Job], None], self.begin)(self.job)
        self.assertEqual(len(list(self.segs.iterdir())), 2)
        self.assertTrue((self.gfx / "g2.mov").exists())

    def render(self, work):
        """render_steps of `work` with render/ in tmp: (mark_log, segs_begin, gfx_begin, finished), run_v4.sh left out."""
        with mock.patch.object(pipeline, "RENDER", self.tmp):
            steps = pipeline.render_steps(work, "f", self.tmp)
        return [cast(Callable[[Job], None], s) for s in steps if not isinstance(s, Cmd)]

    def start(self, steps):
        for step in steps[:3]:
            step(self.job)

    def log(self, text):
        with (self.tmp / "render.log").open("a") as f:
            f.write(text)

    def test_render_that_exits_0_without_done_keeps_the_marker(self):
        self.log("DONE f\n")  # an earlier, finished render of the same film
        steps = self.render(self.work)
        self.start(steps)
        marker = self.tmp / "f" / "panel-unfinished.json"
        self.log("ffmpeg: No space left on device\nUNDONE f\nALL_DONE\n")  # run_v4.sh still exits 0
        with self.assertRaises(StepFailed):
            steps[3](self.job)
        self.assertTrue(marker.exists())
        self.assertTrue(pipeline.gfx_marker(self.work).exists())
        self.log("DONE f\nALL_DONE\n")
        steps[3](self.job)
        self.assertFalse(marker.exists())
        self.assertFalse(pipeline.gfx_marker(self.work).exists())

    def test_other_project_with_the_same_film_does_not_erase_our_cut_gfx(self):
        rsegs = self.tmp / "f" / "segs"  # render/<film>/segs: shared by both projects
        rsegs.mkdir(parents=True)
        a = self.render(self.work)
        self.start(a)
        (self.gfx / "g2.mov").write_text("half")  # project A stopped while rendering g2
        (rsegs / "0001_cut.mkv").write_text("half")
        other = self.tmp / "other"
        (other / "gfx").mkdir(parents=True)
        (other / "gfx_specs.json").write_text("[]")
        b = self.render(other)
        self.start(b)  # B drops the shared segs A may have cut
        self.assertEqual(list(rsegs.iterdir()), [])
        self.log("DONE f\n")
        b[3](self.job)
        self.start(self.render(self.work))  # A again: its own marker still lists g2
        self.assertFalse((self.gfx / "g2.mov").exists())
        self.assertTrue((self.gfx / "g1.mov").exists())

    def test_marker_that_is_not_an_object_is_unfinished(self):
        self.run_cut_short()
        self.marker.write_text("[]")
        os.utime(self.marker, (time.time() - 30, time.time() - 30))
        cast(Callable[[Job], None], self.begin)(self.job)
        self.assertEqual(sorted(q.name for q in self.segs.iterdir()), ["0000_old.mkv"])

    def test_every_long_step_is_guarded(self):
        (self.work / "plan_f.json").write_text(json.dumps({"edl_out": "edl_f.json", "gfx_specs": "gfx_specs.json"}))
        for steps in (pipeline.preview_steps(self.work, "f", 1.0), pipeline.render_steps(self.work, "f", self.tmp),
                      pipeline.gfx_steps(self.work, {"gfx_specs": "gfx_specs.json"})):
            names = [getattr(s, "__name__", "cmd") for s in steps]
            self.assertLess(names.index("begin"), names.index("end" if "end" in names else "finished"))


class CacheTest(Tmp):
    def test_dead_projects_lose_caches_but_keep_history(self):
        gone = self.tmp / "gone"
        gone.mkdir()
        for kind in ("tmp", "preview", "beds", "history"):
            store.project_dir(kind, gone)
            store.project_dir(kind, self.work)
        os.rmdir(gone)
        self.assertEqual(store.prune_dead_projects(), [store.project_key(gone)])
        root = self.tmp / "panel"
        self.assertEqual(sorted(p.parent.parent.name for p in root.glob(f"*/{store.project_key(gone)}/PROJECT.txt")), ["history"])
        self.assertEqual(len(list(root.glob(f"*/{store.project_key(self.work)}"))), 4)
        self.assertEqual(store.prune_dead_projects(), [])  # nothing left to remove: nothing reported


class BeatsStepsTest(Tmp):
    """The "Rozpoznaj momenty" job: beats.py writes only a proposal; no proposal = a failed job, never an empty apply."""

    def setUp(self):
        super().setUp()
        words = [{"offsets": {"from": i * 400, "to": i * 400 + 300}, "text": f" {t}"}
                 for i, t in enumerate(["Mam", "40", "repozytoriów.", "Ale", "żadne", "nie", "działa,", "bo", "to", "wcale", "nie", "znaczy", "sukcesu."])]
        (self.work / "w.json").write_text(json.dumps({"transcription": words}))
        self.plan = {"words": "w.json", "keeps": [[0, len(words) - 1]], "vertical": True, "name": "f"}
        (self.work / "plan_f.json").write_text(json.dumps(self.plan))
        self.job = Job(1, "beats", "f", [])

    def test_proposal_is_collected_and_the_plan_is_untouched(self):
        prepare, cmd, collect = pipeline.beats_steps(self.work, "f")
        assert isinstance(cmd, Cmd) and callable(prepare) and callable(collect)
        self.assertNotIn("--write", cmd.argv)
        prepare(self.job)
        r = subprocess.run(cmd.argv, cwd=cmd.cwd, capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        collect(self.job)
        self.assertGreaterEqual(self.job.result["summary"]["beats"], 2)
        self.assertEqual(json.loads((self.work / "plan_f.json").read_text()), self.plan)
        self.assertEqual(self.job.result["base"], json.dumps(self.plan))

    def test_no_proposal_fails_the_job(self):
        prepare, _, collect = pipeline.beats_steps(self.work, "f")
        assert callable(prepare) and callable(collect)
        prepare(self.job)  # beats.py crashed (or was stopped) before writing: nothing to apply
        with self.assertRaises(StepFailed):
            collect(self.job)


class BrollStepsTest(Tmp):
    """The "Dobierz b-roll" job: EDL rebuild, then broll.py through the Infisical bridge; collect() turns its proposal into
    the summary the dialog shows (new = word not in the plan yet, provider error passed on); no proposal = failed job."""

    def setUp(self):
        super().setUp()
        self.plan = {"words": "w.json", "keeps": [[0, 9]], "vertical": True, "edl_out": "edl_f.json", "broll": [{"word": 3, "query": "a"}]}
        (self.work / "plan_f.json").write_text(json.dumps(self.plan))
        self.job = Job(1, "broll", "f", [])

    def steps(self):
        *rebuild, prepare, cmd, collect = pipeline.broll_steps(self.work, "f")
        assert isinstance(cmd, Cmd) and callable(prepare) and callable(collect)
        self.assertEqual(len(rebuild), len(pipeline.rebuild_steps(self.work, "f")))  # times come from a fresh EDL
        return prepare, cmd, collect

    def test_proposal_becomes_the_dialog_summary(self):
        prepare, cmd, collect = self.steps()
        self.assertEqual(cmd.argv[:6], [pipeline.PY, str(pipeline.BRIDGE), "run", "--secrets", "Pexels", "--"])
        out = Path(cmd.argv[-1])
        prepare(self.job)
        prop = {"broll": [{"word": 3, "said": "faktury", "query": "a", "author": "A"}, {"word": 7, "said": "magazyn", "query": "b"}],
                "new": 1, "format": "short", "error": "Pexels: limit zapytań (200/h): spróbuj za godzinę",
                "skipped": [{"word": 9, "said": "x", "reason": "r", "down": True}], "credits": ["B-roll: Pexels — A (u)"]}
        out.write_text(json.dumps(prop))
        collect(self.job)
        m = self.job.result["summary"]
        self.assertEqual((m["broll"], m["new"], m["format"], m["error"]), (2, 1, "short", prop["error"]))
        self.assertEqual([(i["said"], i["new"]) for i in m["items"]], [("faktury", False), ("magazyn", True)])
        self.assertEqual((m["skipped"], m["credits"]), (prop["skipped"], prop["credits"]))
        self.assertEqual(self.job.result["base"], json.dumps(self.plan))  # /broll-apply refuses if the plan moved on
        self.assertEqual(json.loads((self.work / "plan_f.json").read_text()), self.plan)

    def test_odd_plan_shape_still_collects(self):
        (self.work / "plan_f.json").write_text(json.dumps({**self.plan, "broll": None}))
        prepare, cmd, collect = self.steps()
        prepare(self.job)
        Path(cmd.argv[-1]).write_text(json.dumps({"broll": [{"word": 3, "said": "a"}], "new": 1}))
        collect(self.job)
        self.assertEqual(self.job.result["summary"]["items"][0]["new"], True)

    def test_no_proposal_fails_the_job(self):
        prepare, cmd, collect = self.steps()
        Path(cmd.argv[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd.argv[-1]).write_text("{}")  # left by an earlier run: prepare drops it, so it is never applied again
        prepare(self.job)
        with self.assertRaises(StepFailed):
            collect(self.job)


STUB = """import os, sys
name = os.path.basename(sys.argv[0])
log = os.environ["RUNV4_LOG"]
n = sum(1 for line in open(log) if line.split()[0] == name) + 1 if os.path.exists(log) else 1
open(log, "a").write(f"{name} {n}\\n")
if os.environ.get("FAIL_" + name.split(".")[0]) == str(n):
    sys.exit(1)
if name == "critic.py" and "--fix" in sys.argv and n == 1 and os.environ.get("CRITIC_FIX"):
    print(" FIX: moment wymuszony")
if name == "assemble.py":
    os.makedirs(sys.argv[2], exist_ok=True)
    open(os.path.join(sys.argv[2], "film.mp4"), "w").write("x")
"""


class RunV4FailureTest(unittest.TestCase):
    """run_v4.sh itself (copy with VP pointed at stub scripts): a failed step gives FAILED <step> <film>, no render of
    the stale EDL (no DONE) and exit 1; the fix loop's rebuild failing skips the film too (continue 2).
    Graphics render only when the project has gfx_specs.json (node needed only for that test)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="montaz-runv4-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        vp = self.tmp / "vp"
        for rel in ("build_edl.py", "face_track.py", "critic.py", "assemble.py", "music/make_beds.py", "to_otio.py", "beatfx.py"):
            (vp / rel).parent.mkdir(parents=True, exist_ok=True)
            (vp / rel).write_text(STUB)
        (vp / "gfx").mkdir()
        (vp / "gfx" / "render.mjs").write_text("")
        (vp / ".venv" / "bin").mkdir(parents=True)
        (vp / ".venv" / "bin" / "python").symlink_to(shutil.which("python3") or "/usr/bin/python3")
        (vp / "render").mkdir()
        src = (Path(__file__).resolve().parents[2] / "run_v4.sh").read_text()
        self.assertIn("VP=/home/kamil-jan/video-pipeline;", src)
        (vp / "run_v4.sh").write_text(src.replace("VP=/home/kamil-jan/video-pipeline;", f"VP={vp};"))
        self.vp, self.work, self.out = vp, self.tmp / "work", self.tmp / "out"
        self.work.mkdir()
        self.out.mkdir()
        for film in ("x", "long"):
            (self.work / f"edl_{film}.json").write_text(json.dumps({"out_name": "film.mp4"}))

    def run_v4(self, film: str = "x", rc: int = 0, **env: str) -> tuple[str, str]:
        log = self.tmp / "calls.log"
        r = subprocess.run(["bash", str(self.vp / "run_v4.sh"), str(self.work), str(self.out), film], check=False, timeout=60,
                           env={**os.environ, "RUNV4_LOG": str(log), **env}, capture_output=True, text=True)
        self.assertEqual(r.returncode, rc, r.stderr)  # a terminal or a scheduled task sees a failed film too
        return (self.vp / "render" / "render.log").read_text(), log.read_text() if log.exists() else ""

    def test_all_steps_ok_renders(self):
        render, calls = self.run_v4()
        self.assertIn("DONE x", render)
        self.assertIn("assemble.py 1", calls)
        self.assertTrue((self.out / "film.mp4").exists())

    def test_failed_step_skips_the_render(self):
        for step in ("build_edl", "face_track", "make_beds"):
            with self.subTest(step=step):
                (self.tmp / "calls.log").unlink(missing_ok=True)
                (self.vp / "render" / "render.log").unlink(missing_ok=True)
                render, calls = self.run_v4(rc=1, **{f"FAIL_{step}": "1"})
                self.assertIn(f"FAILED {step} x", render)
                self.assertNotIn("DONE x", render)
                self.assertNotIn("assemble.py", calls)
                self.assertIn("ALL_DONE", render)
                self.assertIn("FAILED_FILMS x", render)

    def test_failed_assemble_is_logged_and_skips_the_metric(self):
        render, calls = self.run_v4(rc=1, FAIL_assemble="1")
        self.assertIn("FAILED assemble x", render)
        self.assertNotIn("DONE x", render)
        self.assertNotIn("beatfx.py", calls)
        self.assertFalse((self.out / "film.mp4").exists())

    def test_rendered_film_logs_the_metric_and_long_gets_its_otio(self):
        render, calls = self.run_v4("long")
        self.assertIn("DONE long", render)
        self.assertIn("beatfx.py 1", calls)  # ADR-0002 metric line per render
        self.assertIn("to_otio.py 1", calls)

    def test_otio_only_for_the_long_rendered_in_this_run(self):
        _, calls = self.run_v4("x")  # edl_long.json lies in WORK from an earlier run
        self.assertNotIn("to_otio.py", calls)
        render, _ = self.run_v4("long", rc=1, FAIL_to_otio="1")
        self.assertIn("FAILED to_otio long", render)
        self.assertIn("FAILED_FILMS long", render)

    def test_failed_long_gets_no_otio_from_its_stale_edl(self):
        render, calls = self.run_v4("long", rc=1, FAIL_build_edl="1")
        self.assertIn("FAILED build_edl long", render)
        self.assertNotIn("to_otio.py", calls)
        self.assertNotIn("beatfx.py", calls)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_failed_gfx_renders_no_film(self):
        (self.work / "gfx_specs.json").write_text("[]")
        (self.vp / "gfx" / "render.mjs").write_text("process.exit(1)")
        render, calls = self.run_v4(rc=1)
        self.assertIn("FAILED gfx", render)
        self.assertIn("FAILED_FILMS x", render)
        self.assertEqual(calls, "")  # not even build_edl: the cards are shared by every film

    def test_out_name_with_a_path_is_refused(self):
        for bad in ("../film.mp4", "sub/film.mp4", ".hidden.mp4", ""):
            with self.subTest(out_name=bad):
                (self.tmp / "calls.log").unlink(missing_ok=True)
                (self.work / "edl_x.json").write_text(json.dumps({"out_name": bad}))
                render, calls = self.run_v4(rc=1)
                self.assertIn("FAILED out_name x", render)
                self.assertNotIn("assemble.py", calls)

    def test_failed_rebuild_in_fix_loop_skips_the_render(self):
        render, calls = self.run_v4(rc=1, CRITIC_FIX="1", FAIL_build_edl="2")
        self.assertIn("FAILED build_edl x", render)
        self.assertNotIn("DONE x", render)
        self.assertNotIn("assemble.py", calls)


if __name__ == "__main__":
    unittest.main()

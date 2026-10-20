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

    def test_stop_after_the_film_was_assembled_keeps_the_segs(self):
        rsegs = self.tmp / "f" / "segs"
        rsegs.mkdir(parents=True)
        steps = self.render(self.work)
        self.start(steps)
        (rsegs / "0001_done.mkv").write_text("complete")
        self.log("DONE f\n")  # Stop came in the critic --video after this line
        self.start(self.render(self.work))
        self.assertEqual([q.name for q in rsegs.iterdir()], ["0001_done.mkv"])
        self.assertIn("po złożeniu", " ".join(self.job.lines))

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


if __name__ == "__main__":
    unittest.main()

"""Runner failure paths: exit codes, timeouts, Stop, a crashing Python step, the live log offset."""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from montazownia import jobs
from montazownia.jobs import Cmd, Job, Runner


def py(code: str, **kw) -> Cmd:
    return Cmd([sys.executable, "-c", code], Path.cwd(), **kw)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        logs = Path(tempfile.mkdtemp(prefix="montaz-jobs-"))
        self.addCleanup(shutil.rmtree, logs, True)
        self.r = Runner(logs)
        self.addCleanup(self.r.shutdown)

    def finish(self, job, limit=20):
        t0 = time.time()
        while job.status in ("queued", "running") and time.time() - t0 < limit:
            time.sleep(0.05)
        return job

    def test_exit_code_fails_unless_allowed(self):
        bad = self.finish(self.r.submit("check", "f", [py("raise SystemExit(3)")]))
        self.assertEqual(bad.status, "failed")
        self.assertIn("kodem 3", bad.error)
        ok = self.finish(self.r.submit("check", "f", [py("raise SystemExit(1)", ok_codes=(0, 1))]))
        self.assertEqual(ok.status, "done")

    def test_hung_step_is_killed_after_its_timeout(self):
        t0 = time.time()
        job = self.finish(self.r.submit("preview", "f", [py("import time; time.sleep(30)", timeout=0.5)]))
        self.assertEqual(job.status, "failed")
        self.assertIn("limit", job.error)
        self.assertLess(time.time() - t0, 10)

    def test_step_ignoring_sigterm_is_killed(self):
        with mock.patch.object(jobs, "KILL_AFTER", 0.5):
            t0 = time.time()
            job = self.finish(self.r.submit("preview", "f", [Cmd(["bash", "-c", "trap '' TERM; sleep 30; true"], Path.cwd(), timeout=0.3)]))
        self.assertEqual(job.status, "failed")
        self.assertLess(time.time() - t0, 10)

    def test_shutdown_kills_a_step_ignoring_sigterm_before_returning(self):
        job = self.r.submit("render", "f", [Cmd(["bash", "-c", "trap '' TERM; sleep 30; true"], Path.cwd(), timeout=None)])
        while job.proc is None:
            time.sleep(0.05)
        proc = job.proc
        time.sleep(0.2)  # let bash install the trap
        with mock.patch.object(jobs, "KILL_AFTER", 0.5):
            self.r.shutdown()
        self.assertIsNotNone(proc.wait(timeout=2))  # gone when shutdown returns, not later from a timer

    def test_grandchild_holding_the_pipe_does_not_block_the_queue(self):
        # setsid puts the sleeper outside the group: it survives the kill and keeps stdout open
        holder = self.r.submit("preview", "f", [Cmd(["bash", "-c", "setsid sleep 12 & sleep 30; true"], Path.cwd(), timeout=0.3)])
        after = self.r.submit("check", "f", [py("print('ok')")])
        t0 = time.time()
        self.finish(after)
        self.assertEqual((holder.status, after.status), ("failed", "done"))
        self.assertLess(time.time() - t0, 11)  # before the 12 s sleeper lets go of the pipe

    def test_stop_running_and_queued(self):
        running = self.r.submit("render", "f", [py("import time; time.sleep(30)", timeout=None)])
        queued = self.r.submit("preview", "f", [py("print(1)")])
        while running.status != "running" or running.proc is None:
            time.sleep(0.05)
        self.r.stop(running.id)
        self.r.stop(queued.id)
        self.assertEqual(self.finish(running).status, "stopped")
        self.assertEqual(queued.status, "stopped")
        self.r.stop(running.id)  # already finished: no ProcessLookupError

    def test_stop_right_after_start_is_not_lost(self):
        for _ in range(20):  # the gap between "running" and Popen is tiny: hit it repeatedly
            job = self.r.submit("render", "f", [py("import time; time.sleep(30)", timeout=None)])
            while job.status == "queued":
                pass
            self.r.stop(job.id)
            self.assertEqual(self.finish(job, limit=5).status, "stopped")

    def test_python_step_error_shows_in_the_job(self):
        def boom(job):
            raise ValueError("zły plan")
        job = self.finish(self.r.submit("fix", "f", [boom]))
        self.assertEqual((job.status, job.error), ("failed", "ValueError: zły plan"))

    def test_pending_finds_the_same_job(self):
        a = self.r.submit("render", "f", [py("import time; time.sleep(30)", timeout=None)])
        b = self.r.submit("check", "f", [py("print(1)")])
        self.assertIs(self.r.pending("check", "f"), b)
        self.assertIsNone(self.r.pending("check", "g"))
        self.r.shutdown()
        self.assertEqual(self.finish(a).status, "stopped")


class LogOffsetTest(unittest.TestCase):
    def test_since_survives_trimming(self):
        j = Job(1, "render", "f", [])
        for k in range(6001):
            j.say(f"l{k}")
        v = j.view(since=5990)
        self.assertEqual(v["n"], 6001)
        self.assertEqual(v["lines"], [f"l{k}" for k in range(5990, 6001)])


if __name__ == "__main__":
    unittest.main()

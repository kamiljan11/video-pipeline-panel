"""One-at-a-time runner for pipeline commands (they all write into the same work dir, so never in parallel).

A job = a list of steps; a step is a command (argv + cwd, run as a subprocess) or a Python callable.
Output goes to the job's log (kept in memory for the UI and written to render/panel/jobs/<id>.log).
"""
from __future__ import annotations

import itertools
import logging
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("montazownia.jobs")


@dataclass
class Cmd:
    argv: list[str]
    cwd: Path
    ok_codes: tuple[int, ...] = (0,)
    tail: list[Path] = field(default_factory=list)  # files the command appends to: streamed into the log too
    timeout: float | None = 600.0  # seconds; a hung ffmpeg/Chrome must not block the one-job queue forever


Step = Cmd | Callable[["Job"], None]


@dataclass
class Job:
    id: int
    kind: str
    film: str
    steps: list[Step]
    status: str = "queued"  # queued | running | done | failed | stopped
    lines: list[str] = field(default_factory=list)
    dropped: int = 0  # lines trimmed from the front: `since` counts from the very first line
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None
    proc: subprocess.Popen | None = None
    stop_requested: bool = False

    def say(self, line: str) -> None:
        self.lines.append(line.rstrip("\n"))
        if len(self.lines) > 5000:
            del self.lines[:1000]
            self.dropped += 1000

    def view(self, since: int = 0) -> dict:
        return {"id": self.id, "kind": self.kind, "film": self.film, "status": self.status, "error": self.error,
                "result": self.result, "lines": self.lines[max(0, since - self.dropped):], "n": self.dropped + len(self.lines),
                "seconds": round((self.ended or time.time()) - (self.started or self.created), 1)}


class StepFailed(RuntimeError):
    pass


def _name(cmd: Cmd) -> str:
    script = next((a for a in cmd.argv if a.endswith((".py", ".sh", ".mjs"))), cmd.argv[0])
    return Path(script).name


KILL_AFTER = 10.0  # seconds between SIGTERM and SIGKILL


def _kill(proc: subprocess.Popen | None) -> None:
    """SIGTERM the whole group (run_v4.sh -> assemble.py -> ffmpeg), SIGKILL if it is still there after KILL_AFTER s."""
    if proc is None or proc.poll() is not None:
        return
    _signal(proc, signal.SIGTERM)
    t = threading.Timer(KILL_AFTER, lambda: proc.poll() is None and _signal(proc, signal.SIGKILL))
    t.daemon = True
    t.start()


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:  # already gone
        pass


class Runner:
    def __init__(self, log_dir: Path, ids: Iterator[int] | None = None) -> None:
        """`ids`: a counter shared with another runner (the player's media lane), so job ids never collide."""
        self.log_dir = log_dir
        self.jobs: dict[int, Job] = {}
        self._q: queue.Queue[Job] = queue.Queue()
        self._ids = ids if ids is not None else itertools.count(1)
        self.current: Job | None = None
        threading.Thread(target=self._loop, daemon=True, name="jobs").start()

    def submit(self, kind: str, film: str, steps: list[Step]) -> Job:
        job = Job(next(self._ids), kind, film, steps)
        self.jobs[job.id] = job
        self._q.put(job)
        log.info("job %d %s %s queued", job.id, kind, film)
        return job

    def busy_with(self, kind: str) -> bool:
        return any(j.kind == kind and j.status in ("queued", "running") for j in list(self.jobs.values()))  # list: other lanes' threads ask too

    def pending(self, kind: str, film: str) -> Job | None:
        """A queued/running job of this kind for this film (a second click returns it instead of queueing a copy)."""
        return next((j for j in list(self.jobs.values()) if j.kind == kind and j.film == film and j.status in ("queued", "running")), None)

    def active(self) -> list[Job]:
        return [j for j in list(self.jobs.values()) if j.status in ("queued", "running")]

    def shutdown(self) -> None:
        """Panel is closing: stop everything, so no render keeps writing into the project unseen.
        Waits for the SIGKILL fallback itself: the timer thread would die with the panel process."""
        procs = [j.proc for j in self.active() if j.proc]
        for j in self.active():
            self.stop(j.id)
        deadline = time.time() + KILL_AFTER
        while any(p.poll() is None for p in procs) and time.time() < deadline:
            time.sleep(0.1)
        for p in procs:
            if p.poll() is None:
                _signal(p, signal.SIGKILL)

    def stop(self, job_id: int) -> Job | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job.stop_requested = True
        if job.status == "queued":
            job.status = "stopped"
        else:
            _kill(job.proc)
        return job

    def _loop(self) -> None:
        while True:
            job = self._q.get()
            if job.status == "stopped":
                continue
            self.current = job
            job.status, job.started = "running", time.time()
            try:
                for step in job.steps:
                    if job.stop_requested:
                        raise StepFailed("zatrzymane")
                    if isinstance(step, Cmd):
                        self._run(job, step)
                    else:
                        step(job)
                job.status = "done"
            except StepFailed as e:
                job.status = "stopped" if job.stop_requested else "failed"
                job.error = str(e)
            except Exception as e:  # a bug in a Python step must show up in the UI, not kill the runner
                log.exception("job %d %s failed", job.id, job.kind)
                job.status, job.error = "failed", f"{type(e).__name__}: {e}"
            job.ended = time.time()
            self.current = None
            self._persist(job)
            log.info("job %d %s %s -> %s %s", job.id, job.kind, job.film, job.status, job.error)

    def _run(self, job: Job, cmd: Cmd) -> None:
        job.say("$ " + " ".join(cmd.argv))
        offsets = {p: (p.stat().st_size if p.exists() else 0) for p in cmd.tail}
        proc = subprocess.Popen(cmd.argv, cwd=cmd.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace", start_new_session=True)
        job.proc = proc
        if job.stop_requested:  # Stop came between the step check and Popen: stop() saw no process yet
            _kill(proc)
        timed_out = threading.Event()

        def expire() -> None:
            timed_out.set()
            _kill(proc)

        watchdog = threading.Timer(cmd.timeout, expire) if cmd.timeout else None
        if watchdog:
            watchdog.daemon = True
            watchdog.start()
        stop_tail = threading.Event()
        tailer = threading.Thread(target=self._tail, args=(job, offsets, stop_tail), daemon=True)
        tailer.start()
        reader = threading.Thread(target=self._read, args=(job, proc), daemon=True)
        reader.start()
        code = proc.wait()
        # a grandchild that left the group (setsid) may keep the pipe open: do not let it hold the one-job queue
        reader.join(5)
        if watchdog:
            watchdog.cancel()
        stop_tail.set()
        tailer.join(2)
        job.proc = None
        if timed_out.is_set():
            raise StepFailed(f"{_name(cmd)} przekroczył limit {cmd.timeout:.0f} s i został zatrzymany")
        if code not in cmd.ok_codes:
            raise StepFailed(f"{_name(cmd)} zakończył się kodem {code}")

    @staticmethod
    def _read(job: Job, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        with proc.stdout:
            for line in proc.stdout:
                job.say(line)

    @staticmethod
    def _tail(job: Job, offsets: dict[Path, int], stop: threading.Event) -> None:
        while True:
            for p, off in list(offsets.items()):
                if p.exists() and p.stat().st_size > off:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        f.seek(off)
                        chunk = f.read()
                        offsets[p] = f.tell()
                    for line in chunk.splitlines():
                        job.say(f"[{p.name}] {line}")
            if stop.wait(1.0) and all(not p.exists() or p.stat().st_size <= offsets[p] for p in offsets):
                return

    def _persist(self, job: Job) -> None:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            (self.log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{job.id}-{job.kind}.log").write_text(
                "\n".join(job.lines) + f"\n# status: {job.status} {job.error}\n", encoding="utf-8")
        except OSError as e:
            log.warning("cannot write job log: %s", e)

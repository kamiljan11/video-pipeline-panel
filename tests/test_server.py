"""HTTP layer on a tiny synthetic project; jobs are only queued (no pipeline script runs).

Covers what review found: film names reach run_v4.sh only if a plan file has that name, only this panel's
pages may call the API, undo rebuilds the film it changed and re-renders graphics whose spec it restored.
"""
import importlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

from montazownia.jobs import Runner

uvicorn: Any = None
server: Any = None
try:
    uvicorn = importlib.import_module("uvicorn")
    server = importlib.import_module("montazownia.server")
except ModuleNotFoundError:  # interpreter without the panel venv (fastapi/uvicorn live in panel/.venv)
    pass

WORDS = [{"text": f" słowo{k}", "offsets": {"from": k * 500, "to": k * 500 + 400}} for k in range(10)]
SPECS = [{"id": "g1", "type": "kw", "text": "jeden", "dur": 2.0}, {"id": "g2", "type": "kw", "text": "dwa", "dur": 2.0}]


class QueueOnly(Runner):
    """Runner whose queue is never consumed: submitted jobs stay 'queued'."""
    def __init__(self) -> None:  # no worker thread
        self.jobs, self.current = {}, None
        self._ids = iter(range(1, 10_000))

    def submit(self, kind, film, steps):
        from montazownia.jobs import Job
        job = Job(next(self._ids), kind, film, steps)
        self.jobs[job.id] = job
        return job


def make_project(root: Path) -> Path:
    work = root / "work"
    (work / "gfx").mkdir(parents=True)
    (work / "w.json").write_text(json.dumps({"transcription": WORDS}))
    (work / "gfx_specs.json").write_text(json.dumps(SPECS, indent=1) + "\n")
    for film, gid in (("a", "g1"), ("b", "g2")):
        plan = {"src": str(work / "src.mp4"), "words": "w.json", "edl_out": f"edl_{film}.json", "keeps": [[0, 9]],
                "gfx": [{"id": gid, "word": 2}], "gfx_specs": "gfx_specs.json"}
        (work / f"plan_{film}.json").write_text(json.dumps(plan, indent=1) + "\n")
        (work / "gfx" / f"{gid}.mov").write_bytes(b"old render")
    return work


@unittest.skipUnless(server is not None, "needs the panel venv: .venv/bin/python -m unittest discover -s tests")
class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="montaz-server-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        mock.patch.dict(os.environ, {"MONTAZ_PANEL_DIR": str(self.tmp / "panel")}).start()  # never the real render/panel
        self.work = make_project(self.tmp)
        self.panel = server.Panel(self.work, self.tmp / "out")
        self.panel.runner = QueueOnly()
        mock.patch.object(server.pipeline, "foreign_render", return_value=[]).start()
        self.addCleanup(mock.patch.stopall)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.uv = uvicorn.Server(uvicorn.Config(server.create_app(self.panel), host="127.0.0.1", port=self.port, log_level="error"))
        threading.Thread(target=self.uv.run, daemon=True).start()
        while not self.uv.started:
            time.sleep(0.02)
        self.addCleanup(setattr, self.uv, "should_exit", True)

    def call(self, path, body=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def kinds(self):
        return [(j.kind, j.film) for j in self.panel.runner.jobs.values()]

    def test_unknown_film_never_reaches_the_pipeline(self):
        evil = urllib.parse.quote("x'+__import__('os').popen('id').read()+'", safe="")
        for path in (f"/api/film/{evil}/render", f"/api/film/{evil}/check", f"/api/film/{evil}/fix-preview",
                     f"/api/film/{evil}/preview?t=1", f"/api/undo?film={evil}", "/api/film/*/render"):
            with self.subTest(path=path):
                self.assertEqual(self.call(path, {})[0], 404)
        self.assertEqual(self.kinds(), [])
        self.assertEqual(self.call("/api/film/a/render", {})[0], 200)
        self.assertEqual(self.kinds(), [("render", "a")])

    def test_only_this_panels_pages(self):
        own = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.call("/api/film/a/check", {}, {"Origin": own})[0], 200)
        for headers in ({"Origin": "http://127.0.0.1:5678"}, {"Origin": "http://localhost:3000"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Host": "evil.example"}):
            with self.subTest(headers=headers), self.assertLogs("montazownia", "WARNING"):
                self.assertEqual(self.call("/api/stop", {}, headers)[0], 403)

    def test_undo_rebuilds_the_changed_film_and_rerenders_its_graphic(self):
        st, r = self.call("/api/film/a/edit", {"op": "update_spec", "id": "g1", "fields": {"text": "nowy"}})
        self.assertEqual(st, 200, r)
        self.assertFalse((self.work / "gfx" / "g1.mov").exists())  # retired: render.mjs makes it again
        self.assertTrue((self.work / "gfx" / "g2.mov").exists())  # other graphics untouched
        (self.work / "gfx" / "g1.mov").write_bytes(b"render of 'nowy'")  # a preview rendered the edited text
        self.panel.runner.jobs.clear()
        st, r = self.call("/api/undo?film=b", {})  # undo pressed while film b is open
        self.assertEqual((st, r["films"]), (200, ["a"]))
        self.assertEqual(self.kinds(), [("rebuild", "a")])
        self.assertFalse((self.work / "gfx" / "g1.mov").exists())  # 'nowy' must not be baked into the full render
        self.assertEqual(json.loads((self.work / "gfx_specs.json").read_text())[0]["text"], "jeden")

    def test_new_graphic_with_a_reused_id_is_rendered_fresh(self):
        (self.work / "gfx" / "g9.mov").write_bytes(b"render of an old g9")
        st, _ = self.call("/api/film/a/edit", {"op": "add_gfx", "i": 3, "spec": {"id": "g9", "type": "kw", "text": "x", "dur": 2}})
        self.assertEqual(st, 200)
        self.assertFalse((self.work / "gfx" / "g9.mov").exists())

    def test_bad_edits_are_400_not_500(self):
        for body in ({"op": "toggle_word", "i": 999}, {"op": "toggle_word"}, {"op": "globals", "values": {"music_db": 999}},
                     {"op": "globals", "values": {"drift": "x"}}, {"op": "globals", "values": {"tail": float("nan")}},
                     {"op": "add_gfx", "i": 1, "spec": {"id": "z1", "type": "kw", "dur": 2, "anything": "x"}}, {"op": "nope"},
                     {"op": "add_gfx", "i": 1, "spec": "x"}, {"op": "globals", "values": [1]},
                     {"op": "accept", "code": ["C6"], "msg": ["x"], "reason": "r"}, {"op": "accept", "code": "C6", "msg": "x", "reason": 5},
                     {"op": "add_gfx", "i": 1, "spec": {"id": "z2", "type": "kw", "dur": 2, "icon": "../../../../tmp/x"}},
                     {"op": "add_gfx", "i": 1, "spec": {"id": "z3", "type": "kw", "dur": 2, "icon": "no-such-icon-zz"}},
                     {"op": "add_gfx", "i": 1, "spec": {"id": "z4", "type": "list", "dur": 2, "items": "abc"}},
                     {"op": "globals", "values": {"hook_text": "raz\nDialogue: 0,0:00:00.00,9:00:00.00,Cap,,0,0,0,,x"}},
                     {"op": "globals", "values": {"hook_text": "raz\u2028dwa"}}, {"op": "globals", "values": {"hook_text": "raz\x7f"}}):
            with self.subTest(body=body):
                self.assertEqual(self.call("/api/film/a/edit", body)[0], 400)
        plan = json.loads((self.work / "plan_a.json").read_text())
        self.assertNotIn("critic_accept", plan)  # nothing above reached the plan the critic reads
        self.assertEqual([g["id"] for g in plan["gfx"]], ["g1"])

    def test_double_click_returns_the_same_job(self):
        a, b = self.call("/api/film/a/check", {})[1], self.call("/api/film/a/check", {})[1]
        self.assertEqual(a["job"], b["job"])

    def test_stop_sweeps_foreign_renders_only_when_a_render_is_stopped(self):
        with mock.patch.object(server.pipeline, "stop_render", return_value="swept") as sweep:
            self.call("/api/film/a/check", {})
            self.assertEqual(self.call("/api/stop", {})[1]["processes"], "")
            self.call("/api/film/a/render", {})
            self.assertEqual(self.call("/api/stop", {})[1]["processes"], "swept")
        self.assertEqual(sweep.call_count, 1)

    def test_fix_apply_refuses_after_a_later_edit(self):
        from montazownia.jobs import Job
        base = (self.work / "plan_a.json").read_text()
        fix = Job(500, "fix", "a", [], status="done", result={"base": base, "plan": {**json.loads(base), "tail": 8}})
        self.panel.runner.jobs[fix.id] = fix
        self.call("/api/film/a/edit", {"op": "toggle_word", "i": 4})
        st, r = self.call("/api/film/a/fix-apply?job=500", {})
        self.assertEqual(st, 409, r)

    def test_beats_job_and_apply_through_history(self):
        from montazownia.jobs import Job
        st, r = self.call("/api/film/a/beats", {})
        self.assertEqual((st, self.kinds()[-1]), (200, ("beats", "a")))
        base = (self.work / "plan_a.json").read_text()
        prop = {"beats": [{"type": "stat", "word": 4, "src": "rule"}], "gfx": [{"id": "b_a_4", "word": 4, "lead": 0}],
                "specs": [{"id": "b_a_4", "type": "kw", "text": "40", "dur": 2.5, "icon": "hash", "quote": True}]}
        self.panel.runner.jobs[600] = Job(600, "beats", "a", [], status="done", result={"base": base, "proposal": prop})
        self.panel.runner.jobs[601] = Job(601, "beats", "b", [], status="done", result={"base": base, "proposal": prop})
        self.assertEqual(self.call("/api/film/a/beats-apply?job=601", {})[0], 400)  # another film's proposal
        st, r = self.call("/api/film/a/beats-apply?job=600", {})
        self.assertEqual(st, 200, r)
        plan = json.loads((self.work / "plan_a.json").read_text())
        self.assertEqual((plan["beats"][0]["word"], plan["gfx"][-1]["id"]), (4, "b_a_4"))
        self.assertIn("b_a_4", {s["id"] for s in json.loads((self.work / "gfx_specs.json").read_text())})
        st, r = self.call("/api/film/a/edit", {"op": "set_beat", "pos": 0, "fields": {"type": "maxim"}})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.call("/api/film/a/edit", {"op": "set_beat", "pos": 0, "fields": {"type": "zzz"}})[0], 400)
        self.call("/api/undo?film=a", {})
        self.call("/api/undo?film=a", {})
        self.assertNotIn("beats", json.loads((self.work / "plan_a.json").read_text()))
        self.assertEqual(self.call("/api/film/a/beats-apply?job=600", {})[0], 200)  # base matches again after undo
        self.call("/api/film/a/edit", {"op": "toggle_word", "i": 2})
        self.assertEqual(self.call("/api/film/a/beats-apply?job=600", {})[0], 409)
        view = self.call("/api/film/a")[1]
        self.assertIn("stat", view["beat_types"])

    def test_first_card_in_a_project_without_specs_file(self):
        from montazownia.jobs import Job
        plan = json.loads((self.work / "plan_a.json").read_text())
        plan = {k: v for k, v in plan.items() if k not in ("gfx_specs", "gfx_dir", "gfx")}
        (self.work / "plan_a.json").write_text(json.dumps(plan))
        (self.work / "gfx_specs.json").unlink(missing_ok=True)
        prop = {"beats": [{"type": "stat", "word": 4, "src": "rule"}], "gfx": [{"id": "b_a_4", "word": 4, "lead": 0}],
                "specs": [{"id": "b_a_4", "type": "kw", "text": "40", "dur": 2.5, "icon": "hash", "quote": True}]}
        base = (self.work / "plan_a.json").read_text()
        self.panel.runner.jobs[700] = Job(700, "beats", "a", [], status="done", result={"base": base, "proposal": prop})
        st, r = self.call("/api/film/a/beats-apply?job=700", {})
        self.assertEqual(st, 200, r)
        self.assertEqual([sp["id"] for sp in json.loads((self.work / "gfx_specs.json").read_text())], ["b_a_4"])
        self.assertEqual(json.loads((self.work / "plan_a.json").read_text())["gfx_specs"], "gfx_specs.json")
        plan["gfx_specs"] = "../poza.json"  # plans arrive via Syncthing: no file is created outside the project
        (self.work / "plan_a.json").write_text(json.dumps(plan))
        base = (self.work / "plan_a.json").read_text()
        self.panel.runner.jobs[701] = Job(701, "beats", "a", [], status="done", result={"base": base, "proposal": prop})
        st, r = self.call("/api/film/a/beats-apply?job=701", {})
        self.assertEqual(st, 400, r)
        self.assertIn("poza katalogiem projektu", str(r))
        self.assertFalse((self.work.parent / "poza.json").exists())

    def test_add_and_remove_beat_ops(self):
        st, r = self.call("/api/film/a/edit", {"op": "add_beat", "i": 3, "type": "maxim"})
        self.assertEqual(st, 200, r)
        plan = json.loads((self.work / "plan_a.json").read_text())
        self.assertEqual([(b["word"], b["type"], b["src"]) for b in plan["beats"]], [(3, "maxim", "manual")])
        self.assertTrue(plan["beats"][0]["text"].strip())  # the sentence around the word (card icon), not ""
        self.assertEqual(self.call("/api/film/a/edit", {"op": "add_beat", "i": 3, "type": "zzz"})[0], 400)
        self.assertEqual(self.call("/api/film/a/edit", {"op": "remove_beat", "pos": 0})[0], 200)
        self.assertEqual(json.loads((self.work / "plan_a.json").read_text())["beats"], [])
        self.assertEqual(self.call("/api/film/a/edit", {"op": "remove_beat", "pos": 0})[0], 400)

    def test_wrong_shape_grammar_degrades_the_view(self):
        from unittest import mock

        from montazownia import pipeline
        bad = self.work / "grammar_bad.json"
        bad.write_text(json.dumps({"beats": {"stat": "Liczba"}}))  # valid JSON, wrong shape (hand edit)
        with mock.patch.object(pipeline, "GRAMMAR", bad):
            st, view = self.call("/api/film/a")
        self.assertEqual((st, view["beat_types"]), (200, {}))

    def test_edits_wait_for_a_render_started_outside_the_panel(self):
        with mock.patch.object(server.pipeline, "foreign_render", return_value=[4242]):
            self.assertEqual(self.call("/api/film/a/edit", {"op": "toggle_word", "i": 4})[0], 409)
            self.assertEqual(self.call("/api/film/a/render", {})[0], 409)
            # check/fix/preview rebuild the EDL (build_edl + face_track) that the outside render is reading
            for path in ("/api/film/a/check", "/api/film/a/fix-preview", "/api/film/a/preview?t=1"):
                with self.subTest(path=path):
                    self.assertEqual(self.call(path, {})[0], 409)
        self.assertEqual(self.kinds(), [])

    def test_unsafe_plan_file_name_is_not_a_film(self):
        # run_v4.sh pastes the name into `python3 -c`: a file dropped into work (Syncthing) must not become a film
        evil = "x'+str(__import__('os').system('true'))+'"
        (self.work / f"plan_{evil}.json").write_text((self.work / "plan_a.json").read_text())
        with self.assertLogs("montazownia.store", "WARNING"):
            self.assertEqual(server.store.films(self.work), ["a", "b"])
        self.assertEqual(self.call(f"/api/film/{urllib.parse.quote(evil, safe='')}/render", {})[0], 404)
        self.assertEqual(self.kinds(), [])

    def test_graphics_wait_for_a_running_preview(self):
        # the preview's gfx/render.mjs would write the OLD spec into the .mov the full render reuses
        self.panel.runner.submit("preview", "a", [])
        self.assertEqual(self.call("/api/film/a/edit", {"op": "update_spec", "id": "g1", "fields": {"text": "nowy"}})[0], 409)
        self.assertEqual(self.call("/api/film/a/edit", {"op": "toggle_word", "i": 4})[0], 200)  # cuts are fine
        self.panel.runner.jobs.clear()
        self.assertEqual(self.call("/api/film/a/edit", {"op": "update_spec", "id": "g1", "fields": {"text": "nowy"}})[0], 200)
        self.panel.runner.submit("preview", "a", [])
        self.assertEqual(self.call("/api/undo?film=a", {})[0], 409)  # undo would rewrite gfx_specs.json too

    def test_time_must_be_finite_and_not_negative(self):
        for path in ("/api/film/a/frame?t=nan", "/api/film/a/frame?t=-1", "/api/film/a/preview?t=inf", "/api/film/a/preview?t=nan"):
            with self.subTest(path=path):
                self.assertEqual(self.call(path, {} if "preview" in path else None)[0], 400)
        self.assertEqual(self.panel.runner.jobs, {})

    def test_broken_source_media_is_502_not_500(self):
        (self.work / "src.mp4").write_bytes(b"not a video")
        for path in ("/api/film/a/peaks", "/api/film/a/frame?t=1"):
            with self.subTest(path=path):
                st, body = self.call(path)
                self.assertEqual(st, 502, body)
                self.assertIn("ffmpeg", body["error"])

    def test_one_panel_per_project(self):
        fd = server.lock_project(self.work)
        self.addCleanup(os.close, fd)
        with self.assertRaises(SystemExit):
            server.lock_project(self.work)
        other = self.tmp / "other"
        other.mkdir()
        os.close(server.lock_project(other))

    def test_busy_port_is_refused_before_opening_the_browser(self):
        self.assertFalse(server.port_free(self.port))



@unittest.skipUnless(server is not None, "needs the panel venv: .venv/bin/python -m unittest discover -s tests")
class ProcessTest(unittest.TestCase):
    def test_closed_terminal_shuts_the_panel_down(self):
        tmp = Path(tempfile.mkdtemp(prefix="montaz-proc-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        work = make_project(tmp)
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0))
            port = sk.getsockname()[1]
        env = {**os.environ, "MONTAZ_PANEL_DIR": str(tmp / "panel")}
        proc = subprocess.Popen([sys.executable, "-m", "montazownia.server", str(work), "--no-browser", "--port", str(port)],
                                cwd=Path(__file__).resolve().parents[1], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/project", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        proc.send_signal(signal.SIGHUP)  # what closing the terminal sends; uvicorn alone ignores it
        self.assertIsNotNone(proc.wait(timeout=10))
        out = proc.stdout.read() if proc.stdout else ""
        self.assertIn("panel closing", out)  # lifespan ran: without the SIGHUP handler the process just dies, jobs keep running
        self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main()

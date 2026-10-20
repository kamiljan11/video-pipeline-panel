"""End-to-end over HTTP on a COPY of the 2026-10-20 project (temp dir; ~/Videos is never written).

short3: cut + restore a word, add a punch-in, 10 s preview renders, "Sprawdź" returns the critic report,
undo restores the file. long: change a graphic's text -> the graphic is re-rendered in the preview.
Slow (real build_edl / face_track / ffmpeg / Chrome): MONTAZ_SLOW=0 skips it.
"""
import importlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

uvicorn: Any = None
server: Any = None
try:
    uvicorn = importlib.import_module("uvicorn")
    server = importlib.import_module("montazownia.server")
except ModuleNotFoundError:  # interpreter without the panel venv (fastapi/uvicorn live in panel/.venv)
    pass
HAVE_DEPS = server is not None

SAMPLE = Path.home() / "Videos" / "Montaz" / "2026-10-20" / "work"
SHORT = "short3_jedno_zlecenie"


def copy_project(dst: Path) -> Path:
    work = dst / "work"
    (dst / "out").mkdir(parents=True)
    work.mkdir()
    for p in SAMPLE.iterdir():
        if p.suffix in (".json", ".sil", ".txt", ".ass", ".srt") and p.is_file():
            shutil.copy(p, work / p.name)
    (work / "gfx").mkdir()
    for mov in (SAMPLE / "gfx").glob("*.mov"):
        (work / "gfx" / mov.name).symlink_to(mov)  # read-only use; a retired graphic only moves the link
    for plan_p in work.glob("plan_*.json"):
        plan = json.loads(plan_p.read_text())
        for k in ("gfx_dir", "ass_out"):
            if plan.get(k, "").startswith(str(SAMPLE)):
                plan[k] = plan[k].replace(str(SAMPLE), str(work))
        if plan.get("srt_out"):
            plan["srt_out"] = str(dst / "out" / Path(plan["srt_out"]).name)
        plan_p.write_text(json.dumps(plan, ensure_ascii=False))
    return work


@unittest.skipUnless(HAVE_DEPS, "needs the panel venv: .venv/bin/python -m unittest discover -s tests")
@unittest.skipUnless(SAMPLE.is_dir() and os.environ.get("MONTAZ_SLOW", "1") != "0", "needs the sample project")
class PanelE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="montaz-e2e-"))
        cls.env = mock.patch.dict(os.environ, {"MONTAZ_PANEL_DIR": str(cls.tmp / "panel")})  # never the real render/panel
        cls.env.start()
        cls.work = copy_project(cls.tmp)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.panel = server.Panel(cls.work, cls.tmp / "out")
        cfg = uvicorn.Config(server.create_app(cls.panel), host="127.0.0.1", port=cls.port, log_level="warning")
        cls.uv = uvicorn.Server(cfg)
        threading.Thread(target=cls.uv.run, daemon=True).start()
        for _ in range(50):
            if cls.uv.started:
                break
            time.sleep(0.1)
        cls.timings = {}

    @classmethod
    def tearDownClass(cls):
        cls.uv.should_exit = True
        cls.env.stop()
        print("\nczasy:", json.dumps(cls.timings, ensure_ascii=False))
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, path, body=None, method=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method or ("POST" if body is not None else "GET"),
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise AssertionError(f"{path}: HTTP {e.code} {e.read().decode()}") from e

    def wait(self, job_id, limit=900):
        t0 = time.time()
        while time.time() - t0 < limit:
            j = self.call(f"/api/jobs/{job_id}")
            if j["status"] not in ("queued", "running"):
                self.assertEqual(j["status"], "done", "\n".join(j["lines"][-30:]) + j["error"])
                return j
            time.sleep(0.5)
        self.fail(f"job {job_id} timeout")

    def plan(self, film=SHORT):
        return json.loads((self.work / f"plan_{film}.json").read_text())

    def test_1_cut_restore_emph_undo(self):
        v = self.call(f"/api/film/{SHORT}")
        self.assertTrue(v["timeline"]["ready"])
        before_plan, before_total = self.plan(), v["timeline"]["total"]
        i = before_plan["keeps"][1][0] + 3  # a word inside the 2nd kept range
        r = self.call(f"/api/film/{SHORT}/edit", {"op": "toggle_word", "i": i})
        t0 = time.time(); self.wait(r["job"]); self.timings["przeliczenie po cięciu"] = round(time.time() - t0, 1)
        cut = self.call(f"/api/film/{SHORT}")
        self.assertFalse(cut["timeline"]["words"][i]["kept"])
        self.assertLess(cut["timeline"]["total"], before_total)
        r = self.call(f"/api/film/{SHORT}/edit", {"op": "toggle_word", "i": i})
        self.wait(r["job"])
        self.assertEqual(self.plan()["keeps"], before_plan["keeps"])
        self.assertAlmostEqual(self.call(f"/api/film/{SHORT}")["timeline"]["total"], before_total, places=2)

        def room(w):
            for sg in cut["timeline"]["segments"]:
                base = sg["zoom"]["a"] if isinstance(sg["zoom"], dict) else sg["zoom"]
                if sg["src_in"] + 1 < w["src"] < sg["src_out"] - 0.6 and (sg["zmax"] or 1) - base >= 0.03:
                    return True
            return False
        target = next(w for w in cut["timeline"]["words"] if w["kept"] and w["emph"] is None and room(w))
        r = self.call(f"/api/film/{SHORT}/edit", {"op": "add_emph", "i": target["i"]})
        self.wait(r["job"])
        p = self.plan()
        self.assertIn({"word": target["i"], "amt": 0.1, "speed": 0.3}, p["emph"])
        edl = json.loads((self.work / p["edl_out"]).read_text())
        acc, hit = 0.0, False
        for s in edl["segments"]:
            d = s["out"] - s["in"]
            if s["in"] <= target["src"] <= s["out"]:
                hit = isinstance(s["zoom"], dict) and s["zoom"]["t0"] > 0.01
            acc += d
        self.assertTrue(hit, "najazd nie trafił do EDL")
        live = self.call(f"/api/film/{SHORT}")["timeline"]["words"][target["i"]]
        self.assertTrue(live["emph_live"])
        dead = [w for w in self.call(f"/api/film/{SHORT}")["timeline"]["words"] if w["emph_live"] is False]
        self.assertTrue(dead, "short3 has punch-ins flattened by zmax: the panel must flag them")

        r = self.call(f"/api/undo?film={SHORT}", {})
        self.wait(r["job"])
        self.assertNotIn({"word": target["i"], "amt": 0.1, "speed": 0.3}, self.plan().get("emph", []))
        backups = list((server.store.panel_dir() / "history" / server.store.project_key(self.work)).glob("plan_*.json"))
        self.assertGreaterEqual(len(backups), 4)  # a copy before every write (2 toggles, emph, undo)

    def test_2_zmax_is_enforced(self):
        seg = self.call(f"/api/film/{SHORT}")["timeline"]["segments"][0]
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/film/{SHORT}/edit", method="POST",
                                     data=json.dumps({"op": "segment_zoom", "seg_in": seg["src_in"], "zoom": seg["zmax"] + 0.05}).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(e.exception.code, 400)
        self.assertIn("limit", json.loads(e.exception.read())["error"])

    def test_3_preview_short(self):
        t0 = time.time()
        j = self.wait(self.call(f"/api/film/{SHORT}/preview?t=12", {})["job"])
        self.timings["podgląd ±10 s short3"] = round(time.time() - t0, 1)
        f = Path(j["result"]["file"])
        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(f)],
                                         capture_output=True, text=True, check=True).stdout)
        self.assertEqual((info["streams"][0]["width"], info["streams"][0]["height"]), (360, 640))
        self.assertAlmostEqual(float(info["format"]["duration"]), j["result"]["to"] - j["result"]["from"], delta=1.0)

    def test_4_check_returns_critic_report(self):
        t0 = time.time()
        j = self.wait(self.call(f"/api/film/{SHORT}/check", {})["job"])
        self.timings["Sprawdź short3"] = round(time.time() - t0, 1)
        self.assertIn("issues", j["result"]["report"])
        v = self.call(f"/api/film/{SHORT}")
        self.assertIsNotNone(v["critic"])

    def test_5_graphic_text_change_rerenders_it(self):
        specs = json.loads((self.work / "gfx_specs.json").read_text())
        g = next(s for s in specs if s["type"] == "kw")
        r = self.call("/api/film/long/edit", {"op": "update_spec", "id": g["id"], "fields": {"text": g["text"] + " (test)"}})
        self.wait(r["job"])
        self.assertEqual(next(s for s in json.loads((self.work / "gfx_specs.json").read_text()) if s["id"] == g["id"])["text"], g["text"] + " (test)")
        self.assertFalse((self.work / "gfx" / f"{g['id']}.mov").exists())  # retired: render.mjs makes it again
        at = next(o for o in self.call("/api/film/long")["timeline"]["overlays"] if o["id"] == g["id"])["start"]
        t0 = time.time()
        j = self.wait(self.call(f"/api/film/long/preview?t={at + 2}", {})["job"], limit=1500)
        self.timings[f"podgląd long z nową grafiką {g['id']}"] = round(time.time() - t0, 1)
        mov = self.work / "gfx" / f"{g['id']}.mov"
        self.assertTrue(mov.exists() and not mov.is_symlink())
        self.assertTrue(Path(j["result"]["file"]).exists())


if __name__ == "__main__":
    unittest.main()

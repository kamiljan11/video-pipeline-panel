"""Montażownia: local editing panel over the video pipeline (FastAPI, 127.0.0.1 only).

Run:  montazownia ~/Videos/Montaz/2026-10-20/work [--port 8790]
Edits change plan_<film>.json / gfx_specs.json (with a backup + undo); after every edit the EDL is
rebuilt by the pipeline itself (build_edl.py + face_track.py), so the timeline shows what will render.
"""
from __future__ import annotations

import argparse
import fcntl
import itertools
import json
import logging
import math
import os
import signal
import socket
import subprocess
import webbrowser
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import live, livemedia, media, pipeline, planops, store, timeline
from .jobs import Job, Runner
from .planops import EditError
from .store import PIPELINE, Change, ConflictError, History, read_json

UI = Path(__file__).resolve().parent.parent / "ui"
GFX_OPS = {"update_spec", "add_gfx", "duplicate_gfx", "remove_gfx"}  # edits that write gfx_specs.json
ICONS = PIPELINE / "node_modules" / "lucide-static" / "icons"
log = logging.getLogger("montazownia")


class Panel:
    def __init__(self, work: Path, out_dir: Path) -> None:
        self.work, self.out_dir = work.resolve(), out_dir.resolve()
        self.history = History(self.work)
        ids = itertools.count(1)  # one numbering for both lanes: /api/jobs/<id> finds a job in either
        self.runner = Runner(store.panel_dir() / "jobs", ids)
        # the live player's media lane: proxies / card copies / beds write only into render/panel, never into work,
        # so they run beside the pipeline lane (a one-time proxy of a long recording must not hold up edits) (ADR-0007)
        self.media = Runner(store.panel_dir() / "jobs", ids)
        self.last_rebuild: dict[str, int] = {}

    # ---- reading ----
    def known(self, film: str) -> str:
        """Only names of existing plan_<film>.json files reach the pipeline (run_v4.sh pastes the name into shell/python)."""
        if film not in store.films(self.work):
            raise HTTPException(404, f"nie ma filmu {film!r} w tym projekcie")
        return film

    def plan(self, film: str) -> dict:
        return read_json(store.plan_path(self.work, film))

    def specs(self, plan: dict) -> list[dict]:
        p = store.specs_path(self.work, plan)
        return read_json(p) if p and p.exists() else []

    def edl(self, plan: dict) -> dict | None:
        p = self.work / plan["edl_out"]
        return read_json(p) if p.exists() else None

    def film_view(self, film: str) -> dict:
        plan = self.plan(film)
        words = store.words_of(self.work, plan)
        report_p = pipeline.critic_report(self.work, film)
        report = read_json(report_p) if report_p.exists() else None
        edl = self.edl(plan)
        g = self.grammar()
        return {"film": film, "plan": plan, "specs": self.specs(plan),
                "timeline": timeline.build(plan, words, self.specs(plan), edl, report["issues"] if report else None, g),
                "beat_types": {k: v.get("label", k) for k, v in (g or {}).get("beats", {}).items()},
                "critic": report, "history": self.history.state(), "rebuild_job": self.last_rebuild.get(film),
                "source": plan["src"]}

    def grammar(self) -> dict | None:
        try:
            return pipeline.grammar()
        except (OSError, ValueError):
            log.exception("style/grammar.json unreadable: moments shown without labels")
            return None

    def beat_types(self) -> list[str]:
        g = self.grammar()
        if not g:
            raise HTTPException(500, "brak gramatyki montażu (style/grammar.json)")
        return list(g["beats"])

    def apply_beats(self, film: str, job_id: int) -> dict:
        self.guard()
        j = self.runner.jobs.get(job_id)
        if not j or j.kind != "beats" or j.film != film or j.status != "done":
            raise HTTPException(400, "najpierw „Rozpoznaj momenty” musi się skończyć")
        if store.plan_path(self.work, film).read_text(encoding="utf-8") != j.result["base"]:
            raise HTTPException(409, "plan zmienił się od rozpoznania; uruchom rozpoznanie jeszcze raz")
        prop = j.result["proposal"]
        plan = self.plan(film)
        n = len(store.words_of(self.work, plan))
        if prop.get("specs"):
            self.no_gfx_render()
            for sp in prop["specs"]:
                _known_icons(sp)
            if not plan.get("gfx_specs"):  # a short gets its first graphic: the project's shared specs file and gfx dir
                plan = {**plan, "gfx_specs": "gfx_specs.json", "gfx_dir": str(self.work / "gfx")}
        p2, s2 = planops.apply_beats(plan, self.specs(plan), prop, self.beat_types(), n)  # validated before anything is written
        sp = store.specs_path(self.work, p2) if prop.get("specs") else None
        if sp is not None and not sp.exists():  # fresh project: the history saves into an existing file
            if not sp.resolve().is_relative_to(self.work.resolve()):  # the plan comes via Syncthing: never create outside the project
                raise HTTPException(400, "plik grafik (gfx_specs) poza katalogiem projektu")
            store.atomic_write(sp, "[]")
        return self.save(film, "momenty montażu", p2, s2 if prop.get("specs") else None)

    def apply_broll(self, film: str, job_id: int) -> dict:
        self.guard()
        j = self.runner.jobs.get(job_id)
        if not j or j.kind != "broll" or j.film != film or j.status != "done":
            raise HTTPException(400, "najpierw „Dobierz b-roll” musi się skończyć")
        if store.plan_path(self.work, film).read_text(encoding="utf-8") != j.result["base"]:
            raise HTTPException(409, "plan zmienił się od doboru b-rollu; uruchom dobór jeszcze raz")
        plan = self.plan(film)
        p2 = planops.apply_broll(plan, j.result["proposal"], len(store.words_of(self.work, plan)), pipeline.BROLL_DIR)
        return self.save(film, "b-roll", p2)

    # ---- writing ----
    def guard(self) -> None:
        if self.runner.busy_with("render"):
            raise HTTPException(409, "trwa pełny render: zmiany po jego zakończeniu (albo zatrzymaj render)")
        self.no_foreign_render()

    def no_gfx_render(self) -> None:
        """A preview renders graphics (gfx/render.mjs reads gfx_specs.json once and skips existing .mov): a spec changed
        meanwhile would get the OLD .mov, which the full render then reuses. So graphics wait for the preview."""
        if self.runner.busy_with("preview") or self.runner.busy_with("gfx"):
            raise HTTPException(409, "trwa render grafik (podgląd albo „Renderuj grafiki”): zmień grafikę po jego zakończeniu")

    def gfx_busy(self) -> bool:
        """gfx/render.mjs may be writing work/gfx/*.mov: the player's card copies wait (a half file would be converted)."""
        return any(self.runner.busy_with(k) for k in ("preview", "gfx", "render")) or bool(self.foreign_render())

    def job(self, job_id: int) -> Job | None:
        return self.runner.jobs.get(job_id) or self.media.jobs.get(job_id)

    # ---- live player (ADR-0007) ----
    def live_edl(self, film: str) -> tuple[Path, dict]:
        p = self.work / self.plan(film)["edl_out"]
        if not p.is_file():
            raise HTTPException(404, "brak EDL filmu: kliknij „Sprawdź”")
        try:
            edl = read_json(p)
        except ValueError as e:  # build_edl.py / face_track.py rewriting it right now: the rebuild's reload reads it again
            raise HTTPException(503, f"EDL filmu właśnie się zapisuje: {e}") from e
        if not isinstance(edl, dict) or not isinstance(edl.get("segments"), list):
            raise HTTPException(502, "EDL filmu nie ma listy ujęć (segments)")
        return p, edl

    def live_view(self, film: str) -> dict:
        p, edl = self.live_edl(film)
        st = livemedia.state(self.work, film, p, edl)
        subs = None
        ass = livemedia.local_file(edl.get("subs"))
        if ass is not None and ass.suffix == ".ass" and ass.stat().st_size < 5_000_000:
            subs = live.parse_ass(ass.read_text(encoding="utf-8", errors="replace"))
        specs = {s["id"]: s for s in self.specs(self.plan(film)) if isinstance(s, dict) and isinstance(s.get("id"), str)}
        view = live.model(film, edl, livemedia.edl_version(p), st, specs, subs)
        pend = self.media.pending("live", film)
        gfx = self.runner.pending("gfx", film)
        view.update({"job": pend.id if pend else None, "gfx_job": gfx.id if gfx else None, "gfx_busy": self.gfx_busy(),
                     "todo": livemedia.todo(st, beds=True)})
        return view

    def live_card(self, film: str, gid: str) -> Path:
        _, edl = self.live_edl(film)
        o = next((o for o in edl.get("overlays", []) if o.get("id") == gid), None)
        mov = livemedia.local_file(o.get("file")) if o and store.FILM_NAME.fullmatch(gid) else None
        f = livemedia.card_file(self.work, gid, mov, livemedia.card_target(edl)) if mov else None
        if f is None or not f.is_file():
            raise HTTPException(404, f"brak kopii karty {gid}")
        return f

    def no_foreign_render(self) -> None:
        """Check/preview/fix also run build_edl.py + face_track.py, which rewrite edl_<film>.json under a running render."""
        if self.foreign_render():
            raise HTTPException(409, "ten projekt renderuje się poza panelem (terminal / zadanie); poczekaj albo zatrzymaj tamten render")

    def foreign_render(self) -> list[int]:
        own = {j.proc.pid for j in self.runner.active() if j.proc}
        return pipeline.foreign_render(self.work, own)

    def save(self, film: str, label: str, plan: dict | None = None, specs: list[dict] | None = None) -> dict:
        updates: dict[Path, Any] = {}
        if plan is not None:
            updates[store.plan_path(self.work, film)] = plan
        if specs is not None:
            sp = store.specs_path(self.work, plan or self.plan(film))
            if sp is None:
                raise HTTPException(400, "ten film nie ma pliku grafik (gfx_specs)")
            updates[sp] = specs
        change = self.history.save(label, updates)
        self.applied(change, "after")
        job = self.rebuild(film) if change.files else None  # coalesces with the rebuild applied() queued
        return {"changed": bool(change.files), "job": job, "history": self.history.state()}

    def applied(self, change: Change, side: str) -> list[str]:
        """After a save/undo/redo wrote `side` of a change: retire graphics whose spec changed, return the films to rebuild."""
        films: set[str] = set()
        plans = {m: self.plan(m) for m in store.films(self.work)}
        for f, (before, after) in change.files.items():
            path = Path(f)
            if path.name.startswith("plan_"):
                films.update(m for m in plans if store.plan_path(self.work, m) == path)
                continue
            old, new = (before, after) if side == "after" else (after, before)
            ids = changed_gfx(old, new)
            for m, plan in plans.items():
                if store.specs_path(self.work, plan) != path:
                    continue
                for gid in ids:
                    self.retire_gfx(m, gid)
                if ids & {g["id"] for g in plan.get("gfx", [])}:
                    films.add(m)
        for m in sorted(films):
            self.rebuild(m)
        return sorted(films)

    def retire_gfx(self, film: str, gid: str) -> None:
        """A changed graphic must be rendered again: gfx/render.mjs skips ids whose .mov exists, so move it aside."""
        gdir = Path(self.plan(film).get("gfx_dir") or self.work / "gfx")
        moved = store.retire_gfx_file(self.work, gdir / f"{gid}.mov")
        if moved:
            log.info("graphic %s will be rendered again (old file moved to %s)", gid, moved.parent)

    def undo_redo(self, which: str, film: str) -> dict:
        self.guard()
        stack = self.history.done if which == "undo" else self.history.undone
        if stack and any(not p.name.startswith("plan_") for p in stack[-1].paths()):
            self.no_gfx_render()
        c = self.history.undo() if which == "undo" else self.history.redo()
        films = self.applied(c, "before" if which == "undo" else "after") if c else []
        job = self.last_rebuild.get(film if film in films else (films[0] if films else ""))
        return {"label": c.label if c else None, "films": films, "job": job, "history": self.history.state()}

    def rebuild(self, film: str) -> int:
        waiting = self.runner.jobs.get(self.last_rebuild.get(film, -1))
        if waiting and waiting.status == "queued":  # several quick clicks -> one rebuild (it reads the latest plan)
            return waiting.id
        # rebuild = the same steps as "Sprawdź": the critic adds ~0.5 s and its markers never show a stale edit
        job = self.runner.submit("rebuild", film, pipeline.check_steps(self.work, film))
        self.last_rebuild[film] = job.id
        return job.id

    def edit(self, film: str, op: str, a: dict) -> dict:
        self.guard()
        if op in GFX_OPS:
            self.no_gfx_render()
        try:
            return self._edit(film, op, a)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            if isinstance(e, EditError):
                raise
            log.exception("edit %s on %s failed", op, film)  # a bug or a broken plan must leave a traceback, not only a 400
            raise EditError(f"złe dane operacji {op}: {type(e).__name__} {e}") from e

    def _edit(self, film: str, op: str, a: dict) -> dict:
        plan = self.plan(film)
        words = store.words_of(self.work, plan)
        texts = [w["text"] for w in words]
        n = len(words)
        specs = self.specs(plan)
        if "i" in a and not 0 <= int(a["i"]) < n:
            raise EditError(f"słowo {a['i']} poza transkrypcją")
        if op == "toggle_word":
            i = int(a["i"])
            verb = "wytnij" if i in planops.kept_indices(plan) else "przywróć"
            return self.save(film, f"{verb} „{texts[i].strip()}”", planops.toggle_word(plan, i, n))
        if op == "add_emph":
            i = int(a["i"])
            return self.save(film, f"najazd na „{texts[i].strip()}”", planops.add_emph(plan, texts, i, a.get("amt"), a.get("speed", 0.3)))
        if op == "update_emph":
            return self.save(film, "zmiana najazdu", planops.update_emph(plan, int(a["pos"]), a.get("amt"), a.get("speed")))
        if op == "remove_emph":
            return self.save(film, "usuń najazd", planops.remove_emph(plan, int(a["pos"])))
        if op == "anchor_gfx":
            i = int(a["i"])
            return self.save(film, f"{a['id']} na „{texts[i].strip()}”", planops.anchor_gfx(plan, a["id"], i, n))
        if op == "set_gfx_lead":
            return self.save(film, f"przesunięcie {a['id']}", planops.set_gfx_lead(plan, a["id"], float(a["lead"])))
        if op == "update_spec":
            new = planops.update_spec(specs, a["id"], _obj(a["fields"]))
            _known_icons(a["fields"])
            return self.save(film, f"grafika {a['id']}", specs=new, plan=None)
        if op == "add_gfx":
            p2, s2 = planops.add_gfx(plan, specs, _obj(a["spec"]), int(a["i"]), n)
            _known_icons(a["spec"])
            return self.save(film, f"nowa grafika {a['spec'].get('id')}", p2, s2)
        if op == "duplicate_gfx":
            p2, s2 = planops.duplicate_gfx(plan, specs, a["id"], a["new_id"], int(a["i"]), n)
            return self.save(film, f"kopia {a['id']}", p2, s2)
        if op == "remove_gfx":
            p2, s2 = planops.remove_gfx(plan, specs, a["id"])
            used_elsewhere = any(a["id"] in {g["id"] for g in self.plan(f).get("gfx", [])} for f in store.films(self.work) if f != film)
            if not used_elsewhere:
                s2 = [s for s in s2 if s["id"] != a["id"]]
            return self.save(film, f"usuń grafikę {a['id']}", p2, s2 if not used_elsewhere else None)
        if op == "segment_zoom":
            seg_in = float(a["seg_in"])
            edl = self.edl(plan)
            seg = next((s for s in (edl or {}).get("segments", []) if abs(s["in"] - seg_in) < 0.006), None)
            if seg is None:
                raise EditError("tego ujęcia już nie ma (zmieniły się cięcia), odśwież")
            return self.save(film, "zoom ujęcia", planops.set_segment_zoom(plan, seg_in, a.get("zoom"), seg.get("zmax")))
        if op == "set_beat":
            return self.save(film, "zmiana momentu", planops.set_beat(plan, int(a["pos"]), _obj(a["fields"]), self.beat_types()))
        if op == "add_beat":
            i = int(a["i"])
            around = "".join(texts[max(0, i - 6):i + 10]).strip()[:300]  # the sentence around it: beats.py picks a card icon from it
            return self.save(film, f"moment na „{texts[i].strip()}”", planops.add_beat(plan, i, str(a.get("type")), self.beat_types(), around))
        if op == "remove_broll":
            return self.save(film, "usuń b-roll", planops.remove_broll(plan, int(a["pos"])))
        if op == "remove_beat":
            return self.save(film, "usuń moment", planops.remove_beat(plan, int(a["pos"])))
        if op == "globals":
            return self.save(film, "ustawienia filmu", planops.set_globals(plan, _obj(a["values"])))
        if op == "accept":
            return self.save(film, f"akceptuję {a['code']}", planops.accept_issue(plan, a["code"], a["msg"], a.get("reason", "")))
        raise EditError(f"nieznana operacja {op}")


def _obj(v: Any) -> dict:
    if not isinstance(v, dict):
        raise EditError("złe dane: oczekiwany obiekt")
    return v


def _known_icons(fields: dict) -> None:
    """planops allows only icon NAMES; a name without a file would crash gfx/render.mjs (ENOENT) for every graphic."""
    names = [fields.get("icon") or "", *(fields.get("icons") or [])]
    missing = [n for n in names if isinstance(n, str) and n and not (ICONS / f"{n}.svg").is_file()]
    if missing:
        raise EditError(f"nie ma takiej ikony: {', '.join(missing)}")


def changed_gfx(old: str, new: str) -> set[str]:
    """Ids of graphics added, removed or edited between two gfx_specs.json texts."""
    a = {s["id"]: s for s in json.loads(old)}
    b = {s["id"]: s for s in json.loads(new)}
    return {g for g in a.keys() | b.keys() if a.get(g) != b.get(g)}


class FreshUI(StaticFiles):
    """The page's own files: the browser asks again every time (ETag -> 304, cheap). A cached app.js next to a new
    index.html left the live player dead after an update (2026-10-24: Play did nothing until a hard reload)."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        r = super().file_response(full_path, stat_result, scope, status_code)
        r.headers["Cache-Control"] = "no-cache"
        return r


def same_origin(headers: Mapping[str, str]) -> bool:
    host = headers.get("host", "")
    if host.split(":")[0] not in ("127.0.0.1", "localhost"):
        return False
    origin = headers.get("origin")
    if origin is not None and origin != f"http://{host}":
        return False
    return headers.get("sec-fetch-site") in (None, "same-origin", "none")


def create_app(panel: Panel) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        log.info("panel closing: stopping %d jobs", len(panel.runner.active()) + len(panel.media.active()))
        panel.media.shutdown()
        panel.runner.shutdown()  # Ctrl+C / closed terminal: no render keeps writing into the project unseen

    app = FastAPI(title="Montażownia", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.exception_handler(EditError)
    async def edit_error(_: Request, e: EditError) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=400)

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, e: ConflictError) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=409)

    @app.exception_handler(subprocess.SubprocessError)
    async def media_failed(_: Request, e: subprocess.SubprocessError) -> JSONResponse:
        log.warning("helper command failed: %s %s", e, getattr(e, "stderr", "") or "")
        return JSONResponse({"error": "polecenie pomocnicze (ffmpeg / stop_render.py) nie powiodło się, szczegóły w logu panelu"}, status_code=502)

    @app.exception_handler(FileNotFoundError)
    async def missing(_: Request, e: FileNotFoundError) -> JSONResponse:
        log.warning("missing file: %s", e)
        return JSONResponse({"error": f"brak pliku: {Path(str(e)).name}"}, status_code=404)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # the API runs the pipeline: only pages of THIS panel may talk to it. Host check = DNS rebinding;
        # exact Origin = other local apps (n8n, dev servers on other ports); Sec-Fetch-Site = <img>/<video> from any site.
        if not same_origin(request.headers):
            log.warning("refused %s %s (host=%r origin=%r fetch-site=%r)", request.method, request.url.path,
                        request.headers.get("host"), request.headers.get("origin"), request.headers.get("sec-fetch-site"))
            return JSONResponse({"error": "tylko z tego panelu"}, status_code=403)
        return await call_next(request)

    @app.get("/api/project")
    def project() -> dict:
        return {"work": str(panel.work), "out": str(panel.out_dir), "films": store.films(panel.work),
                "history": panel.history.state(), "battery": pipeline.battery(),
                "render_busy": panel.runner.busy_with("render")}

    @app.get("/api/film/{film}")
    def film(film: str) -> dict:
        return panel.film_view(panel.known(film))

    @app.get("/api/film/{film}/peaks")
    def peaks(film: str) -> dict:
        return media.peaks(panel.plan(panel.known(film))["src"])

    @app.get("/api/film/{film}/frame")
    def frame(film: str, t: float) -> FileResponse:
        _time(t)
        return FileResponse(media.frame(panel.plan(panel.known(film))["src"], t), media_type="image/jpeg")

    @app.post("/api/film/{film}/edit")
    async def edit(film: str, request: Request) -> dict:
        body = await request.json()
        if not isinstance(body, dict):
            raise EditError("zła treść zapytania")
        return panel.edit(panel.known(film), str(body.pop("op", "")), body)

    @app.post("/api/undo")
    def undo(film: str) -> dict:
        return panel.undo_redo("undo", panel.known(film))

    @app.post("/api/redo")
    def redo(film: str) -> dict:
        return panel.undo_redo("redo", panel.known(film))

    def submit_once(kind: str, film: str, steps: Callable[[], list]) -> dict:
        j = panel.runner.pending(kind, film) or panel.runner.submit(kind, film, steps())  # double click = same job
        return {"job": j.id}

    @app.post("/api/film/{film}/check")
    def check(film: str) -> dict:
        panel.no_foreign_render()
        return submit_once("check", panel.known(film), lambda: pipeline.check_steps(panel.work, film))

    @app.post("/api/film/{film}/fix-preview")
    def fix_preview(film: str) -> dict:
        panel.no_foreign_render()
        return submit_once("fix", panel.known(film), lambda: pipeline.fix_preview_steps(panel.work, film))

    @app.post("/api/film/{film}/fix-apply")
    def fix_apply(film: str, job: int) -> dict:
        panel.known(film)
        panel.guard()
        j = panel.runner.jobs.get(job)
        if not j or j.kind != "fix" or j.status != "done":
            raise HTTPException(400, "najpierw „Popraw automatycznie” musi się skończyć")
        if store.plan_path(panel.work, film).read_text(encoding="utf-8") != j.result["base"]:
            raise HTTPException(409, "plan zmienił się od podglądu poprawek; uruchom podgląd jeszcze raz")
        return panel.save(film, "poprawki krytyka", j.result["plan"])

    @app.post("/api/film/{film}/beats")
    def beats(film: str, llm: bool = False) -> dict:
        return submit_once("beats", panel.known(film), lambda: pipeline.beats_steps(panel.work, film, llm))

    @app.post("/api/film/{film}/beats-apply")
    def beats_apply(film: str, job: int) -> dict:
        return panel.apply_beats(panel.known(film), job)

    @app.post("/api/film/{film}/broll")
    def broll(film: str) -> dict:
        panel.no_foreign_render()  # the job rebuilds the EDL first
        return submit_once("broll", panel.known(film), lambda: pipeline.broll_steps(panel.work, film))

    @app.post("/api/film/{film}/broll-apply")
    def broll_apply(film: str, job: int) -> dict:
        return panel.apply_broll(panel.known(film), job)

    @app.post("/api/film/{film}/preview")
    def preview(film: str, t: float) -> dict:
        _time(t)
        plan = panel.plan(panel.known(film))
        panel.no_foreign_render()
        return submit_once("preview", film, lambda: pipeline.gfx_steps(panel.work, plan) + pipeline.preview_steps(panel.work, film, t))

    @app.get("/api/jobs/{job_id}/video")
    def preview_video(job_id: int) -> FileResponse:
        j = panel.runner.jobs.get(job_id)
        if not j or "file" not in j.result or not Path(j.result["file"]).exists():
            raise HTTPException(404, "brak podglądu")
        return FileResponse(j.result["file"], media_type="video/mp4")

    # ---- live player: the EDL as data + browser-playable copies (ADR-0007); media files only from the EDL ----
    @app.get("/api/film/{film}/live")
    def live_view(film: str) -> dict:
        return panel.live_view(panel.known(film))

    @app.post("/api/film/{film}/live/prepare")
    def live_prepare(film: str, beds: bool = True) -> dict:
        panel.known(film)
        j = panel.media.pending("live", film)
        if j is None:
            j = panel.media.submit("live", film, livemedia.prepare_steps(panel.work, film, beds, panel.gfx_busy))
        return {"job": j.id}

    @app.get("/api/film/{film}/live/src/{key}")
    def live_src(film: str, key: str) -> FileResponse:
        _, edl = panel.live_edl(panel.known(film))
        p = livemedia.local_file(edl.get("sources", {}).get(key))
        f = livemedia.source_file(panel.work, p) if p else None
        if f is None or not f.is_file():
            raise HTTPException(404, "brak kopii nagrania: przygotuj odtwarzacz")
        return FileResponse(f, media_type="video/mp4")

    @app.get("/api/film/{film}/live/card/{gid}")
    def live_card(film: str, gid: str) -> FileResponse:
        return FileResponse(panel.live_card(panel.known(film), gid), media_type="video/webm")

    @app.get("/api/film/{film}/live/broll/{k}")
    def live_broll(film: str, k: int) -> FileResponse:
        _, edl = panel.live_edl(panel.known(film))
        f = livemedia.broll_file(edl, k)
        if f is None:
            raise HTTPException(404, "brak klipu b-roll")
        return FileResponse(f, media_type="video/mp4")

    @app.get("/api/film/{film}/live/bed/{kind}")
    def live_bed(film: str, kind: str) -> FileResponse:
        if kind not in ("music", "sfx"):
            raise HTTPException(404, "nie ma takiego podkładu")
        f = livemedia.beds_dir(panel.work, panel.known(film)) / f"{kind}.wav"
        if not f.is_file():
            raise HTTPException(404, "brak podkładu: przygotuj odtwarzacz z muzyką")
        return FileResponse(f, media_type="audio/wav")

    @app.get("/api/film/{film}/live/pipframe")
    def live_pipframe(film: str) -> FileResponse:
        _, edl = panel.live_edl(panel.known(film))
        f = livemedia.pip_frame_file(edl)
        if f is None:
            raise HTTPException(404, "brak ramki PiP motywu")
        return FileResponse(f, media_type="image/png")

    @app.post("/api/film/{film}/gfx")
    def gfx(film: str) -> dict:
        """Render the graphics whose .mov is missing (after an edit retired them), without a preview or a full render."""
        plan = panel.plan(panel.known(film))
        if panel.runner.busy_with("render"):
            raise HTTPException(409, "trwa pełny render: on też renderuje grafiki")
        panel.no_foreign_render()
        steps = pipeline.gfx_steps(panel.work, plan)
        if not steps:
            raise HTTPException(400, "ten film nie ma grafik (gfx_specs)")
        return submit_once("gfx", film, lambda: steps)

    @app.post("/api/film/{film}/render")
    def render(film: str) -> dict:
        panel.known(film)
        if panel.runner.busy_with("render"):
            raise HTTPException(409, "render już trwa")
        panel.no_foreign_render()
        b = pipeline.battery()
        job = panel.runner.submit("render", film, pipeline.render_steps(panel.work, film, panel.out_dir))
        return {"job": job.id, "battery": b}

    @app.post("/api/stop")
    def stop() -> dict:
        jobs = [(panel.runner, j) for j in panel.runner.active()] + [(panel.media, j) for j in panel.media.active()]
        for lane, j in jobs:
            lane.stop(j.id)
        # the panel's own process groups are already signalled; the pipeline's stop script also catches ffmpeg
        # children a render left behind, but it ends EVERY pipeline render on the laptop, so only when a render was stopped
        swept = pipeline.stop_render() if any(j.kind == "render" for _, j in jobs) else ""
        return {"stopped": [j.id for _, j in jobs], "processes": swept}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int, since: int = 0) -> dict:
        j = panel.job(job_id)
        if not j:
            raise HTTPException(404, "brak zadania")
        return j.view(since)

    @app.get("/api/jobs")
    def jobs() -> list[dict]:
        both = [*panel.runner.jobs.values(), *panel.media.jobs.values()]
        return [{k: v for k, v in j.view().items() if k != "lines"} for j in sorted(both, key=lambda x: -x.id)[:30]]

    @app.get("/api/icons")
    def icons() -> list[str]:
        return sorted(p.stem for p in ICONS.glob("*.svg"))

    app.mount("/icons", StaticFiles(directory=ICONS), name="icons")
    app.mount("/", FreshUI(directory=UI, html=True), name="ui")
    return app


def _time(t: float) -> None:
    if not math.isfinite(t) or t < 0:  # FastAPI accepts "nan" / "inf" / -5 as a float
        raise EditError("czas: liczba sekund od 0")


def lock_project(work: Path) -> int:
    """One panel per project (a second one would write the same files). The fd stays open until the process ends."""
    lock_dir = store.panel_dir() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_dir / f"{store.project_key(work)}.lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise SystemExit(f"panel dla {work} już działa (drugi zapisywałby te same pliki); otwórz tamten") from None
    return fd


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # like uvicorn: a just-closed panel (TIME_WAIT) is not "busy"
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Montażownia: panel montażowy pipeline'u")
    ap.add_argument("work", type=Path, help="katalog work projektu (z plan_*.json)")
    ap.add_argument("--out", type=Path, help="katalog na gotowe filmy (domyślnie ../out obok work)")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    work = args.work.expanduser().resolve()
    if not store.films(work):
        raise SystemExit(f"brak plan_*.json w {work}")
    lock_project(work)
    pruned = store.prune_dead_projects()
    if pruned:
        log.info("removed caches of %d projects whose work dir no longer exists", len(pruned))
    panel = Panel(work, (args.out or work.parent / "out").expanduser())
    url = f"http://127.0.0.1:{args.port}/"
    if not port_free(args.port):  # else the browser would open whatever already listens there (maybe another project's panel)
        raise SystemExit(f"port {args.port} jest zajęty (inny panel?); uruchom z --port {args.port + 1}")
    # closing the terminal sends SIGHUP, which uvicorn ignores: turn it into SIGTERM so shutdown stops the jobs
    signal.signal(signal.SIGHUP, lambda *_: signal.raise_signal(signal.SIGTERM))
    log.info("Montażownia: %s (%s)", url, work)
    if not args.no_browser:
        webbrowser.open(url)
    # an open player tab keeps a video response streaming: without a limit, shutdown waits until the tab is closed
    uvicorn.run(create_app(panel), host="127.0.0.1", port=args.port, log_level="warning", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()

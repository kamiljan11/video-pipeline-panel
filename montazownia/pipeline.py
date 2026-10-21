"""How the panel calls the existing pipeline scripts (the only module that knows their names and order).

Mirrors run_v4.sh: build_edl.py and critic.py run on the system python3, face_track.py and make_beds.py
on the pipeline venv (OpenCV, NumPy). Everything runs in the project's work dir, like run_v4.sh does.
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import preview
from .jobs import Cmd, Job, Step, StepFailed
from .store import (
    FILM_NAME,
    PIPELINE,
    RENDER,
    atomic_write,
    project_dir,
    read_json,
    retire_gfx_file,
)

PY = "python3"
PyStep = Callable[[Job], None]
VENV_PY = str(PIPELINE / ".venv" / "bin" / "python")
BATTERY = Path("/sys/class/power_supply/BAT0")


def _s(name: str) -> str:
    return str(PIPELINE / name)


def scratch(work: Path, *parts: str) -> Path:
    return project_dir("tmp", work).joinpath(*parts)


def critic_report(work: Path, film: str) -> Path:
    return scratch(work, f"critic_{film}.json")


def unfinished_guard(work: Path, marker: Path, segs: Path | None, specs: str | None) -> tuple[PyStep, PyStep]:
    """Steps around a run whose Stop / limit / crash would leave files cut short under their final names, which the
    pipeline then reuses (assemble.py keeps segs/*.mkv, gfx/render.mjs keeps <id>.mov). The marker is removed only when
    the run finishes; if it is still there, the next run first drops what the unfinished one may have cut.
    No "it got far enough" shortcut: render.log is shared by every project, so a "DONE" in it proves nothing about this run."""
    gdir = work / "gfx"

    def begin(job: Job) -> None:
        if marker.exists():
            since = marker.stat().st_mtime
            try:
                left = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):  # cut short itself: still an unfinished run, only the gfx list is lost
                left = {}
            left = left if isinstance(left, dict) else {}
            cut = [q for q in segs.glob("*.mkv") if q.stat().st_mtime >= since] if segs and segs.is_dir() else []
            for q in cut:  # written during the unfinished run: one of them may be cut short (6 in parallel)
                q.unlink(missing_ok=True)
            ids = left.get("gfx", []) if isinstance(left.get("gfx"), list) else []
            gone = [gid for gid in ids if FILM_NAME.fullmatch(str(gid)) and retire_gfx_file(work, gdir / f"{gid}.mov")]
            job.say(f"poprzednie uruchomienie się nie skończyło: usuwam {len(cut)} ujęć i {len(gone)} grafik do ponownego renderu")
        sp = work / specs if specs else None
        missing = [g["id"] for g in read_json(sp) if not (gdir / f"{g['id']}.mov").exists()] if sp and sp.exists() else []
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(marker, json.dumps({"gfx": missing}))

    def end(job: Job) -> None:
        marker.unlink(missing_ok=True)

    return begin, end


def gfx_marker(work: Path) -> Path:
    """One per project: work/gfx belongs to the project (gfx before a preview and the full render both use it)."""
    return scratch(work, "gfx.unfinished.json")


def done_in_log(log: Path, film: str, at: int) -> bool:
    """run_v4.sh writes the line "DONE <film>" only after assemble.py and the move of the film succeeded."""
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            f.seek(at)
            return f"DONE {film}" in f.read().splitlines()
    except (OSError, ValueError):  # missing log, or an offset that is not a position in it
        return False


def rebuild_steps(work: Path, film: str) -> list[Step]:
    edl = read_json(work / f"plan_{film}.json")["edl_out"]
    return [Cmd([PY, _s("build_edl.py"), f"plan_{film}.json"], work), Cmd([VENV_PY, _s("face_track.py"), edl], work, timeout=1800)]  # first run on a long film tracks every shot


def check_steps(work: Path, film: str) -> list[Step]:
    report = critic_report(work, film)

    def drop_old(job: Job) -> None:
        report.unlink(missing_ok=True)  # a crashed critic must not leave the previous report looking current

    def load(job: Job) -> None:
        if not report.exists():
            raise StepFailed("critic.py nie zapisał raportu (szczegóły w logu)")
        job.result["report"] = read_json(report)

    # critic.py exits 1 when it found errors: that is a result, not a failure
    return [*rebuild_steps(work, film), drop_old,
            Cmd([PY, _s("critic.py"), f"plan_{film}.json", "--json", str(report)], work, (0, 1)), load]


def fix_preview_steps(work: Path, film: str) -> list[Step]:
    """critic --fix on a COPY of the plan; the job result is the proposed plan and a readable diff."""
    src = work / f"plan_{film}.json"
    copy = scratch(work, f"plan_{film}.fix.json")
    report = scratch(work, f"critic_{film}.fix.json")

    def prepare(job: Job) -> None:
        # base = exactly the plan the fixer works on; /fix-apply refuses if the real plan moved on since
        base = src.read_text(encoding="utf-8")
        copy.write_text(base, encoding="utf-8")
        job.result["base"] = base

    def collect(job: Job) -> None:
        before, after = json.loads(job.result["base"]), read_json(copy)
        job.result.update({"plan": after, "report": read_json(report), "diff": plan_diff(before, after)})

    return [*rebuild_steps(work, film), prepare,
            Cmd([PY, _s("critic.py"), str(copy), "--fix", "--json", str(report)], work, (0, 1)), collect]


GRAMMAR = PIPELINE / "style" / "grammar.json"


def grammar() -> dict:
    """The editing grammar (moment types, labels, effects per format); data the editor may change, read on every use.
    Only the shape the panel reads is checked here (beats.<type> objects); the full check is beatfx.load_grammar."""
    g = read_json(GRAMMAR)
    beats = g.get("beats") if isinstance(g, dict) else None
    if not isinstance(beats, dict) or not all(isinstance(b, dict) for b in beats.values()):
        raise ValueError(f"{GRAMMAR}: beats must map each moment type to an object")
    return g


def beats_steps(work: Path, film: str, llm: bool = False) -> list[Step]:
    """beats.py on the plan as it is now; it writes nothing but the proposal. /beats-apply saves it through the history."""
    src = work / f"plan_{film}.json"
    out = scratch(work, f"beats_{film}.json")

    def prepare(job: Job) -> None:
        job.result["base"] = src.read_text(encoding="utf-8")  # /beats-apply refuses if the plan moved on since
        out.unlink(missing_ok=True)

    def collect(job: Job) -> None:
        if not out.exists():
            raise StepFailed("beats.py nie zapisał propozycji (szczegóły w logu)")
        prop = read_json(out)
        old = {(b["word"], b["type"]) for b in json.loads(job.result["base"]).get("beats", [])}
        job.result.update({"proposal": prop, "summary": {
            "beats": len(prop.get("beats", [])), "new": sum((b["word"], b["type"]) not in old for b in prop.get("beats", [])),
            "gfx": [f"{sp.get('text')} {sp.get('sub', '')}".strip() for sp in prop.get("specs", [])]}})

    args = [PY, _s("beats.py"), f"plan_{film}.json", "--gfx", "--json", str(out), *(["--llm"] if llm else [])]
    return [prepare, Cmd(args, work, timeout=600), collect]


def plan_diff(before: dict, after: dict) -> list[str]:
    a = json.dumps(before, ensure_ascii=False, indent=1).splitlines()
    b = json.dumps(after, ensure_ascii=False, indent=1).splitlines()
    return [ln for ln in difflib.unified_diff(a, b, "plan teraz", "plan po poprawkach", lineterm="", n=1)]


def preview_steps(work: Path, film: str, t: float, half: float = 10.0) -> list[Step]:
    plan = read_json(work / f"plan_{film}.json")
    edl_p = work / plan["edl_out"]
    out = project_dir("preview", work) / film
    beds = project_dir("beds", work) / film  # never the full render's render/beds_<film>
    full = out / "edl_full.json"  # make_beds.py writes music/sfx paths INTO the EDL: give it a copy, not the project's
    sliced = out / "edl_preview.json"
    name = "preview.mp4"

    def make_slice(job: Job) -> None:
        edl = read_json(full)
        a, b = preview.window(edl, t, half)
        part = preview.slice_edl(edl, a, b)
        if not part["segments"]:
            raise StepFailed("w tym miejscu nie ma obrazu do podglądu")
        if part.get("subs") and Path(part["subs"]).exists():
            ass = out / "subs.ass"
            ass.write_text(preview.shift_ass(Path(part["subs"]).read_text(encoding="utf-8"), a, b), encoding="utf-8")
            part["subs"] = str(ass)
        part["out_name"] = name
        sliced.write_text(json.dumps(part, ensure_ascii=False), encoding="utf-8")
        job.result.update({"from": a, "to": b})
        job.say(f"fragment {a:.1f}-{b:.1f} s, {len(part['segments'])} ujęć, {len(part.get('overlays', []))} grafik")

    def copy_edl(job: Job) -> None:
        shutil.copy(edl_p, full)

    def done(job: Job) -> None:
        job.result["file"] = str(out / name)

    def mkdir(job: Job) -> None:
        out.mkdir(parents=True, exist_ok=True)
        (out / name).unlink(missing_ok=True)

    # beds: generated for this film's current EDL (SFX follow graphics and punch-ins); a Cmd step, so Stop and the limit reach it
    mix = Cmd([VENV_PY, _s("music/make_beds.py"), str(full), str(beds)], work, timeout=300)
    begin, end = unfinished_guard(work, out / "unfinished.json", out / "segs", None)
    return [*rebuild_steps(work, film), mkdir, begin, copy_edl, mix, make_slice,
            Cmd([PY, _s("assemble.py"), str(sliced), str(out), "--proxy"], work, timeout=900), end, done]


def render_steps(work: Path, film: str, out_dir: Path) -> list[Step]:
    log = RENDER / "render.log"
    logs = [log, RENDER / f"critic_{film}.log"]
    start = {"at": 0}

    def mark_log(job: Job) -> None:
        start["at"] = log.stat().st_size if log.exists() else 0

    # run_v4.sh assembles into render/<film>/segs, shared by every project with a film of that name: the segs marker sits
    # there; it renders gfx_specs.json into work/gfx, which is this project's: that marker is per project
    segs_begin, segs_end = unfinished_guard(work, RENDER / film / "panel-unfinished.json", RENDER / film / "segs", None)
    gfx_begin, gfx_end = unfinished_guard(work, gfx_marker(work), None, "gfx_specs.json")

    def finished(job: Job) -> None:
        # run_v4.sh exits 0 even when assemble.py failed (no set -e, `echo ALL_DONE` last): its own "DONE <film>" is the proof
        if not done_in_log(log, film, start["at"]):
            raise StepFailed("render się nie udał (brak „DONE” w render.log): szczegóły w logu powyżej")
        segs_end(job)
        gfx_end(job)

    # a full long-form render takes tens of minutes; the limit only catches a hang
    return [mark_log, segs_begin, gfx_begin,
            Cmd(["nice", "-n", "5", "bash", _s("run_v4.sh"), str(work), str(out_dir), film], work, tail=logs, timeout=4 * 3600), finished]


def gfx_steps(work: Path, plan: dict) -> list[Step]:
    """gfx/render.mjs renders every graphic whose .mov is missing (Chrome + ffmpeg)."""
    if not plan.get("gfx_specs"):
        return []
    begin, end = unfinished_guard(work, gfx_marker(work), None, plan["gfx_specs"])
    return [begin, Cmd(["node", _s("gfx/render.mjs"), plan["gfx_specs"], "gfx"], work, timeout=1800), end]


def foreign_render(work: Path, own: set[int]) -> list[int]:
    """PIDs of run_v4.sh rendering THIS project that the panel did not start (terminal, scheduled task, old panel)."""
    target = work.resolve()
    pids = []
    for d in Path("/proc").glob("[0-9]*"):
        try:
            argv = [a.decode(errors="replace") for a in (d / "cmdline").read_bytes().split(b"\0")]
            at = next(k for k, a in enumerate(argv) if a.endswith("run_v4.sh"))
            pgid = os.getpgid(int(d.name))
            # run_v4.sh WORK OUT FILM; WORK may be relative to where it was started ("." from inside work, a trailing "/")
            arg_work = (d / "cwd").resolve() / argv[at + 1]
        except (OSError, ValueError, StopIteration, IndexError):
            continue
        if pgid not in own and arg_work.resolve() == target:
            pids.append(int(d.name))
    return pids


def stop_render() -> str:
    """The pipeline's own stop script: ends EVERY pipeline render on this laptop (also ones started outside the panel)."""
    r = subprocess.run([PY, _s("stop_render.py")], capture_output=True, text=True, timeout=20, check=False)
    return r.stdout.strip()


def battery() -> dict:
    """{"percent": int|None, "charging": bool|None, "warn": bool}; warn = below 40 % and not on power."""
    try:
        pct = int((BATTERY / "capacity").read_text().strip())
        status = (BATTERY / "status").read_text().strip()
    except (OSError, ValueError):
        return {"percent": None, "charging": None, "warn": False}
    charging = status in ("Charging", "Full", "Not charging")
    return {"percent": pct, "charging": charging, "status": status, "warn": pct < 40 and not charging}

"""Browser-playable copies for the live player, cached per project (ADR-0007).

render/panel/cache/<project>/   src_<path>_<stamp>.mp4   720p H.264 copy of a recording (keyframe every 0.5 s, AAC)
                                card_<id>_<target>_<stamp>.webm (+ .json size/duration)   VP9 with alpha of gfx/<id>.mov
render/panel/beds/<project>/<film>.live/   music.wav, sfx.wav of the current EDL (music/make_beds.py), stamp = EDL version
A stamp hashes the input's real path + size + mtime: a re-rendered card or another recording is converted again, an
unchanged one never. Converting runs only as a job (mediaconv.py, one subprocess); requests only look at files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from . import pipeline
from .jobs import Cmd, Job, Step, StepFailed
from .store import FILM_NAME, PIPELINE, atomic_write, project_dir, read_json

CONV = Path(__file__).resolve().parent / "mediaconv.py"
CARD_LONG = 1280  # card copies: the film frame at 1280 px on its long side
FRESH_WRITE = 3.0  # s: a .mov written this recently may still be growing (gfx/render.mjs): converted next time
LIMIT = 3600  # s for one preparation: a slow laptop needs ~1x real time for the proxy of a long recording
THEME_PART = re.compile(r"[A-Za-z0-9_-]{1,40}")


def cache_dir(work: Path) -> Path:
    return project_dir("cache", work)


def beds_dir(work: Path, film: str) -> Path:
    return project_dir("beds", work) / f"{film}.live"  # never a film's own dir: names have no dot (store.FILM_NAME)


def file_version(p: Path | None) -> str | None:
    """?v= of a file served as is (b-roll clip, PiP frame): another file or a rewrite gets another URL."""
    return stamp(p) if p is not None else None


def _h(text: str, n: int = 10) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:n]


def stamp(p: Path, extra: str = "") -> str:
    st = p.stat()
    return _h(f"{os.path.realpath(p)}|{st.st_size}|{st.st_mtime_ns}|{extra}", 12)


BED_KEYS = ("music", "sfx")  # make_beds.py writes these into the EDL it mixes (run_v4.sh: the project's own): not a new cut


def edl_version(edl_path: Path) -> str:
    return version_of(edl_path.read_bytes())


def version_of(raw: bytes) -> str:
    """The EDL's content, not its bytes: a full render re-saving it with the bed paths is the same film."""
    try:
        edl = json.loads(raw)
    except ValueError:
        return hashlib.sha1(raw).hexdigest()[:12]
    if isinstance(edl, dict):
        edl = {k: v for k, v in edl.items() if k not in BED_KEYS}
    return hashlib.sha1(json.dumps(edl, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def local_file(path: object) -> Path | None:
    """EDL paths come from plans that arrive via Syncthing: only an existing local regular file is ever read
    (no URL, no device; ffmpeg also gets it with the file: protocol)."""
    if not isinstance(path, str) or not path or "\0" in path:
        return None
    p = Path(path)
    return p if p.is_absolute() and p.is_file() else None


def source_file(work: Path, src: Path) -> Path:
    return cache_dir(work) / f"src_{_h(os.path.realpath(src))}_{stamp(src)}.mp4"


def card_target(edl: dict) -> dict:
    W, H = edl.get("w", 2560), edl.get("h", 1440)
    if edl.get("vertical"):  # assemble.py scales every card of a short to the frame
        return {"mode": "fit", "w": round(CARD_LONG * W / H) // 2 * 2, "h": CARD_LONG}
    return {"mode": "scale", "k": CARD_LONG / W, "fullw": CARD_LONG, "fullh": round(CARD_LONG * H / W)}


def _tkey(t: dict) -> str:
    return f"v{t['w']}x{t['h']}" if t["mode"] == "fit" else f"s{t['fullw']}"


def card_file(work: Path, gid: str, mov: Path, target: dict) -> Path:
    tk = _tkey(target)
    return cache_dir(work) / f"card_{gid}_{tk}_{stamp(mov, tk)}.webm"


def pip_frame_file(edl: dict) -> Path | None:
    """= themes.pip_frame: gfx/pip_frame_<name>_<RRGGBB>.png of the EDL theme, else gfx/pip_frame.png."""
    th = edl.get("theme")
    gfx = PIPELINE / "gfx"
    if not isinstance(th, dict):
        p = gfx / "pip_frame.png"
    else:
        name, acc = th.get("name"), th.get("acc")
        if not (isinstance(name, str) and THEME_PART.fullmatch(name) and isinstance(acc, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", acc)):
            return None
        p = gfx / f"pip_frame_{name}_{acc[1:]}.png"
    return p if p.is_file() else None


def state(work: Path, film: str, edl_path: Path, edl: dict, now: float | None = None) -> dict:
    """What exists for this EDL right now (files only; nothing is converted here)."""
    now = time.time() if now is None else now
    sources = {}
    srcs = edl.get("sources")
    for key, path in (srcs if isinstance(srcs, dict) else {}).items():
        p = local_file(path)
        if p is None:
            sources[key] = {"ready": False, "stamp": "", "error": f"brak nagrania {Path(str(path)).name}", "path": None}
            continue
        f = source_file(work, p)
        sources[key] = {"ready": f.is_file(), "stamp": f.stem[-12:], "error": None, "path": str(p), "file": str(f)}
    target = card_target(edl)
    cards: dict[str, dict] = {}
    for o in edl.get("overlays", []):
        gid = o.get("id")
        if not isinstance(gid, str) or not FILM_NAME.fullmatch(gid):
            cards[str(gid)] = {"state": "bad_id", "stamp": ""}
            continue
        mov = local_file(o.get("file"))
        if mov is None:  # retired after an edit (store.retire_gfx_file) or never rendered
            cards[gid] = {"state": "no_render", "stamp": ""}
            continue
        f = card_file(work, gid, mov, target)
        meta = f.with_suffix(".json")
        if f.is_file() and meta.is_file():
            try:
                cards[gid] = {"state": "ready", "stamp": f.stem[-12:], "file": str(f), "meta": json.loads(meta.read_text())}
                continue
            except (OSError, ValueError):
                pass  # a broken meta file: convert again
        busy = now - mov.stat().st_mtime < FRESH_WRITE
        cards[gid] = {"state": "busy" if busy else "todo", "stamp": "", "mov": str(mov), "file": str(f),
                      "mtime_ns": mov.stat().st_mtime_ns}
    broll = [file_version(_broll_file(b)) for b in edl.get("broll", [])]
    bd = beds_dir(work, film)
    beds = None
    if (bd / "music.wav").is_file() and (bd / "sfx.wav").is_file():
        st = bd / "edl.version"
        made = st.read_text().strip() if st.is_file() else ""
        beds = {"stamp": made or "x", "fresh": made == edl_version(edl_path), "dir": str(bd)}
    return {"sources": sources, "cards": cards, "broll": broll, "beds": beds, "pip_frame": file_version(pip_frame_file(edl))}


def _broll_file(b: dict) -> Path | None:
    """Stock clips only from render/broll (= planops.apply_broll / broll.CACHE)."""
    p = local_file(b.get("file")) if isinstance(b, dict) else None
    if p is None:
        return None
    root = os.path.realpath(pipeline.BROLL_DIR)
    return p if os.path.realpath(p).startswith(root + os.sep) else None


def broll_file(edl: dict, k: int) -> Path | None:
    items = edl.get("broll", [])
    return _broll_file(items[k]) if 0 <= k < len(items) else None


def todo(st: dict, beds: bool) -> dict:
    return {"proxies": [k for k, s in st["sources"].items() if not s["ready"] and s["path"]],
            "cards": [g for g, c in st["cards"].items() if c["state"] == "todo"],
            "beds": bool(beds and (st["beds"] is None or not st["beds"]["fresh"]))}


def prepare_steps(work: Path, film: str, beds: bool, gfx_busy: Callable[[], bool]) -> list[Step]:
    """One job: decide at RUN time what is missing (a queued job sees the newest EDL), then mediaconv.py makes it.
    gfx_busy: a graphics render is running (gfx/render.mjs writes the .mov files): cards wait for the next run."""
    plan = read_json(work / f"plan_{film}.json")
    edl_path = work / plan["edl_out"]
    cdir = cache_dir(work)
    tasks_p = cdir / f"tasks_{film}.json"
    keep: dict[str, list[str]] = {}

    def plan_tasks(job: Job) -> None:
        if not edl_path.is_file():
            raise StepFailed("brak EDL filmu: najpierw „Sprawdź”")
        for q in cdir.glob("*.part"):  # left by a stopped run
            q.unlink(missing_ok=True)
        raw = edl_path.read_bytes()  # one read: the beds' copy and their version stamp are the same EDL
        try:
            edl = json.loads(raw)
        except ValueError as e:
            raise StepFailed(f"EDL filmu nieczytelny (właśnie się zapisuje?): {e}") from e
        st = state(work, film, edl_path, edl)
        need = todo(st, beds)
        if need["cards"] and gfx_busy():
            job.say("grafiki właśnie się renderują: karty przygotuję po ich zakończeniu")
            need["cards"] = []
        errs = [s["error"] for s in st["sources"].values() if s["error"]]
        if errs:
            raise StepFailed("; ".join(errs))
        target = card_target(edl)
        tasks: dict = {"proxies": [], "cards": [], "beds": None}
        for key in need["proxies"]:
            s = st["sources"][key]
            tasks["proxies"].append({"src": s["path"], "out": s["file"], "dur": _duration(Path(s["path"]))})
        for gid in need["cards"]:
            c = st["cards"][gid]
            tasks["cards"].append({"id": gid, "src": c["mov"], "out": c["file"], "mtime_ns": c["mtime_ns"], **target})
        if need["beds"]:
            bd = beds_dir(work, film)
            new = bd.with_name(bd.name + ".new")  # made aside and swapped in whole: a stopped mix never shows as "the beds"
            shutil.rmtree(new, ignore_errors=True)
            new.mkdir(parents=True)
            copy = new / "edl.json"  # make_beds.py writes the bed paths into the EDL it gets: never the project's
            copy.write_bytes(raw)
            tasks["beds"] = {"python": pipeline.VENV_PY, "script": str(PIPELINE / "music" / "make_beds.py"), "edl": str(copy),
                             "dir": str(new), "final": str(bd), "version": version_of(raw), "cwd": str(work)}
        keep["files"] = [s["file"] for s in st["sources"].values() if s.get("file")] + \
                        [c["file"] for c in st["cards"].values() if c.get("file")]
        atomic_write(tasks_p, json.dumps(tasks, ensure_ascii=False))
        job.result["todo"] = {"proxies": len(tasks["proxies"]), "cards": len(tasks["cards"]), "beds": bool(tasks["beds"])}
        job.say(f"do przygotowania: kopie nagrań {len(tasks['proxies'])}, karty {len(tasks['cards'])}, "
                f"muzyka i SFX {'tak' if tasks['beds'] else 'nie'}")

    def cleanup(job: Job) -> None:
        """Older copies of the same recording / card (other stamp) are dead weight: ~100 MB per recording."""
        current = {Path(f).name for f in keep.get("files", [])}
        prefixes = {version_prefix(n) for n in current}
        for q in list(cdir.glob("*.mp4")) + list(cdir.glob("*.webm")):
            if q.name not in current and version_prefix(q.name) in prefixes:
                q.unlink(missing_ok=True)
                q.with_suffix(".json").unlink(missing_ok=True)

    return [plan_tasks, Cmd([sys.executable, str(CONV), str(tasks_p)], work, timeout=LIMIT), cleanup]


def version_prefix(name: str) -> str:
    """src_<path>_ / card_<id>_<target>_ : files with the same prefix are versions of one input."""
    return re.sub(r"_[0-9a-f]{12}\.(mp4|webm)$", "_", name)


def _duration(p: Path) -> float:
    """For the progress percentage only: 0 when ffprobe cannot tell (the copy is made anyway)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", f"file:{p}"],
                           capture_output=True, text=True, timeout=30, check=True)
        return float(r.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        return 0.0

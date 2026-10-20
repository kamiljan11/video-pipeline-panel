"""Project files on disk: discovery, reading, and saving with a timestamped backup + undo/redo.

A project = a pipeline work dir (e.g. ~/Videos/Montaz/2026-10-20/work) with plan_<film>.json files.
The panel edits ONLY plan_<film>.json and gfx_specs.json; edl_*.json and the rest are pipeline output.
Backups go to ~/video-pipeline/render/panel/history/ (never into ~/Videos: it is synced by Syncthing).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PIPELINE = Path(__file__).resolve().parents[2]
RENDER = PIPELINE / "render"


def panel_dir() -> Path:
    """The panel's own scratch/backup area. MONTAZ_PANEL_DIR moves it (tests use a temp dir, never the real one)."""
    return Path(os.environ.get("MONTAZ_PANEL_DIR") or RENDER / "panel")


KEEP_BACKUPS = 500
KEEP_STALE_GFX = 50
log = logging.getLogger("montazownia.store")


# run_v4.sh pastes the film name into shell and `python3 -c` code: only these characters may ever reach it
FILM_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def films(work: Path) -> list[str]:
    names = sorted(p.stem.removeprefix("plan_") for p in work.glob("plan_*.json"))
    bad = [n for n in names if not FILM_NAME.fullmatch(n)]
    if bad:
        log.warning("ignored plan files with unsafe names (letters, digits, _ and - only): %s", bad)
    return [n for n in names if n not in bad]


def plan_path(work: Path, film: str) -> Path:
    if not FILM_NAME.fullmatch(film or ""):
        raise FileNotFoundError(film)
    p = work / f"plan_{film}.json"
    if not p.is_file():
        raise FileNotFoundError(p)
    return p


def read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def words_of(work: Path, plan: dict) -> list[dict]:
    return read_json(work / plan["words"])["transcription"]


def specs_path(work: Path, plan: dict) -> Path | None:
    return work / plan["gfx_specs"] if plan.get("gfx_specs") else None


def project_key(work: Path) -> str:
    return hashlib.sha1(str(work.resolve()).encode()).hexdigest()[:10]


# per-project areas under panel_dir(); the caches can be rebuilt, history (backups) is never pruned here
CACHE_KINDS = ("tmp", "preview", "beds", "stale-gfx")


def project_dir(kind: str, work: Path) -> Path:
    """panel_dir()/<kind>/<project key>, with PROJECT.txt naming the work dir (lets a human, and the pruner, see whose it is)."""
    d = panel_dir() / kind / project_key(work)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / "PROJECT.txt"
    if not marker.exists():
        marker.write_text(str(work.resolve()) + "\n", encoding="utf-8")
    return d


def prune_dead_projects() -> list[str]:
    """Drop the caches (beds ~300 MB, previews) of projects whose work dir is gone, e.g. a deleted copy or test run."""
    root, dead = panel_dir(), set()
    for marker in root.glob("*/*/PROJECT.txt"):
        try:
            gone = not Path(marker.read_text(encoding="utf-8").strip()).is_dir()
        except OSError:
            continue
        if gone:
            dead.add(marker.parent.name)
    removed = set()
    for kind in CACHE_KINDS:
        for key in dead:
            d = root / kind / key
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed.add(key)
    return sorted(removed)  # history of a dead project stays, so it is "dead" on every start: report only real removals


def retire_gfx_file(work: Path, mov: Path) -> Path | None:
    """gfx/render.mjs skips ids whose .mov exists: move the file aside (last KEEP_STALE_GFX kept) so it is rendered again."""
    if not mov.exists():
        return None
    dst = project_dir("stale-gfx", work)
    moved = dst / f"{mov.stem}.{time.strftime('%Y%m%d-%H%M%S')}.mov"
    shutil.move(str(mov), moved)
    for old in sorted(dst.glob("*.mov"), key=lambda q: q.stat().st_mtime)[:-KEEP_STALE_GFX]:
        old.unlink(missing_ok=True)
    return moved


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def atomic_write(p: Path, text: str) -> None:
    tmp = p.with_name(f".{p.name}.panel-tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


class ConflictError(RuntimeError):
    """A file changed outside the panel since the change being undone/redone."""


@dataclass
class Change:
    label: str
    files: dict[str, tuple[str, str]]  # path -> (before, after)

    def paths(self) -> list[Path]:
        return [Path(f) for f in self.files]


@dataclass
class History:
    """Saves with a backup copy first; undo/redo of whole changes (one change may touch plan + specs)."""
    work: Path
    backup_root: Path = field(default_factory=lambda: panel_dir() / "history")
    done: list[Change] = field(default_factory=list)
    undone: list[Change] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def backup_dir(self) -> Path:
        d = self.backup_root / project_key(self.work)
        d.mkdir(parents=True, exist_ok=True)
        (d / "PROJECT.txt").write_text(str(self.work.resolve()) + "\n", encoding="utf-8")
        return d

    def save(self, label: str, updates: dict[Path, Any]) -> Change:
        """Write new contents of several files as one undoable change."""
        with self.lock:
            files = {}
            for p, data in updates.items():
                before = p.read_text(encoding="utf-8")
                after = _dump(data)
                if before != after:
                    files[str(p)] = (before, after)
            change = Change(label, files)
            if files:
                self._apply(change, "after")
                self.done.append(change)
                self.undone.clear()
                log.info("saved %s: %s", label, ", ".join(Path(f).name for f in files))
            return change

    def undo(self) -> Change | None:
        with self.lock:
            if not self.done:
                return None
            c = self.done[-1]
            self._apply(c, "before")  # may raise ConflictError: the change then stays where it was
            self.undone.append(self.done.pop())
            log.info("undo %s", c.label)
            return c

    def redo(self) -> Change | None:
        with self.lock:
            if not self.undone:
                return None
            c = self.undone[-1]
            self._apply(c, "after")
            self.done.append(self.undone.pop())
            log.info("redo %s", c.label)
            return c

    def _apply(self, c: Change, side: str) -> None:
        d = self.backup_dir()
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
        for f, (before, after) in c.files.items():
            current = Path(f).read_text(encoding="utf-8")
            expected = before if side == "after" else after
            if current != expected:
                # someone else (critic --fix, a render, another session) changed the file since: never overwrite that
                raise ConflictError(f"{Path(f).name} zmienił się poza panelem; cofanie przerwane, odśwież projekt")
        for f in c.files:
            p = Path(f)
            text = p.read_text(encoding="utf-8")
            (d / f"{p.stem}.{stamp}{p.suffix}").write_text(text, encoding="utf-8")  # copy BEFORE every write
            first = d / "original" / p.name  # the file as it was before the panel ever touched it: _prune never removes it
            if not first.exists():
                first.parent.mkdir(exist_ok=True)
                atomic_write(first, text)  # never rewritten later: a half file here would be the "original" for good
        written: list[tuple[Path, str]] = []
        try:
            for f, (before, after) in c.files.items():
                p = Path(f)
                old = p.read_text(encoding="utf-8")
                atomic_write(p, after if side == "after" else before)
                written.append((p, old))
        except OSError:
            for p, old in reversed(written):  # plan + specs change together or not at all
                atomic_write(p, old)
            raise
        self._prune(d)

    def _prune(self, d: Path) -> None:
        """Keep the newest KEEP_BACKUPS copies per project (a copy is written before every save); original/ stays."""
        olds = sorted(d.glob("*.json"), key=lambda q: q.stat().st_mtime)[:-KEEP_BACKUPS]
        for q in olds:
            q.unlink(missing_ok=True)

    def state(self) -> dict:
        return {"undo": self.done[-1].label if self.done else None, "redo": self.undone[-1].label if self.undone else None}

"""Media for the live player, run by the panel as ONE job step (a subprocess: Stop and the time limit reach every ffmpeg).

    python3 mediaconv.py <tasks.json>

tasks.json (written by livemedia.prepare_steps right before this runs):
  {"proxies": [{"src", "out", "dur"}], "cards": [{"id", "src", "out", "mtime_ns", "mode": "fit"|"scale", ...}],
   "beds": {"python", "script", "edl", "dir", "final", "version"} | null}

Stdlib only (runs as a plain script, no package imports). Every file is written as <name>.part and renamed when complete,
so a stopped or failed run never leaves a half file under its final name. Errors are printed as lines starting with
"BŁĄD" (the player shows the last one) and the exit code is 1 when anything failed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROXY_H = 720  # the player scales a 9:16 crop of it up to a phone-sized frame: 540p was too soft there
CARD_WORKERS = 6
FFMPEG = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-protocol_whitelist", "file"]  # inputs come from an EDL: local files only


def say(line: str) -> None:
    print(line, flush=True)


def part(out: Path) -> Path:
    return out.with_name(out.name + ".part")


def tail(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1][:300] if lines else "brak komunikatu ffmpeg"


def proxy(t: dict) -> str | None:
    """Low-res H.264 copy: keyframe every 0.5 s (seeking to any cut is quick), loudness-normalised AAC
    (close to the voice level of the final mix, so music and SFX beds sit about right)."""
    out, src, dur = Path(t["out"]), t["src"], max(float(t.get("dur") or 0), 0.001)
    tmp = part(out)
    argv = [*FFMPEG, "-i", f"file:{src}", "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", f"scale=-2:{PROXY_H}:flags=bicubic", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-g", "15", "-keyint_min", "15", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
            "-af", "highpass=f=70,loudnorm=I=-14:TP=-1.0:LRA=9,aresample=48000", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart", "-f", "mp4", "-progress", "pipe:1", "-stats_period", "5", str(tmp)]
    say(f"kopia nagrania {Path(src).name} -> {out.name}")
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    except OSError as e:  # no ffmpeg: one named error instead of a traceback that hides the others
        return f"BŁĄD kopia nagrania {Path(src).name}: {type(e).__name__} {e}"
    assert proc.stdout is not None and proc.stderr is not None
    errbuf: list[str] = []
    drain = threading.Thread(target=lambda: errbuf.append(proc.stderr.read() if proc.stderr else ""), daemon=True)
    drain.start()  # a broken recording can fill the stderr pipe while we read progress: ffmpeg would block forever
    last = -1
    for line in proc.stdout:
        if line.startswith("out_time_us=") and line.strip() != "out_time_us=N/A":
            pct = min(99, int(int(line.split("=", 1)[1]) / 1e6 / dur * 100))
            if pct != last:
                say(f"postęp {pct}%")
                last = pct
    rc = proc.wait()
    drain.join()
    err = "".join(errbuf)
    if rc != 0:
        tmp.unlink(missing_ok=True)
        return f"BŁĄD kopia nagrania {Path(src).name}: {tail(err)}"
    os.replace(tmp, out)
    say("postęp 100%")
    return None


def probe(p: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", f"file:{p}"], capture_output=True, text=True, timeout=60, check=True)
    d = json.loads(r.stdout)
    st = d["streams"][0]
    return {"w": int(st["width"]), "h": int(st["height"]), "dur": float(d.get("format", {}).get("duration") or 0)}


def card(t: dict) -> str | None:
    """VP9 with alpha in WebM (Chrome plays it with transparency). fit = the film frame size (9:16 shorts scale every card
    to the frame like assemble.py); scale = k x the card's own size (16:9: assemble overlays it unscaled at 0,0)."""
    out = Path(t["out"])
    tmp = part(out)
    try:
        changed = os.stat(t["src"]).st_mtime_ns != t["mtime_ns"]
    except OSError:
        changed = True
    if changed:  # an edit retired it or a graphics render rewrote it since the job was planned: next run takes the new one
        say(f"karta {t['id']} zmieniła się w trakcie: przygotuję ją przy następnym przebiegu")
        return None
    if t["mode"] == "fit":
        vf = f"scale={t['w']}:{t['h']}:flags=bicubic"
    else:
        k = float(t["k"])
        vf = f"scale=trunc(iw*{k:.6f}/2)*2:trunc(ih*{k:.6f}/2)*2:flags=bicubic"
    argv = [*FFMPEG, "-i", f"file:{t['src']}", "-an", "-vf", f"{vf},format=yuva420p", "-c:v", "libvpx-vp9",
            "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-crf", "36", "-b:v", "0", "-auto-alt-ref", "0",
            "-f", "webm", str(tmp)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=900, check=False)
        if r.returncode != 0:
            tmp.unlink(missing_ok=True)
            return f"BŁĄD karta {t['id']}: {tail(r.stderr)}"
        meta = probe(tmp)
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, IndexError) as e:
        tmp.unlink(missing_ok=True)
        return f"BŁĄD karta {t['id']}: {type(e).__name__} {e}"
    full_w, full_h = (t["w"], t["h"]) if t["mode"] == "fit" else (t["fullw"], t["fullh"])
    meta.update({"fw": round(meta["w"] / full_w, 4), "fh": round(meta["h"] / full_h, 4)})
    os.replace(tmp, out)
    out.with_suffix(".json").write_text(json.dumps(meta))  # written after the webm: the player needs both
    say(f"karta {t['id']} gotowa")
    return None


def beds(t: dict) -> str | None:
    """music/make_beds.py on a copy of the EDL (it writes the bed paths into the EDL it gets) into a fresh dir, which then
    replaces the old beds whole (a stopped mix never leaves half a wav under the player); stamp = EDL version."""
    new, final = Path(t["dir"]), Path(t["final"])
    try:
        r = subprocess.run([t["python"], t["script"], t["edl"], str(new)], capture_output=True, text=True, errors="replace",
                           timeout=t.get("timeout", 600), cwd=t.get("cwd"), check=False)
    except (subprocess.SubprocessError, OSError) as e:  # time limit or a missing interpreter: report it with the other errors
        shutil.rmtree(new, ignore_errors=True)
        return f"BŁĄD muzyka i SFX: {type(e).__name__} {e}"
    if r.returncode != 0:
        shutil.rmtree(new, ignore_errors=True)
        return f"BŁĄD muzyka i SFX: {tail(r.stderr or r.stdout)}"
    (new / "edl.version").write_text(t["version"])
    old = final.with_name(final.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if final.exists():
        os.replace(final, old)  # an <audio> still reading the old file keeps it (open files survive the rename)
    os.replace(new, final)
    shutil.rmtree(old, ignore_errors=True)
    say("muzyka i SFX gotowe")
    return None


def main(tasks_path: str) -> int:
    t0 = time.time()
    tasks = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
    errors: list[str] = []
    cards = tasks.get("cards", [])
    if cards:  # first: seconds, and checked against the files right before; the recording copy takes minutes
        say(f"karty: {len(cards)} do przygotowania")
        with ThreadPoolExecutor(min(CARD_WORKERS, os.cpu_count() or 2)) as ex:
            errors += [e for e in ex.map(card, cards) if e]
    errors += [e for e in (proxy(p) for p in tasks.get("proxies", [])) if e]
    if tasks.get("beds"):
        say("muzyka i SFX (make_beds.py)…")
        errors += [e for e in [beds(tasks["beds"])] if e]
    for e in errors:
        say(e)
    say(f"gotowe w {time.time() - t0:.0f} s" if not errors else f"{len(errors)} błędów")
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("użycie: mediaconv.py <tasks.json>")
    sys.exit(main(sys.argv[1]))

"""Source media for the UI: voice waveform (peaks) and single frames, both cached in render/panel/cache."""
from __future__ import annotations

import array
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

from .store import atomic_write, panel_dir

PEAK_RATE = 50  # peaks per second of SOURCE audio
SAMPLE_RATE = 4000


def _cache() -> Path:
    d = panel_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(src: str, extra: str = "") -> str:
    st = Path(src).stat()
    return hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}|{extra}".encode()).hexdigest()[:16]


def peaks(src: str) -> dict:
    """Max |amplitude| per 1/PEAK_RATE s of the whole source, 0..255; computed once per source file."""
    out = _cache() / f"peaks_{_key(src)}.json"
    if out.exists():
        return json.loads(out.read_text())
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
                       capture_output=True, timeout=600, check=True)
    pcm = array.array("h")
    pcm.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    step = SAMPLE_RATE // PEAK_RATE
    vals = []
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        vals.append(max(max(chunk), -min(chunk)))
    top = max(vals, default=0) or 1  # silent or empty audio: a flat line (no audio stream at all: ffmpeg fails above)
    data = {"rate": PEAK_RATE, "peaks": [min(255, v * 255 // top) for v in vals]}
    atomic_write(out, json.dumps(data))  # a half-written cache file would break the waveform for good
    return data


def frame(src: str, t: float, width: int = 640) -> Path:
    """JPEG of the source frame at t seconds (for the crop preview)."""
    out = _cache() / f"frame_{_key(src, f'{t:.2f}-{width}')}.jpg"
    if not out.exists():
        tmp = out.with_name(f".{out.name}.{os.getpid()}-{threading.get_ident()}.part.jpg")  # two requests for one frame: own files
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", src, "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-q:v", "4", str(tmp)], capture_output=True, timeout=60, check=True)
            os.replace(tmp, out)  # past the end: ffmpeg fails or writes nothing (version-dependent); no half file is left
        finally:
            tmp.unlink(missing_ok=True)
    return out

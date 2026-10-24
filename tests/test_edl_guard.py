"""edl_guard.py: what an EDL may carry into ffmpeg (assemble.py checks it before any ffmpeg, behind.py uses its rules).
Every EDL of the sample and the current projects passes unchanged; text that would become a filter, an option or a
protocol is refused with the field named. assemble.py itself: tests/test_pipeline_compat.py (CompatTest.test_assemble_refuses_filter_text_from_the_edl_before_any_ffmpeg)."""
import importlib.util
import json
import unittest
from pathlib import Path
from typing import Any

PIPE = Path(__file__).resolve().parents[2]


def _load(name: str) -> Any:
    """a pipeline script next to the panel, not a package: loaded by path (as tests/test_beats.py)"""
    spec = importlib.util.spec_from_file_location(name, PIPE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


edl_guard = _load("edl_guard")

SAMPLE = Path.home() / "Videos" / "Montaz" / "2026-10-20" / "work"


def base():
    return {"sources": {"A": "/x/src.mp4"}, "segments": [{"src": "A", "in": 0.0, "out": 1.0, "zoom": 1.0}],
            "overlays": [{"id": "g", "file": "gfx/g.mov", "at": 0.5, "dur": 1.0}], "w": 2560, "h": 1440, "face": [0.5, 0.45]}


class EdlGuardTest(unittest.TestCase):
    def test_every_real_edl_passes(self):
        files = sorted(SAMPLE.glob("edl*.json")) + sorted((PIPE / "render" / "montaz").glob("*/work/edl*.json"))
        self.assertTrue(files, "no EDL to check (sample project missing?)")
        for f in files:
            with self.subTest(f=f.name):
                self.assertEqual(edl_guard.problems(json.loads(f.read_text())), [])

    def test_the_voice_chain_of_the_plans_and_the_default_grade_pass(self):
        self.assertEqual(edl_guard.problems({**base(), "audio": "highpass=f=70,afftdn=nf=-30,acompressor=threshold=-20dB:ratio=3:"
                                             "attack=10:release=150:makeup=2,loudnorm=I=-14:TP=-1.0:LRA=9",
                                             "grade": "eq=contrast=1.04:saturation=1.07:gamma=0.98,unsharp=5:5:0.35"}), [])

    def test_text_that_would_become_a_filter_option_or_protocol_is_refused(self):
        bad = {"face": [0.5, "0.45)),vidstabdetect=result=/tmp/x,crop=iw:ih:0:((0"], "face_target_y": "0.4,movie=x",
               "src_w": "2560", "w": None, "vertical": "yes", "tail": "1,amovie=x", "music_db": "-13dB,amovie=x",
               "pip_size": float("nan"), "audio": "anull,amovie=/etc/passwd", "grade": "null,movie=/etc/passwd",
               "subs": "/x/a.ass':force_style='x", "sub_style": "x'", "music": "http://evil/x.wav",
               "theme": {"name": "t", "acc": "#fff;drawtext"}}
        for k, v in bad.items():
            with self.subTest(k=k):
                found = edl_guard.problems({**base(), k: v})
                self.assertTrue(found, k)
                with self.assertRaises(edl_guard.EdlError):
                    edl_guard.check({**base(), k: v})

    def test_filter_chains_only_allowed_filters_and_plain_values(self):
        for chain in ("highpass=f=70[x];[x]anull", "volume=1:enable='between(t,1,2)'", "ametadata=mode=print:file=/tmp/x",
                      "highpass=f=70,", "loudnorm=I=-14\\,amovie=x", "", "volume=a/b"):
            with self.subTest(chain=chain):
                self.assertFalse(edl_guard.chain_ok(chain, edl_guard.AUDIO))

    def test_inputs_are_local_files(self):
        for path, ok in (("/home/x/a.mp4", True), ("gfx/a.mov", True), ("/x/a:b.mp4", True), ("concat:/a|/b", False),
                         ("subfile:,start,0,end,1,:/x", False), ("subfile,,start,0,end,100,,:/etc/hostname", False),
                         ("data:x", False), ("", False)):
            with self.subTest(path=path):
                self.assertEqual(edl_guard.local_file(path), ok)

    def test_segments_zoom_face_track(self):
        for seg, ok in (({"zoom": {"a": 1, "b": 1.1, "t0": 0, "t1": 1, "keys": [[0, 1], [1, 1.1]]}}, True),
                        ({"zoom": "1,movie=x"}, False), ({"face": [0.5, "x"]}, False), ({"track": [[0, "x"]]}, False),
                        ({"zoom": {"a": 1, "b": "2", "t0": 0, "t1": 1}}, False), ({"zoom": 10**400}, False),
                        ({"zoom": 0}, False), ({"zoom": {"a": 0, "b": 1.1, "t0": 0, "t1": 1}}, False)):
            with self.subTest(seg=seg):
                self.assertEqual(edl_guard.segment_ok({"in": 0, "out": 1, **seg}), ok)


    def test_output_name_segments_and_null_are_named_faults_not_crashes(self):
        """out_name goes to ffmpeg -y as the output path; a bad segment or a null must not end in a traceback"""
        for k, v in (("out_name", "/home/x/.bashrc"), ("out_name", "../../x.mp4"), ("out_name", ".hidden.mp4"),
                     ("out_name", "film"), ("out_name", None), ("music_db", None), ("tail", None), ("tail", 1e8),
                     ("tail", -1)):
            with self.subTest(k=k, v=v):
                self.assertTrue(edl_guard.problems({**base(), k: v}))
        for seg in ({"src": ["A"], "in": 0, "out": 1}, {"src": {"A": 1}, "in": 0, "out": 1}, {"src": "A", "in": 5, "out": 1},
                    {"src": "A", "in": 1, "out": 1}):
            with self.subTest(seg=seg), self.assertRaises(edl_guard.EdlError):
                edl_guard.check({**base(), "segments": [seg]})
        self.assertEqual(edl_guard.problems({**base(), "out_name": "95procent_v2_long.mp4", "tail": 1.2}), [])


if __name__ == "__main__":
    unittest.main()

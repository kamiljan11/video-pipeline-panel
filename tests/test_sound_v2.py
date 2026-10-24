"""Sound v2: plan keys "sfx_style": "v2" (sounds by motion type) and "music_moods" ([[t_out, mood], ...]) in music/make_beds.py + gen_music.py.

Without the keys both scripts must give the same bytes as before (pinned to the commit the keys were added on top of); the sounds are checked
by where their energy lands (typed letters, list clicks, riser into impact) and by level/brightness per mood. Short EDLs only.
Runs the scripts in the pipeline venv (NumPy); the checks themselves are plain stdlib.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

PIPE = Path(__file__).resolve().parents[2]
PY = PIPE / ".venv" / "bin" / "python"
BASE_REV = "db7219f"  # the make_beds / gen_music before sound v2; without it the tests fail (comparing HEAD with itself proves nothing)
NEW_SFX = ("tick", "type", "click", "riser", "impact", "paper", "scatter", "roar", "swoosh", "drop")
SEG = [{"src": "A", "in": 0.0, "out": 6.0, "zoom": 1.0}]

ANALYZE = r"""
import json, sys, wave
import numpy as np
req = json.load(sys.stdin)
w = wave.open(req["wav"]); sr = w.getframerate()
x = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2).mean(1) / 32768
def seg(t0, t1): return x[int(t0 * sr):int(t1 * sr)]
out = []
for op, *a in req["ops"]:
    if op == "peak": out.append(float(np.abs(seg(*a)).max()))
    elif op == "rms": out.append(float(np.sqrt((seg(*a[:2]) ** 2).mean())))
    elif op == "argmax": out.append(a[0] + int(np.abs(seg(*a)).argmax()) / sr)
    elif op == "zcr": out.append(float((np.diff(np.sign(seg(*a))) != 0).mean()))
    elif op == "centroid":
        s = seg(*a); m = np.abs(np.fft.rfft(s)); out.append(float((m * np.fft.rfftfreq(len(s), 1 / sr)).sum() / m.sum()))
    elif op == "onsets":  # 2 ms frames over t0..t1: a frame above frac * the loudest that is 2.5x the 6 ms before it
        t0, t1, frac = a; s = np.abs(seg(t0, t1)); f = 2 * sr // 1000
        env = np.array([s[i:i + f].max() for i in range(0, len(s) - f, f)]); thr = frac * env.max()
        out.append([t0 + i * f / sr for i in range(3, len(env)) if env[i] > thr and env[i] > 2.5 * env[i - 3:i].max()])
print(json.dumps(out))
"""
# every CARD_SFX sound as the script builds it: runpy runs make_beds on a tiny EDL, then each sound is made once
RAW = r"""
import json, runpy, sys
make_beds = sys.argv[3]; sys.argv = ["make_beds.py", sys.argv[1], sys.argv[2]]
res = {}
for name, fn in runpy.run_path(make_beds, run_name="__main__")["CARD_SFX"].items():
    sig, gain, dt = fn(); s = sig * gain
    res[name] = [float(abs(s).max()), float((s ** 2).mean() ** 0.5), len(sig) / 44100]
print(json.dumps(res))
"""


@unittest.skipUnless(PY.exists(), "pipeline venv (NumPy) missing")
class SoundV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sound-v2-"))
        if subprocess.run(["git", "-C", str(PIPE), "cat-file", "-e", f"{BASE_REV}:music/make_beds.py"], check=False, capture_output=True).returncode != 0:
            raise AssertionError(f"commit {BASE_REV} (make_beds before sound v2) missing: the 'same bytes without the keys' check has nothing to compare with")
        rev = BASE_REV
        cls.old = cls.tmp / "old"; cls.old.mkdir()
        for f in ("make_beds.py", "gen_music.py"):  # make_beds calls gen_music.py from its own dir
            (cls.old / f).write_bytes(subprocess.run(["git", "-C", str(PIPE), "show", f"{rev}:music/{f}"], check=True, capture_output=True).stdout)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def beds(self, edl, specs=None, script=None):
        """runs make_beds on a copy of the EDL -> (completed process, output dir)"""
        SoundV2Test.n += 1; d = self.tmp / f"run{self.n}"; d.mkdir()
        if specs is not None:
            (d / "specs.json").write_text(json.dumps(specs)); edl = {**edl, "gfx_specs": str(d / "specs.json")}
        (d / "edl.json").write_text(json.dumps(edl))
        r = subprocess.run([str(PY), str(script or PIPE / "music" / "make_beds.py"), str(d / "edl.json"), str(d / "out")], capture_output=True, text=True, check=False)
        return r, d / "out"

    def ok(self, edl, specs=None, script=None):
        r, out = self.beds(edl, specs, script)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out

    def an(self, wav, *ops):
        r = subprocess.run([str(PY), "-c", ANALYZE], input=json.dumps({"wav": str(wav), "ops": ops}), capture_output=True, text=True, check=True)
        return json.loads(r.stdout)

    def edl(self, cards, **keys):
        """cards: [(id, at, spec)] -> (edl, specs); the spec's dur is the overlay's"""
        specs = [{"id": i, **sp} for i, _, sp in cards]
        return {"segments": SEG, "tail": 0, "overlays": [{"id": i, "at": at, "dur": sp.get("dur", 2.0)} for i, at, sp in cards], **keys}, specs

    # --- without the keys: the same bytes as before ---
    def test_without_keys_both_files_are_identical_to_before(self):
        cards = [("m", 0.5, {"type": "maxim", "text": "ab cd", "dur": 2.0}), ("l", 1.0, {"type": "list", "delays": [0.4, 0.8]}), ("k", 1.5, {"type": "kw"}),
                 ("c", 2.0, {"type": "count", "text": "9%"}), ("ch", 2.5, {"type": "chap"}), ("s", 3.0, {"type": "slide"}), ("e", 3.5, {"type": "end"}),
                 ("t", 4.0, {"type": "cta"}), ("f", 4.2, {"type": "float", "sfx": [[0.1, "pen"], [0.5, "pop"], [0.8, "whoosh"], [1.0, "ding"]]}),
                 ("lo", 4.8, {"type": "lower"})]
        edl, specs = self.edl(cards)
        edl["segments"] = [{"src": "A", "in": 0.0, "out": 3.0, "zoom": {"a": 1.0, "b": 1.2, "t0": 1.0, "t1": 1.3}}, {"src": "A", "in": 3.0, "out": 6.0, "zoom": 1.0}]
        old = self.ok(edl, specs, self.old / "make_beds.py")
        for name, extra in (("none", {}), ("v1 and empty moods", {"sfx_style": "v1", "music_moods": []})):
            new = self.ok({**edl, **extra}, specs)
            for f in ("sfx.wav", "music.wav"):
                with self.subTest(keys=name, file=f):
                    self.assertEqual((old / f).read_bytes(), (new / f).read_bytes())

    # --- sfx_style v2 ---
    def test_v2_maxim_ticks_once_per_letter_when_the_letter_is_typed_and_no_whoosh(self):
        cases = {"spec at": ({"at": [0.5, 1.5], "text": "ab cd", "dur": 3.0}, [0.5, 0.545, 1.5, 1.545]),  # CHAR_MAX .045 s per letter inside a word
                 "even pace + a lone star": ({"text": "ab * cd", "dur": 4.0}, [0.25, 0.295, 0.4, 0.445])}  # TYPE_START .25, span = 6 letters*CHAR_EVEN*.. = .3
        for name, (sp, rel) in cases.items():
            with self.subTest(name):
                edl, specs = self.edl([("m", 1.0, {"type": "maxim", **sp})], sfx_style="v2")
                on, pk = self.an(self.ok(edl, specs) / "sfx.wav", ["onsets", 0.0, 5.0, 0.12], ["peak", 0.0, 1.0 + rel[0] - 0.01])
                self.assertEqual(len(on), 4, on)
                for got, want in zip(on, rel, strict=True): self.assertAlmostEqual(got, 1.0 + want, delta=0.012)
                self.assertEqual(pk, 0.0)  # nothing before the first letter: no whoosh into the card
        edl, specs = self.edl([("m", 1.0, {"type": "maxim", "text": "ab", "dur": 3.0})])  # v1 keeps its soft whoosh
        self.assertGreater(self.an(self.ok(edl, specs) / "sfx.wav", ["peak", 0.8, 1.4])[0], 0.1)

    def test_v2_list_clicks_land_at_card_start_plus_delay(self):
        edl, specs = self.edl([("l", 1.0, {"type": "list", "dur": 3.0, "delays": [0.5, 1.5]})], sfx_style="v2")
        wav = self.ok(edl, specs) / "sfx.wav"
        for dl in (0.5, 1.5):
            t = 1.0 + dl; (peak_at,) = self.an(wav, ["argmax", t - 0.06, t + 0.06])
            self.assertGreaterEqual(peak_at, t - 0.001); self.assertLess(peak_at, t + 0.006)  # the click is the sharpest moment around the item
        self.assertGreater(self.an(wav, ["peak", 1.0, 1.4])[0], 0.05)  # the header slides in first

    def test_v2_count_ticks_rise_while_digits_count_and_chap_rises_into_an_impact(self):
        edl, specs = self.edl([("c", 1.0, {"type": "count", "text": "95%", "dur": 3.0}), ("ch", 5.0, {"type": "chap", "dur": 1.0})], sfx_style="v2")
        wav = self.ok(edl, specs) / "sfx.wav"
        pop, ticks = self.an(wav, ["onsets", 0.9, 1.2, 0.3], ["onsets", 1.3, 2.7, 0.04])
        self.assertAlmostEqual(pop[0], 1.03, delta=0.01)
        self.assertGreaterEqual(len(ticks), 18)  # countSeconds(3.0) = 1.2 s, one tick per 50 ms from COUNT_DELAY .35 s
        self.assertAlmostEqual(ticks[0], 1.35, delta=0.012)
        quiet, swell, hit, rest = self.an(wav, ["rms", 3.9, 4.1], ["rms", 4.7, 4.95], ["argmax", 4.95, 5.2], ["peak", 3.0, 3.99])
        self.assertEqual((quiet, rest), (0.0, 0.0))  # the riser starts .9 s before the card
        self.assertGreater(swell, 0.02)
        self.assertGreaterEqual(hit, 4.99); self.assertLess(hit, 5.1)  # the impact lands on the card (its loudest point is in the first 100 ms)

    def test_v2_float_is_silent_until_its_own_sfx_asks(self):
        edl, specs = self.edl([("f", 1.0, {"type": "float", "dur": 3.0})], sfx_style="v2")
        self.assertEqual(self.an(self.ok(edl, specs) / "sfx.wav", ["peak", 0, 5])[0], 0.0)
        specs[0]["sfx"] = [[1.0, "tick"]]
        (peak_at,) = self.an(self.ok(edl, specs) / "sfx.wav", ["argmax", 0, 5])
        self.assertAlmostEqual(peak_at, 2.0, delta=0.03)

    def test_every_new_card_sound_is_audible_in_the_file_and_within_range_before_normalising(self):
        cards = [("f", 0.0, {"type": "float", "dur": 22.0, "sfx": [[2 * i + 0.2, n] for i, n in enumerate(NEW_SFX)]})]
        edl, specs = self.edl(cards)  # no sfx_style: the names work in both
        edl["segments"] = [{"src": "A", "in": 0.0, "out": 22.0, "zoom": 1.0}]
        wav = self.ok(edl, specs) / "sfx.wav"
        peaks = self.an(wav, *[["peak", 2 * i, 2 * i + 2] for i in range(len(NEW_SFX))], ["peak", 0, 22])
        for n, p in zip(NEW_SFX, peaks, strict=False):
            with self.subTest(n): self.assertGreater(p, 0.03)
        self.assertLessEqual(peaks[-1], 0.8 + 1e-3)
        d = self.tmp / "raw"; d.mkdir()
        (d / "edl.json").write_text(json.dumps({"segments": SEG, "tail": 0}))
        r = subprocess.run([str(PY), "-c", RAW, str(d / "edl.json"), str(d / "out"), str(PIPE / "music" / "make_beds.py")], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        raw = json.loads(r.stdout.strip().splitlines()[-1])
        for n in ("pop", "whoosh", "ding", "pen", *NEW_SFX):
            with self.subTest(n): self.assertIn(n, raw)
        for n in NEW_SFX:
            peak, rms, _ = raw[n]
            with self.subTest(n):
                self.assertGreater(peak, 0.05); self.assertLessEqual(peak, 1.0); self.assertGreater(rms, 0.003)
        self.assertAlmostEqual(raw["roar"][2], 1.2, delta=0.05)
        self.assertAlmostEqual(raw["type"][2], 0.66, delta=0.1)  # a 0.6 s burst + the last tick's ring
        self.assertLessEqual(raw["scatter"][2], 1.0)

    # --- music_moods ---
    def test_moods_differ_keep_one_loudness_and_drop_is_nearly_silent(self):
        moods = [[0, "tension"], [4, "story"], [8, "drop"], [9, "upbeat"], [13, "resolve"]]
        out = self.ok({"segments": [{"src": "A", "in": 0.0, "out": 19.0, "zoom": 1.0}], "tail": 0, "music_moods": moods})
        wav = out / "music.wav"
        win = {"tension": (2.0, 3.2), "story": (5.0, 7.8), "upbeat": (10.0, 12.8), "resolve": (14.5, 16.8)}
        rms = {m: self.an(wav, ["rms", *w])[0] for m, w in win.items()}
        zcr = {m: self.an(wav, ["zcr", *w])[0] for m, w in win.items()}
        cen = {m: self.an(wav, ["centroid", *w])[0] for m, w in win.items()}
        mean = sum(rms.values()) / 4
        for m, v in rms.items():
            with self.subTest(m): self.assertGreater(v, mean / 1.6); self.assertLess(v, mean * 1.6)  # about the same loudness (assemble ducks under the voice)
        (drop,) = self.an(wav, ["rms", 8.2, 8.6])
        self.assertLess(drop, mean / 50)
        self.assertGreater(cen["upbeat"], 1.5 * max(cen["tension"], cen["story"], cen["resolve"]))  # drums, hats, bright plucks
        self.assertLess(zcr["tension"], min(zcr["story"], zcr["upbeat"], zcr["resolve"]))  # dark and low
        feats = {m: (rms[m] / mean, zcr[m], cen[m]) for m in rms}
        names = list(feats)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                with self.subTest(a=a, b=b):  # the sections really are different music: some feature is >15 % apart
                    self.assertTrue(any(abs(x - y) > 0.15 * max(x, y) for x, y in zip(feats[a], feats[b], strict=True)), (feats[a], feats[b]))
        with wave.open(str(wav)) as w: self.assertEqual((w.getnframes(), w.getnchannels()), (20 * 44100, 2))  # the body + 1 s, as the plain bed

    def test_moods_do_not_touch_the_sfx_and_the_same_plan_gives_the_same_music(self):
        edl, specs = self.edl([("m", 0.5, {"type": "maxim", "text": "ab", "dur": 2.0})], sfx_style="v2")
        a = self.ok(edl, specs); b = self.ok({**edl, "music_moods": [[0, "story"], [3, "resolve"]]}, specs); c = self.ok({**edl, "music_moods": [[0, "story"], [3, "resolve"]]}, specs)
        self.assertEqual((a / "sfx.wav").read_bytes(), (b / "sfx.wav").read_bytes())
        self.assertEqual((b / "music.wav").read_bytes(), (c / "music.wav").read_bytes())
        self.assertNotEqual((a / "music.wav").read_bytes(), (b / "music.wav").read_bytes())

    def test_bad_values_stop_with_a_named_error_not_a_traceback(self):
        cases = [("music_moods", "tension"), ("music_moods", [[1, "nosuch"]]), ("music_moods", [[2, "story"], [1, "upbeat"]]), ("music_moods", [[-1, "story"]]),
                 ("music_moods", [["x", "story"]]), ("music_moods", [[1, "story", 2]]), ("music_moods", [[True, "story"]]), ("sfx_style", "v3"), ("sfx_style", 2)]
        for key, bad in cases:
            with self.subTest(key=key, bad=bad):
                r, out = self.beds({"segments": SEG, "tail": 0, key: bad})
                self.assertEqual(r.returncode, 1)
                self.assertTrue(r.stderr.startswith(key), r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertFalse((out / "sfx.wav").exists())  # checked before anything is written
        r = subprocess.run([str(PY), str(PIPE / "music" / "gen_music.py"), str(self.tmp / "x.wav"), "5", "7", "[[0, \"nosuch\"]]"], capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, "Traceback" in r.stderr, r.stderr.startswith("music_moods")), (1, False, True))


if __name__ == "__main__":
    unittest.main()

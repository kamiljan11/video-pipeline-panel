"""The pipeline's behind.py tests (matte/test_behind.py: text behind the speaker) run with the panel suite in the matting
venv (onnxruntime + numpy live only there). No venv = a failure, not a skip: gfx/render.mjs needs it for every "behind" card."""
import shutil
import subprocess
import unittest
from pathlib import Path

PIPE = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("ffmpeg"), "no ffmpeg")
class BehindTests(unittest.TestCase):
    def test_behind_tests_pass(self):
        py = PIPE / "matte" / ".venv" / "bin" / "python"
        self.assertTrue(py.exists(), f"{py} missing: python3 -m venv matte/.venv && matte/.venv/bin/pip install onnxruntime numpy")
        r = subprocess.run([str(py), "-m", "unittest", "matte/test_behind.py"], cwd=PIPE, capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertRegex(r.stderr, r"Ran [1-9]\d* tests")
        self.assertNotIn("skipped", r.stderr)

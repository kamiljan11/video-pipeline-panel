"""The pipeline's JS tests (gfx/test: the "web" card's address guard, quote search, capture failures) run with the
panel suite, so a regression there fails the same gate. Needs node and /usr/bin/google-chrome (a skipped Chrome case fails here)."""
import shutil
import subprocess
import unittest
from pathlib import Path

PIPE = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("node"), "no node")
class GfxNodeTests(unittest.TestCase):
    def test_gfx_node_tests_pass(self):
        r = subprocess.run(["node", "--test", *sorted(str(p) for p in (PIPE / "gfx" / "test").glob("*.test.mjs"))],
                           cwd=PIPE, capture_output=True, text=True, timeout=300, check=False)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        self.assertRegex(r.stdout, r"ℹ pass [1-9]")
        self.assertRegex(r.stdout, r"ℹ skipped 0\b")  # the Chrome case is the proof of the network guard: never skipped silently

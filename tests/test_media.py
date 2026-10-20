"""media.py with real ffmpeg on tiny generated files: waveform peaks, frames, silent and broken sources."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from montazownia import media


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class MediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="montaz-media-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        mock.patch.dict(os.environ, {"MONTAZ_PANEL_DIR": str(self.tmp / "panel")}).start()
        self.addCleanup(mock.patch.stopall)

    def make(self, name, *lavfi):
        out = self.tmp / name
        args = [a for src in lavfi for a in ("-f", "lavfi", "-i", src)]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-t", "2", "-shortest", str(out)], check=True)
        return str(out)

    def test_peaks_scale_to_255_and_are_cached(self):
        src = self.make("tone.wav", "sine=frequency=440:sample_rate=8000")
        d = media.peaks(src)
        self.assertEqual(d["rate"], media.PEAK_RATE)
        self.assertAlmostEqual(len(d["peaks"]), 2 * media.PEAK_RATE, delta=2)
        self.assertEqual(max(d["peaks"]), 255)
        with mock.patch.object(media.subprocess, "run", side_effect=AssertionError("ffmpeg again")):
            self.assertEqual(media.peaks(src), d)

    def test_source_without_audio_is_an_error_not_a_crash(self):
        src = self.make("video.mp4", "color=c=gray:s=64x36:r=10")
        with self.assertRaises(subprocess.SubprocessError):  # -> HTTP 502 with a message, the UI shows "no waveform"
            media.peaks(src)

    def test_empty_audio_is_a_flat_line(self):
        src = self.make("tone.wav", "sine=sample_rate=8000")
        with mock.patch.object(media.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            self.assertEqual(media.peaks(src)["peaks"], [])

    def test_frame_is_a_jpeg_and_past_the_end_leaves_no_half_file(self):
        src = self.make("video.mp4", "testsrc=s=320x180:r=10")
        self.assertEqual(media.frame(src, 1.0).read_bytes()[:2], b"\xff\xd8")
        with self.assertRaises((subprocess.SubprocessError, FileNotFoundError)):  # ffmpeg version decides which
            media.frame(src, 99.0)
        self.assertEqual(list((self.tmp / "panel" / "cache").glob(".*part*")), [])

    def test_broken_source_raises_subprocess_error(self):
        bad = self.tmp / "bad.mp4"
        bad.write_bytes(b"not a video")
        for call in (lambda: media.peaks(str(bad)), lambda: media.frame(str(bad), 0.5)):
            with self.assertRaises(subprocess.SubprocessError):
                call()

    def test_hung_ffmpeg_is_a_subprocess_error(self):
        src = self.make("tone.wav", "sine=sample_rate=8000")
        with mock.patch.object(media.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 600)), \
             self.assertRaises(subprocess.SubprocessError):
            media.peaks(src)


if __name__ == "__main__":
    unittest.main()

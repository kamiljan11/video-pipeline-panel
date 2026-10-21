"""Cutting a preview window out of an EDL: graphics start whole, zoom ramps and subtitles are re-based."""
import unittest

from montazownia import preview

EDL = {"segments": [{"in": 100.0, "out": 110.0, "zoom": 1.0},
                    {"in": 200.0, "out": 210.0, "zoom": {"a": 1.0, "b": 1.1, "t0": 4.0, "t1": 4.3}}],
       "overlays": [{"id": "g", "at": 6.0, "dur": 5.0}], "tail": 3.0, "progress": True}


class PreviewTest(unittest.TestCase):
    def test_window_starts_with_a_whole_graphic(self):
        self.assertEqual(preview.window(EDL, 17.0, half=10.0), (6.0, 23.0))  # 7.0 is inside g (6-11): move to 6
        self.assertEqual(preview.window(EDL, 30.0, half=10.0), (20.0, 23.0))  # clipped to film + tail

    def test_slice_rebases_segments_overlays_and_zoom(self):
        part = preview.slice_edl(EDL, 6.0, 16.0)
        self.assertEqual([(s["in"], s["out"]) for s in part["segments"]], [(106.0, 110.0), (200.0, 206.0)])
        self.assertEqual(part["segments"][1]["zoom"], EDL["segments"][1]["zoom"])  # cut started before it: untouched
        later = preview.slice_edl(EDL, 14.1, 20.0)  # starts 4.1 s into the 2nd segment, inside the 4.0-4.3 ramp
        z = later["segments"][0]["zoom"]
        self.assertEqual((z["t0"], z["t1"]), (0.0, 0.2))
        self.assertAlmostEqual(z["a"], 1.0333, places=3)
        self.assertEqual(part["overlays"], [{"id": "g", "at": 0.0, "dur": 5.0}])
        self.assertEqual((part["progress"], part["music_offset"], part["tail"]), (False, 6.0, 0.0))

    def test_slice_keeps_later_punches_of_a_keyframed_shot(self):
        keys = [[0, 1.0], [0.2, 1.0], [0.4, 1.2], [6.0, 1.2], [6.034, 1.0], [6.2, 1.0], [6.5, 1.3]]
        edl = {"segments": [{"src": "A", "in": 10.0, "out": 20.0,
                             "zoom": {"a": 1.0, "b": 1.3, "t0": 0.2, "t1": 0.4, "keys": keys}}], "overlays": [], "tail": 0}
        z = preview.slice_edl(edl, 6.1, 9.0)["segments"][0]["zoom"]
        self.assertEqual(z["keys"], [[0.0, 1.0], [0.1, 1.0], [0.4, 1.3]])  # after the snap-back: punch again at 6.2-6.5
        z = preview.slice_edl(edl, 3.0, 9.0)["segments"][0]["zoom"]
        self.assertEqual(z["keys"][0], [0.0, 1.2])  # held punch, then snap back at 3.0 s of the window
        self.assertEqual(z["keys"][2], [3.034, 1.0])
        self.assertEqual(preview.slice_edl(edl, 7.0, 9.0)["segments"][0]["zoom"], 1.3)  # past every move: a still shot

    def test_shift_ass_keeps_window_events_from_zero(self):
        ass = "[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Cap,,0,0,0,,raz\nDialogue: 0,0:00:05.50,0:00:07.00,Cap,,0,0,0,,dwa, trzy\n"
        out = preview.shift_ass(ass, 5.0, 6.0)
        self.assertEqual(out, "[Events]\nDialogue: 0,0:00:00.50,0:00:01.00,Cap,,0,0,0,,dwa, trzy\n")

    def test_ass_time_rounds_before_carrying(self):
        self.assertEqual([preview._ass_t(x) for x in (59.996, 3599.999, 0.004, -1)], ["0:01:00.00", "1:00:00.00", "0:00:00.00", "0:00:00.00"])


if __name__ == "__main__":
    unittest.main()

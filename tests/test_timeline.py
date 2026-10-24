"""timeline.py on a synthetic EDL: output time, punch-ins the head clamp flattened, SFX marks, critic markers."""
import re
import unittest
from pathlib import Path

from montazownia import timeline
from montazownia.planops import CARD_SFX

# two shots: source 10-14 s (static) and 20-26 s (punch-in ramp at 1.0 s into the shot)
EDL = {"segments": [{"in": 10.0, "out": 14.0, "zoom": 1.0},
                    {"in": 20.0, "out": 26.0, "zoom": {"a": 1.0, "b": 1.2, "t0": 1.0, "t1": 1.3}}],
       "overlays": [{"id": "g1", "at": 2.0, "dur": 3.0}, {"id": "g2", "at": 6.0, "dur": 2.0}], "tail": 5}
SPECS = {"g1": {"id": "g1", "type": "kw", "text": "95%"}, "g2": {"id": "g2", "type": "list", "items": ["a", "b"], "delays": [0.5, 1.0]}}


def word(text, ms):
    return {"text": text, "offsets": {"from": ms, "to": ms + 300}}


class TimelineTest(unittest.TestCase):
    def test_src2out_walks_the_kept_shots(self):
        segs = EDL["segments"]
        self.assertEqual(timeline.src2out(segs, 12.0), 2.0)
        self.assertEqual(timeline.src2out(segs, 21.0), 5.0)
        self.assertEqual(timeline.src2out(segs, 16.0), 4.0)  # in a cut: start of the next shot
        self.assertEqual(timeline.src2out(segs, 99.0), 10.0)

    def test_zoom_kinds(self):
        self.assertEqual([timeline.zoom_kind(z) for z in (1.0, 1.1, {"a": 1, "b": 1.1, "t0": 0, "t1": 4}, EDL["segments"][1]["zoom"])],
                         ["static", "zoom", "drift", "emph"])

    def test_dead_punch_in_is_flagged(self):
        words = [word("raz", 11000), word("dwa", 21000)]
        plan = {"keeps": [[0, 1]], "emph": [{"word": 0}, {"word": 1}]}
        view = timeline.words_view(plan, words, EDL)
        # word 0 sits in the static shot: build_edl/face_track left no ramp there
        self.assertEqual([(w["emph"], w["emph_live"], w["out"]) for w in view], [(0, False, 1.0), (1, True, 5.0)])
        self.assertIsNone(timeline.words_view(plan, words, None)[0]["emph_live"])

    def test_web_card_whooshes_and_shows_its_quote(self):
        edl = {**EDL, "overlays": [{"id": "w", "at": 3.0, "dur": 6.0, "pip": True}]}
        specs = {"w": {"id": "w", "type": "web", "url": "https://example.org/", "find": "zdanie ze strony"}}
        self.assertIn((2.7, "whoosh"), [(round(e["t"], 3), e["kind"]) for e in timeline.sfx_events(edl, specs)])
        self.assertEqual(timeline.overlays_view(edl, specs)[0]["text"], "zdanie ze strony")

    def test_count_card_pops_like_kw(self):
        edl = {**EDL, "overlays": [{"id": "c", "at": 3.0, "dur": 4.0}]}
        ev = [(round(e["t"], 3), e["kind"]) for e in timeline.sfx_events(edl, {"c": {"id": "c", "type": "count", "text": "95%"}})]
        self.assertIn((3.03, "pop"), ev)  # the other events come from the EDL's zoom ramps

    def test_maxim_whoosh_and_a_cards_own_moments(self):
        edl = {**EDL, "overlays": [{"id": "m", "at": 3.0, "dur": 4.0}]}
        sp = {"m": {"id": "m", "type": "maxim", "text": "x", "sfx": [[1.0, "pen"], [2.0, "whoosh"], [1.5, "nosuch"], ["x", "pop"], [1.2]]}}
        ev = [(round(e["t"], 3), e["kind"]) for e in timeline.sfx_events(edl, sp)]
        self.assertIn((2.9, "whoosh"), ev)
        self.assertIn((4.0, "pen"), ev)
        self.assertIn((4.95, "whoosh"), ev)
        self.assertFalse([e for e in ev if 4.4 < e[0] < 4.6])  # malformed or unknown entries make no sound (= make_beds)
        sp["m"]["sfx"] = 5  # not a list at all: no sound, the timeline still loads
        self.assertIn((2.9, "whoosh"), [(round(e["t"], 3), e["kind"]) for e in timeline.sfx_events(edl, sp)])

    def test_card_sound_names_match_make_beds(self):
        src = (Path(__file__).resolve().parents[2] / "music" / "make_beds.py").read_text()
        beat = re.search(r"^BEAT_SFX = \{(.*)\}$", src, re.MULTILINE)
        card = re.search(r"^CARD_SFX = \{\*\*BEAT_SFX, (.*)\}$", src, re.MULTILINE)
        if not (beat and card): self.fail("make_beds.py: BEAT_SFX / CARD_SFX lines not found")
        names = re.findall(r'"(\w+)": ', beat.group(1) + ", " + card.group(1))
        self.assertEqual(sorted(names), sorted(CARD_SFX))

    def test_sfx_follow_graphics_and_ramps(self):
        ev = [(e["t"], e["kind"]) for e in timeline.sfx_events(EDL, SPECS)]
        self.assertEqual(ev, [(2.03, "pop"), (4.95, "whoosh"), (6.0, "whoosh"), (6.55, "pop"), (7.05, "pop")])

    def test_issue_time_from_every_message_format(self):
        cases = [({"code": "C1", "msg": "12.3s bez mocnego wydarzenia", "at": 3.5}, 3.5),
                 ({"code": "C2", "msg": "nakładanie grafik g1 i g2 (6.0s)"}, 6.0),
                 ({"code": "C3", "msg": "short: brak zmiany obrazu w 1.5 s (pierwsza w 2.4s)"}, 2.4),
                 ({"code": "C4", "msg": "3+ ujęcia <1 s pod rząd przy 21.0s źródła"}, 5.0),
                 ({"code": "C6", "msg": "brak min. 5 s ekranu końcowego"}, 15.0),
                 ({"code": "C9", "msg": "grafika g2 zawiera liczby, brak w verified_claims"}, 6.0),
                 ({"code": "C7", "msg": "głośność -16 LUFS (cel -14±1)"}, None)]
        for issue, t in cases:
            with self.subTest(issue["code"]):
                self.assertEqual(timeline.issue_time(issue, EDL, 15.0), t)

    def test_build_totals_and_defaults(self):
        tl = timeline.build({"keeps": [[0, 0]]}, [word("raz", 11000)], list(SPECS.values()), EDL, [{"code": "C6", "msg": "x"}])
        self.assertEqual((tl["body"], tl["total"], tl["issues"][0]["t"]), (10.0, 15.0, 15.0))
        self.assertEqual(tl["music"], {"seed": 7, "db": -13, "sfx_db": -12, "file": None})  # same defaults as build_edl/assemble
        self.assertEqual(tl["overlays"][1]["text"], ["a", "b"])
        self.assertFalse(timeline.build({"keeps": []}, [], [], None, None)["ready"])


class BeatsViewTest(unittest.TestCase):
    def test_beats_with_effect_and_pace_and_silent_punches(self):
        edl = {**EDL, "beat_fx": [{"type": "stat", "word": 1, "at": 5.0, "fx": "punch", "gfx": "kw", "sfx": "ding"},
                                  {"type": "pace", "word": 0, "at": 1.0, "fx": "punch", "gfx": None, "sfx": None}]}
        plan = {"keeps": [[0, 1]], "beats": [{"type": "stat", "word": 1, "src": "rule"}, {"type": "maxim", "word": 0, "src": "manual", "off": True}]}
        words = [word("raz", 11000), word("40", 21000)]
        rows = timeline.beats_view(plan, words, edl, {"beats": {"stat": {"label": "Liczba"}}})
        self.assertEqual([(r["type"], r["label"], r["at"], r["fx"]) for r in rows],
                         [("maxim", "maxim", 1.0, None), ("pace", "rytm", 1.0, "punch"), ("stat", "Liczba", 5.0, "punch")])
        self.assertTrue(rows[0]["off"])
        ev = timeline.sfx_events(edl, SPECS)
        self.assertNotIn({"t": 4.95, "kind": "whoosh"}, ev)  # with beat_fx the punch whoosh is gone
        self.assertIn({"t": 5.0, "kind": "ding"}, ev)


if __name__ == "__main__":
    unittest.main()

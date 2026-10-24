import json
import shutil
import subprocess
import unittest
from pathlib import Path

from montazownia import planops as po
from montazownia.planops import EditError

WORDS = [" Ala", " ma", " kota.", " Kot", " ma", " Alę.", " Koniec."]


def plan(keeps, **kw):
    return {"keeps": keeps, **kw}


class KeepsTest(unittest.TestCase):
    def test_cut_middle_splits_and_keeps_manual_bounds_on_their_side(self):
        p = po.toggle_word(plan([[0, 5, {"t0": 0.1, "t1": 9.0}]]), 2, len(WORDS))
        self.assertEqual(p["keeps"], [[0, 1, {"t0": 0.1}], [3, 5, {"t1": 9.0}]])

    def test_cut_edges_and_single_word(self):
        self.assertEqual(po.toggle_word(plan([[0, 3]]), 0, 7)["keeps"], [[1, 3]])
        self.assertEqual(po.toggle_word(plan([[0, 3]]), 3, 7)["keeps"], [[0, 2]])
        self.assertEqual(po.toggle_word(plan([[2, 2], [4, 5]]), 2, 7)["keeps"], [[4, 5]])

    def test_restore_joins_neighbours_into_one_range(self):
        p = plan([[0, 1, {"t0": 0.1}], [3, 5, {"t1": 9.0}]])
        self.assertEqual(po.toggle_word(p, 2, 7)["keeps"], [[0, 5, {"t0": 0.1, "t1": 9.0}]])

    def test_restore_extends_one_side_and_drops_its_manual_bound(self):
        self.assertEqual(po.toggle_word(plan([[0, 1, {"t1": 3.0}]]), 2, 7)["keeps"], [[0, 2]])
        self.assertEqual(po.toggle_word(plan([[3, 5, {"t0": 4.0}]]), 2, 7)["keeps"], [[2, 5]])

    def test_restore_isolated_word_goes_in_source_order(self):
        self.assertEqual(po.toggle_word(plan([[0, 0], [5, 6]]), 3, 7)["keeps"], [[0, 0], [3, 3], [5, 6]])

    def test_reordered_keeps_are_not_merged_across_the_order(self):
        # hook moved first: [5,6] then [0,3]; restoring 4 must not glue the hook to the body:
        # it extends the range it continues in the source (word 3 -> 4), the hook stays as it is
        p = po.toggle_word(plan([[5, 6], [0, 3]]), 4, 7)
        self.assertEqual(p["keeps"], [[5, 6], [0, 4]])

    def test_cut_then_restore_is_identity(self):
        p = plan([[0, 6]])
        self.assertEqual(po.toggle_word(po.toggle_word(p, 3, 7), 3, 7), p)

    def test_input_not_mutated_and_bounds_checked(self):
        p = plan([[0, 6]])
        po.toggle_word(p, 3, 7)
        self.assertEqual(p, plan([[0, 6]]))
        with self.assertRaises(EditError):
            po.toggle_word(p, 7, 7)


class EmphGfxTest(unittest.TestCase):
    def test_add_emph_on_kept_word_only_once(self):
        p = po.add_emph(plan([[0, 6]]), WORDS, 3)
        self.assertEqual(p["emph"], [{"word": 3, "amt": 0.15, "speed": 0.3}])
        with self.assertRaises(EditError):
            po.add_emph(p, WORDS, 3)
        with self.assertRaises(EditError):
            po.add_emph(plan([[0, 1]]), WORDS, 3)

    def test_find_near_anchor_resolves_like_build_edl(self):
        p = plan([[0, 6]], emph=[{"find": "ma", "near": 3}])
        self.assertEqual(po.emph_index(p, WORDS, 4), 0)  # " ma" at 4 is 1 word from near=3, " ma" at 1 is 2 away
        self.assertEqual(po.resolve({"find": "ma", "near": 5}, WORDS, 30), 4)

    def test_update_and_remove_emph(self):
        p = po.add_emph(plan([[0, 6]], vertical=True), WORDS, 2)
        self.assertEqual(p["emph"][0]["amt"], 0.1)
        p = po.update_emph(p, 0, amt=0.2, speed=0.5)
        self.assertEqual((p["emph"][0]["amt"], p["emph"][0]["speed"]), (0.2, 0.5))
        with self.assertRaises(EditError):
            po.update_emph(p, 0, amt=0.9)
        self.assertEqual(po.remove_emph(p, 0)["emph"], [])

    def test_anchor_gfx_replaces_find_near(self):
        p = plan([[0, 6]], gfx=[{"id": "g1", "find": "kot", "near": 3, "lead": 0.2}])
        self.assertEqual(po.anchor_gfx(p, "g1", 5, 7)["gfx"], [{"id": "g1", "lead": 0.2, "word": 5}])
        with self.assertRaises(EditError):
            po.anchor_gfx(p, "nie-ma", 5, 7)

    def test_new_duplicate_and_update_graphic(self):
        specs = [{"id": "g1", "type": "kw", "text": "A", "dur": 4}]
        p, s = po.add_gfx(plan([[0, 6]], gfx=[]), specs, {"id": "g2", "text": "Nowa"}, 2, 7)
        self.assertEqual(p["gfx"], [{"id": "g2", "word": 2}])
        self.assertEqual(s[-1], {"type": "kw", "text": "Nowa", "dur": 4.0, "id": "g2"})
        with self.assertRaises(EditError):
            po.add_gfx(p, s, {"id": "g2"}, 2, 7)
        p, s = po.duplicate_gfx(p, s, "g1", "g1b", 4, 7)
        self.assertEqual(s[-1]["text"], "A")
        s = po.update_spec(s, "g1b", {"text": "B", "icon": "zap"})
        self.assertEqual((s[-1]["text"], s[-1]["icon"]), ("B", "zap"))
        with self.assertRaises(EditError):
            po.update_spec(s, "g1b", {"type": "film"})
        with self.assertRaises(EditError):
            po.update_spec(s, "g1b", {"color": "red"})
        self.assertEqual(specs, [{"id": "g1", "type": "kw", "text": "A", "dur": 4}])


class ZoomGlobalsTest(unittest.TestCase):
    def test_segment_zoom_never_above_zmax(self):
        p = po.set_segment_zoom(plan([[0, 6]]), 12.3456, 1.07, zmax=1.078)
        self.assertEqual(p["seg_zoom"], {"12.35": 1.07})
        with self.assertRaises(EditError):
            po.set_segment_zoom(p, 12.3456, 1.1, zmax=1.078)
        with self.assertRaises(EditError):
            po.set_segment_zoom(p, 12.3456, {"a": 1.0, "b": 1.2, "t0": 0, "t1": 1}, zmax=1.078)
        with self.assertRaises(EditError):
            po.set_segment_zoom(p, 12.3456, 0.9, zmax=1.5)
        self.assertNotIn("seg_zoom", po.set_segment_zoom(p, 12.3456, None, zmax=1.078))

    def test_globals_typed_and_validated(self):
        p = po.set_globals(plan([[0, 6]]), {"drift": "0.04", "tail": 8, "progress": True, "music_db": -16, "zooms": [1, 1.12]})
        self.assertEqual((p["drift"], p["tail"], p["music_db"], p["zooms"]), (0.04, 8.0, -16.0, [1.0, 1.12]))
        for bad in ({"drift": 0.5}, {"zooms": [2.0]}, {"tail": -1}, {"color": 1}):
            with self.assertRaises(EditError):
                po.set_globals(p, bad)

    def test_accept_needs_reason_and_is_idempotent(self):
        with self.assertRaises(EditError):
            po.accept_issue(plan([]), "C1", "x", " ")
        p = po.accept_issue(po.accept_issue(plan([]), "C1", "x", "celowo"), "C1", "x", "celowo")
        self.assertEqual(p["critic_accept"], [{"code": "C1", "msg": "x", "reason": "celowo"}])
        for code, msg in ((["C1"], "x"), ("C1", ["x"]), ("rm -rf", "x"), ("C1", "x" * 501), ("C1", "")):
            with self.subTest(code=code), self.assertRaises(EditError):
                po.accept_issue(plan([]), code, msg, "celowo")


SPEC = {"id": "a", "type": "kw", "text": "x", "dur": 3}


class SpecSchemaTest(unittest.TestCase):
    """gfx_specs.json is shared by all films: a bad field must stop at the panel, not in gfx/render.mjs."""

    def test_bad_fields_rejected(self):
        for fields in ({"type": "list"}, {"type": "list", "items": "abc"}, {"items": [1, 2]}, {"icon": "../../etc/x"},
                       {"icon": "Bell"}, {"icons": "bell"}, {"text": {"a": 1}}, {"sub": 3}, {"html": "x" * 20001},
                       {"delays": [0.1, "x"]}, {"dur": 0.1}):
            with self.subTest(fields=fields), self.assertRaises(EditError):
                po.update_spec([SPEC], "a", fields)

    def test_good_fields_and_untouched_keys_pass(self):
        s = po.update_spec([{**SPEC, "side": "left"}], "a", {"type": "list", "items": ["jeden", "dwa"], "icons": ["bell", ""],
                                                                     "delays": [0.2, 0.6], "text": "Trzy"})
        self.assertEqual(s[0], {"id": "a", "type": "list", "text": "Trzy", "dur": 3.0, "side": "left", "items": ["jeden", "dwa"],
                                "icons": ["bell", ""], "delays": [0.2, 0.6]})
        self.assertNotIn("text", po.update_spec([{"id": "b", "type": "end", "dur": 4}], "b", {"sub": "x"})[0])

    def test_count_card_needs_a_number_in_its_text(self):
        self.assertEqual(po.update_spec([SPEC], "a", {"type": "count", "text": "2,5 dnia"})[0]["type"], "count")
        for text in ("bez liczby", "", "\u0663 dni", "B2B", "Q3", "15:30", "10.5", "kod 007"):  # nothing the renderer counts
            with self.subTest(text=text), self.assertRaises(EditError):
                po.update_spec([SPEC], "a", {"type": "count", "text": text})


    def test_maxim_card_needs_a_sentence(self):
        s = po.update_spec([SPEC], "a", {"type": "maxim", "text": "Pierwszy projekt ma *zbudować zaufanie*"})[0]
        self.assertEqual(s["type"], "maxim")
        for text in ("", "  ", "**", ["dwa", "teksty"]):
            with self.subTest(text=text), self.assertRaises(EditError):
                po.update_spec([SPEC], "a", {"type": "maxim", "text": text})
        self.assertEqual(len(po.update_spec([SPEC], "a", {"type": "maxim", "text": "*" + "a" * 138 + "*"})[0]["text"]), 140)
        with self.assertRaises(EditError):  # 141: the band would cover the face (the stars count)
            po.update_spec([SPEC], "a", {"type": "maxim", "text": "*" + "a" * 139 + "*"})

    def test_maxim_word_times_go_with_a_new_text(self):
        m = {"id": "a", "type": "maxim", "text": "Dwa słowa", "dur": 4, "at": [0.2, 0.9]}
        self.assertEqual(po.update_spec([m], "a", {"dur": 5})[0]["at"], [0.2, 0.9])  # same words: times stay
        self.assertEqual(po.update_spec([m], "a", {"text": "Dwa słowa"})[0]["at"], [0.2, 0.9])
        self.assertNotIn("at", po.update_spec([m], "a", {"text": "Inne dwa"})[0])  # new words: even pace

    def test_pill_is_a_9_16_maxim_flag(self):
        hook = {"id": "h", "type": "maxim", "canvas": "v", "pill": True, "text": "*95%* firm", "dur": 3.5}
        plan, specs = po.add_gfx({"gfx": []}, [], hook, 0, 10)
        self.assertEqual(specs[0]["pill"], True)
        _, specs = po.duplicate_gfx(plan, specs, "h", "h2", 1, 10)  # the panel copies a hook card
        self.assertEqual(specs[-1]["pill"], True)
        for bad in ({"pill": "true"}, {"pill": 1}, {"canvas": None, "pill": True}, {"type": "kw", "pill": True}):
            spec = {k: v for k, v in {**hook, "id": "x", **bad}.items() if v is not None}
            with self.subTest(bad=bad), self.assertRaises(EditError):
                po.add_gfx({"gfx": []}, [], spec, 0, 10)
        self.assertEqual(po.update_spec([hook], "h", {"pill": False})[0]["pill"], False)

    def test_card_sfx(self):
        m = {"id": "a", "type": "maxim", "text": "x y", "dur": 4, "sfx": [[1.0, "pen"], [2, "pop"]]}
        _, specs = po.duplicate_gfx({"gfx": [{"id": "a", "word": 0}]}, [m], "a", "b", 0, 5)
        self.assertEqual(specs[1]["sfx"], [[1.0, "pen"], [2.0, "pop"]])
        for sfx in (5, [[1.0, "boom"]], [[-1, "pop"]], [[1.0]], [["x", "pop"]], [[float("nan"), "pop"]], [[4.5, "pop"]]):
            with self.subTest(sfx=sfx), self.assertRaises(EditError):  # 4.5: after the 4 s card ends
                po.update_spec([m], "a", {"sfx": sfx})

    def test_stored_fields_not_edited_now_pass_through(self):
        old = {"id": "a", "type": "maxim", "text": "x y", "dur": 4, "sfx": [[1, "boom"], [1.5, "pen"]], "at": [0.1, 0.6]}
        s = po.update_spec([old], "a", {"dur": 5})[0]  # make_beds skips "boom": an older card is still editable
        self.assertEqual((s["dur"], s["sfx"], s["at"]), (5.0, old["sfx"], old["at"]))
        _, specs = po.duplicate_gfx({"gfx": [{"id": "a", "word": 0}]}, [old], "a", "b", 0, 5)
        self.assertEqual((specs[1]["sfx"], specs[1]["at"]), ([[1.5, "pen"]], [0.1, 0.6]))  # the copy plays the same
        with self.assertRaises(EditError):
            po.update_spec([old], "a", {"at": [0.1, 9]})  # a word after the card ends

    def test_web_card_fields(self):
        web = {"type": "web", "url": "https://example.org/raport", "find": "95 procent firm nie widzi zwrotu"}
        s = po.update_spec([SPEC], "a", {**web, "page": 3, "src": "Źródło: MIT"})[0]
        self.assertEqual((s["type"], s["url"], s["page"], s["src"]), ("web", web["url"], 3, "Źródło: MIT"))
        self.assertEqual(po.update_spec([SPEC], "a", {"type": "web", "img": "zrzut.png", "box": [10, 20, 300, 40]})[0]["box"],
                         [10.0, 20.0, 300.0, 40.0])
        for bad in ({"type": "web"}, {**web, "url": "http://example.org/"}, {**web, "url": "file:///etc/passwd"},
                    {**web, "find": "za mało"}, {**web, "find": 5}, {"type": "web", "img": "../sekret.png"},
                    {"type": "web", "img": "a/b.png"}, {"type": "web", "img": ".ukryty.png"}, {**web, "page": 0},
                    {**web, "box": [1, 2, 3]}, {**web, "url": "https://x.pl/" + "a" * 2000}, {**web, "canvas": "v"},
                    {**web, "page": 3.5}, {"type": "web", "url": "", "find": "", "img": ""}):
            with self.subTest(bad=bad), self.assertRaises(EditError):
                po.update_spec([SPEC], "a", bad)

    def test_web_card_with_own_screenshot_saves_as_the_editor_sends_it(self):
        own = {**SPEC, "type": "web", "img": "zrzut.png", "box": [1, 2, 3, 4]}
        s = po.update_spec([own], "a", {"type": "web", "text": "", "dur": 7.0, "url": "", "find": ""})[0]  # empty = no field
        self.assertEqual((s["dur"], s["img"], "url" in s, "find" in s), (7.0, "zrzut.png", False, False))
        web = {**SPEC, "type": "web", "url": "https://example.org/r", "find": "zdanie ze strony "}
        s = po.update_spec([web], "a", {"type": "web", "url": "", "img": "zrzut.png"})[0]  # address replaced by a screenshot
        self.assertEqual(("url" in s, s["img"], s["find"]), (False, "zrzut.png", "zdanie ze strony "))  # untouched find kept as is
        kw = {**SPEC, "type": "kw", "url": "http://stare.pl/"}  # leftovers of another type are not checked as a web card
        self.assertEqual(po.update_spec([kw], "a", {"dur": 3.0})[0]["url"], "http://stare.pl/")


class BeatsTest(unittest.TestCase):
    TYPES = ("hook", "stat", "contrast", "maxim")

    def test_apply_proposal_drops_auto_emph_and_adds_only_new_graphics(self):
        p = plan([[0, 9]], emph=[{"word": 3, "amt": 0.1, "auto": True}, {"word": 5, "amt": 0.2}])
        specs = [{"id": "old", "type": "kw", "text": "x", "dur": 2}]
        prop = {"beats": [{"type": "stat", "word": 4, "src": "rule", "text": "40 repo"}],
                "gfx": [{"id": "b_new", "word": 4, "lead": 0.0}, {"id": "old", "word": 1}],
                "specs": [{"id": "b_new", "type": "kw", "text": "40", "dur": 2.5, "canvas": "v", "quote": True},
                          {"id": "old", "type": "kw", "text": "changed", "dur": 2}]}
        p2, s2 = po.apply_beats(p, specs, prop, self.TYPES, 10)
        self.assertEqual(p2["beats"], [{"type": "stat", "word": 4, "src": "rule", "text": "40 repo"}])
        self.assertEqual(p2["emph"], [{"word": 5, "amt": 0.2}])
        self.assertEqual([g["id"] for g in p2["gfx"]], ["b_new"])
        self.assertEqual([s["text"] for s in s2], ["x", "40"])  # an existing id is never overwritten
        self.assertNotIn("beats", p)  # input untouched

    def test_apply_rejects_bad_proposals(self):
        p = plan([[0, 9]])
        for prop in ({"beats": [{"type": "nope", "word": 1}]}, {"beats": [{"type": "stat", "word": 99}]},
                     {"beats": [], "specs": [{"id": "a/b", "type": "kw", "dur": 2}]},
                     {"beats": [], "specs": [{"id": "c", "type": "kw", "dur": 2, "canvas": "h"}]}, {"beats": "x"}):
            with self.subTest(prop=prop), self.assertRaises(EditError):
                po.apply_beats(p, [], prop, self.TYPES, 10)

    def test_set_beat_type_makes_it_manual_off_and_force_exclusive(self):
        p = plan([[0, 9]], beats=[{"type": "stat", "word": 4, "src": "rule"}])
        p2 = po.set_beat(p, 0, {"type": "maxim", "off": True}, self.TYPES)
        self.assertEqual(p2["beats"][0], {"type": "maxim", "word": 4, "src": "manual", "off": True})
        self.assertNotIn("off", po.set_beat(p2, 0, {"off": False}, self.TYPES)["beats"][0])
        with self.assertRaises(EditError):
            po.set_beat(p2, 0, {"force": True}, self.TYPES)
        with self.assertRaises(EditError):
            po.set_beat(p, 0, {"amt": 1}, self.TYPES)

    def test_add_and_remove_manual_beat(self):
        p = plan([[0, 5]], beats=[{"type": "stat", "word": 4, "src": "rule"}])
        p2 = po.add_beat(p, 2, "contrast", self.TYPES)
        self.assertEqual([(b["word"], b["src"]) for b in p2["beats"]], [(2, "manual"), (4, "rule")])
        for i in (4, 8):  # already a moment / cut word
            with self.subTest(i=i), self.assertRaises(EditError):
                po.add_beat(p, i, "contrast", self.TYPES)
        self.assertEqual(len(po.remove_beat(p2, 0)["beats"]), 1)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("node"), "no node")
class CountRuleMatchesRendererTest(unittest.TestCase):
    """The panel's "count needs a number" must say what gfx/count_lib.mjs (the renderer) counts, or a saved card renders as kw."""

    def test_same_answer_as_count_lib(self):
        texts = ["95%", "2,5 dnia", "12 000 zł", "50–70%", "-5%", "Q3 2025", "5 h", "B2B", "Q3", "v2.0", "15:30", "10.5",
                 "kod 007", "00", "bez liczby", "\u0663 dni", "od 2 1000 zł", "1,05 zł"]
        lib = Path(__file__).resolve().parents[2] / "gfx" / "count_lib.mjs"
        js = f"import('{lib.as_uri()}').then(m => console.log(JSON.stringify(JSON.parse(process.argv[1]).map(m.hasNumber))))"
        r = subprocess.run(["node", "-e", js, json.dumps(texts)], capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(json.loads(r.stdout), [po.counts_something(t) for t in texts])

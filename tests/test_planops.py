import unittest

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


if __name__ == "__main__":
    unittest.main()

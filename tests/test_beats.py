"""Editing grammar (pipeline side): beats.py detects moments in the spoken text, beatfx.py turns them into punches.

Pure functions on synthetic words; no files in ~/Videos. The pipeline lives one level above the panel."""
import http.client
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

from montazownia import planops

PIPE = Path(__file__).resolve().parents[2]


def _load(name: str) -> Any:
    """The pipeline modules are scripts next to the panel, not a package: load them by path. Their folder goes on
    sys.path as when they run as scripts, so their own imports of each other (beatfx -> gfx_types) resolve."""
    if str(PIPE) not in sys.path:
        sys.path.append(str(PIPE))
    spec = importlib.util.spec_from_file_location(name, PIPE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


beatfx = _load("beatfx")
beats = _load("beats")

G = beats.load_grammar()


def words(text, start=0.0, step=0.4):
    """[(from, to, text)] with one word every `step` seconds."""
    return [(start + i * step, start + i * step + 0.3, " " + t) for i, t in enumerate(text.split())]


class DetectTest(unittest.TestCase):
    def detect(self, text):
        W = words(text)
        return beats.detect({"keeps": [[0, len(W) - 1]]}, W, G), W

    def test_sentences_split_on_punctuation(self):
        W = words("Raz dwa trzy. Cztery pięć? Sześć")
        self.assertEqual(beats.sentences({"keeps": [[0, len(W) - 1]]}, W), [[0, 1, 2], [3, 4], [5]])

    def test_first_sentence_is_hook_anchored_after_half_a_second(self):
        found, W = self.detect("Dziś pokażę wam coś naprawdę ważnego dla firmy.")
        self.assertEqual(found[0]["type"], "hook")
        self.assertGreaterEqual(W[found[0]["word"]][0] - W[0][0], 0.5)

    def test_intents_from_cues(self):
        found, W = self.detect("Wstęp jest tutaj zwykły. Te 95% firm traci pieniądze. Ale to nie wszystko. "
                               "I powiem Wam szczerze, że sam byłem w tej grupie. Trzymam się trzech zasad.")
        got = {(b["type"], W[b["word"]][2].strip()) for b in found}
        self.assertIn(("stat", "95%"), got)
        self.assertIn(("contrast", "Ale"), got)
        self.assertIn(("confession", "szczerze,"), got)
        self.assertIn(("list", "trzech"), got)

    def test_mid_sentence_ale_only_after_a_comma(self):
        found, W = self.detect("Wstęp jest tutaj zwykły. Nie ale tylko tak mówię zwyczajnie dzisiaj. Zrobiłem, ale nikt tego nie chciał.")
        anchors = [W[b["word"]][2].strip() for b in found if b["type"] == "contrast"]
        self.assertEqual(anchors, ["ale"])
        self.assertEqual(found[-1]["word"], 12)

    def test_several_moments_in_one_long_sentence(self):
        found, _ = self.detect("Wstęp jest tutaj zwykły. Mam około 40 repozytoriów i to jest cała puenta, bo mocne nie znaczy używane.")
        self.assertEqual({b["type"] for b in found[1:]}, {"stat", "maxim"})

    def test_merge_keeps_manual_and_switched_off(self):
        old = [{"type": "stat", "word": 5, "src": "rule", "off": True}, {"type": "maxim", "word": 9, "src": "manual"},
               {"type": "contrast", "word": 12, "src": "rule"}]
        new = [{"type": "stat", "word": 5, "src": "rule"}, {"type": "contrast", "word": 12, "src": "rule"}]
        merged = beats.merge(old, new)
        self.assertEqual([(b["word"], b.get("off"), b["src"]) for b in merged], [(5, True, "rule"), (9, None, "manual"), (12, None, "rule")])

    def test_llm_unreachable_falls_back_to_rules(self):
        found, W = self.detect("Wstęp jest tutaj zwykły. To zdanie nie ma żadnego sygnału w sobie.")
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=OSError("down")):
            self.assertEqual(beats.llm_refine(found, W, G), 0)
        self.assertIsNone(found[-1]["type"])

    def test_llm_labels_only_known_types_and_words_of_the_sentence(self):
        found, W = self.detect("Wstęp jest tutaj zwykły. Panel liczy kto się logował w tym tygodniu.")
        body = {"choices": [{"message": {"content": json.dumps({"labels": [
            {"n": 0, "type": "demo", "word": "liczy"}, {"n": 5, "type": "demo"}, {"n": 0, "type": "nonsense"}]})}}]}
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.OpenerDirector.open", return_value=resp), mock.patch("json.load", return_value=body):
            self.assertEqual(beats.llm_refine(found, W, G), 1)
        self.assertEqual((found[-1]["type"], W[found[-1]["word"]][2].strip(), found[-1]["src"]), ("demo", "liczy", "llm"))


class LlmShapeTest(unittest.TestCase):
    """A reply of an unexpected shape from the local model must fall back to the rules, never crash the job."""

    def setUp(self):
        self.W = words("Zwykłe zdanie na początek. Tu nie ma żadnego wzorca w ogóle.")
        self.found = beats.detect({"keeps": [[0, len(self.W) - 1]]}, self.W, G)

    def refine(self, **patch):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.OpenerDirector.open", return_value=resp), mock.patch("json.load", **patch):
            return beats.llm_refine(self.found, self.W, G)

    def test_bad_replies_mean_rules_only(self):
        for body in ({"choices": []}, {"choices": [{"message": {"content": None}}]},
                     {"choices": [{"message": {"content": '{"labels": null}'}}]}, {},
                     {"choices": [{"message": {"content": '{"labels": [{"n": 0, "type": ["demo"]}, {"n": 0, "type": {"a": 1}}]}'}}]}):
            with self.subTest(body=body):
                self.assertEqual(self.refine(return_value=body), 0)
        self.assertEqual(self.refine(side_effect=http.client.IncompleteRead(b"{")), 0)


class GfxTest(unittest.TestCase):
    def setUp(self):
        self.W = words("Wstęp jest zwykły tutaj dziś. Mam około 40 repozytoriów, z czego może 10 działa. "
                       "Z firm 3 na 4 nie doszły do codziennego użycia.", step=0.5)
        self.plan = {"name": "s", "vertical": True, "keeps": [[0, len(self.W) - 1]]}
        self.order = beats.kept_order(self.plan)

    def test_stat_card_text_from_spoken_words(self):
        self.assertEqual(beats.stat_card(7, self.W, self.order, G["detect"]), ("~40", "repozytoriów,".strip(",")))
        self.assertEqual(beats.stat_card(16, self.W, self.order, G["detect"])[0], "3 na 4")
        W = words("Byłem w tych 95%. Porekscytowany budowałem.")
        self.assertEqual(beats.stat_card(3, W, list(range(len(W))), G["detect"]), ("95%", ""))  # never a word of the next sentence

    def test_stat_card_keeps_a_number_said_in_thousand_groups(self):
        W = words("Kosztuje 1 200 zł rocznie.")
        self.assertEqual(beats.stat_card(1, W, list(range(len(W))), G["detect"]), ("1\u00a0200", "zł rocznie"))
        W = words("Mam 3 200-letnie drzewa.")  # not a thousands group: '200-letnie' stays a word
        self.assertEqual(beats.stat_card(1, W, list(range(len(W))), G["detect"])[0], "3")

    def test_stat_card_caption_never_ends_on_a_preposition(self):
        W = words("Wychodzi 20 minut w tygodniu.")
        self.assertEqual(beats.stat_card(1, W, list(range(len(W))), G["detect"]), ("20", "minut"))

    def test_graphic_ids_are_file_safe_whatever_the_film_name(self):
        g = {**G, "auto_gfx": {**G["auto_gfx"], "long": ["stat"]}}
        plan = {**self.plan, "name": "95 procent - long", "vertical": False}
        ids = [sp["id"] for sp in beats.make_gfx(plan, beats.detect(plan, self.W, g), self.W, g, [])]
        self.assertTrue(ids and all(re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids), ids)
        self.assertTrue(ids[0].startswith("b_95_procent_-_long_"), ids)

    def test_hand_written_beat_without_text_gets_a_card(self):
        specs = beats.make_gfx(self.plan, [{"type": "stat", "word": 7}], self.W, G, [])  # README: "text" is optional
        self.assertEqual([sp["text"] for sp in specs], ["~40"])

    def test_icon_by_topic(self):
        self.assertEqual(beats.pick_icon("Mam 40 repozytoriów na GitHubie", G), "folder-git-2")
        self.assertEqual(beats.pick_icon("zupełnie nic", G), "hash")

    def test_make_gfx_vertical_quote_cards_idempotent_and_clear_of_the_hook(self):
        found = beats.detect(self.plan, self.W, G)
        specs = beats.make_gfx(self.plan, found, self.W, G, [])
        self.assertEqual([(sp["text"], sp["canvas"], sp["quote"]) for sp in specs], [("~40", "v", True), ("3 na 4", "v", True)])
        self.assertEqual(len(self.plan["gfx"]), 2)
        self.assertEqual(beats.make_gfx(self.plan, found, self.W, G, specs), [])  # second run adds nothing
        hooked = {**self.plan, "gfx": [], "hook_text": "Hak", "hook_dur": 3.0}
        self.assertEqual([sp["text"] for sp in beats.make_gfx(hooked, found, self.W, G, [])], ["3 na 4"])  # 40 at 3.5 s < 3+1

    def test_moment_cards_pass_the_panel_graphic_edits(self):
        """The fields beats.py writes (canvas, quote, beat) are accepted by every panel graphic edit, not only apply_beats."""
        found = beats.detect(self.plan, self.W, G)
        specs = beats.make_gfx(self.plan, found, self.W, G, [])
        p, s = planops.duplicate_gfx(self.plan, specs, specs[0]["id"], "kopia", 9, len(self.W))
        self.assertEqual({k: s[-1][k] for k in ("canvas", "quote", "beat")}, {"canvas": "v", "quote": True, "beat": "stat"})
        s = planops.update_spec(s, "kopia", {"text": "41"})
        p, s = planops.add_gfx(p, s, {"id": "z_momentu", "type": "kw", "text": "40", "dur": 3, "canvas": "v"}, 7,
                               len(self.W))  # ui/app.js "+ grafika z tego momentu" in a vertical film
        self.assertEqual(s[-1]["canvas"], "v")
        with self.assertRaises(planops.EditError):
            planops.update_spec(s, "kopia", {"beat": ["stat"]})


class GrammarFileTest(unittest.TestCase):
    def broken(self, edit):
        g = json.loads((PIPE / "style" / "grammar.json").read_text())
        edit(g)
        d = tempfile.mkdtemp(prefix="montaz-grammar-")
        self.addCleanup(shutil.rmtree, d, True)
        p = Path(d) / "g.json"
        p.write_text(json.dumps(g))
        return str(p)

    def test_missing_key_is_named(self):
        cases = {r"rules\.short\.min_gap": lambda g: g["rules"]["short"].pop("min_gap"),
                 r"effects\.short\.stat\.punch\b": lambda g: g["effects"]["short"]["stat"]["punch"].pop("amt"),
                 r"beats\.hook\.priority": lambda g: g["beats"]["hook"].pop("priority"),
                 r"beats\.stat\.intent": lambda g: g["beats"]["stat"].pop("intent"),
                 r"effects\.short\b": lambda g: g["effects"].pop("short"),
                 r"effects\.long\b": lambda g: g.pop("effects"),
                 r"detect\.filler": lambda g: g["detect"].update(filler=[[1]]),
                 r"detect\.approx": lambda g: g["detect"].update(approx={"około": 1})}
        for name, edit in cases.items():
            with self.subTest(key=name):
                path = self.broken(edit)
                with self.assertRaisesRegex(ValueError, name):
                    beatfx.load_grammar(path)
                with self.assertRaisesRegex(ValueError, name):  # "Rozpoznaj momenty" names it too, not a bare KeyError
                    beats.load_grammar(path)

    def test_tier_sfx_and_cues_are_checked(self):
        cases = {r"beats\.stat\.tier": lambda g: g["beats"]["stat"].update(tier="primry"),
                 r"effects\.long\.stat\.sfx": lambda g: g["effects"]["long"]["stat"].update(sfx="dingg"),
                 r"beats\.stat\.cues": lambda g: g["beats"]["stat"].update(cues=[1])}
        for name, edit in cases.items():
            with self.subTest(key=name), self.assertRaisesRegex(ValueError, name):
                beatfx.load_grammar(self.broken(edit))

    def test_values_are_checked_not_only_keys(self):
        cases = {r"effects\.long\.hook\.punch": lambda g: g["effects"]["long"]["hook"].update(punch={"amt": -0.5}),
                 r"effects\.short\.stat\.punch": lambda g: g["effects"]["short"]["stat"]["punch"].update(speed="fast"),
                 r"rules\.long\.min_gap": lambda g: g["rules"]["long"].update(min_gap=-1),
                 r"detect\.per_sentence": lambda g: g["detect"].update(per_sentence="3"),
                 r"detect\.hook_latest": lambda g: g["detect"].pop("hook_latest")}  # no hidden default in beats.py
        for name, edit in cases.items():
            with self.subTest(key=name), self.assertRaisesRegex(ValueError, name):
                beats.load_grammar(self.broken(edit))

    def test_sfx_names_match_what_make_beds_plays_and_the_panel_draws(self):
        make_beds = (PIPE / "music" / "make_beds.py").read_text()
        played = set(re.findall(r'"(\w+)": lambda', make_beds[make_beds.index("BEAT_SFX"):]))
        m = re.search(r'r\.get\("sfx"\) in \(([^)]*)\)', (PIPE / "panel" / "montazownia" / "timeline.py").read_text())
        assert m, "timeline.sfx_events no longer lists the sounds"
        drawn = set(re.findall(r'"(\w+)"', m.group(1)))
        self.assertEqual(set(beatfx.SFX) - {None}, played)
        self.assertEqual(set(beatfx.SFX) - {None}, drawn)

    def test_icons_effects_regexes_and_shape_are_checked(self):
        cases = {r"icons\.github": lambda g: g["icons"].update({"github|repozytor": "folder-git-3x"}),
                 r"effects\.long\.stat\b": lambda g: g["effects"]["long"].pop("stat"),
                 r"effects\.short\.pace": lambda g: g["effects"]["short"].pop("pace"),
                 r"beats\.stat\.cues": lambda g: g["beats"]["stat"]["cues"].append("(unclosed"),
                 r"muszą być obiektami": lambda g: g.update(rules=[])}
        for name, edit in cases.items():
            with self.subTest(key=name), self.assertRaisesRegex(ValueError, name):
                beats.load_grammar(self.broken(edit))

    def test_stamp_keeps_the_text_of_each_version(self):
        path = self.broken(lambda g: g.update(version=99))
        st = beatfx.grammar_stamp(path)
        kept = Path(path).parent / "history" / f"grammar-{st['sha']}.json"
        self.assertEqual(kept.read_bytes(), Path(path).read_bytes())
        self.assertEqual(beatfx.grammar_stamp(path), st)  # same text: same file, nothing new
        self.assertEqual(len(list(kept.parent.iterdir())), 1)

    def test_stats_pace_share(self):
        rows = [{"type": "stat", "fx": "punch"}, {"type": "pace", "fx": "punch"}, {"type": "maxim", "fx": "spacing"}]
        self.assertEqual(beatfx.stats(rows), {"punches": 2, "by_type": {"stat": 1, "pace": 1}, "pace_share": 0.5})
        self.assertEqual(beatfx.stats([])["pace_share"], 0.0)

    def test_llm_call_ignores_http_proxy(self):
        W = words("Zwykłe zdanie na początek. Tu nie ma żadnego wzorca w ogóle.")
        found = beats.detect({"keeps": [[0, len(W) - 1]]}, W, G)
        with mock.patch.dict("os.environ", {"http_proxy": "http://proxy.example:3128"}), \
                mock.patch("urllib.request.OpenerDirector.open", side_effect=OSError("down")) as op, \
                mock.patch("urllib.request.build_opener", wraps=urllib.request.build_opener) as bo:
            beats.llm_refine(found, W, G)
        op.assert_called_once()
        handlers = bo.call_args.args
        self.assertTrue(any(type(h).__name__ == "ProxyHandler" and h.proxies == {} for h in handlers), handlers)

    def test_stamp_ties_an_edl_to_the_grammar_text(self):
        st = beatfx.grammar_stamp()
        self.assertEqual((st["version"], len(st["sha"])), (G["version"], 12))

    def test_llm_url_must_be_local(self):
        W = words("Zwykłe zdanie na początek. Tu nie ma żadnego wzorca w ogóle.")
        found = beats.detect({"keeps": [[0, len(W) - 1]]}, W, G)
        with mock.patch("urllib.request.OpenerDirector.open") as op:
            self.assertEqual(beats.llm_refine(found, W, G, url="https://api.example.com/v1/chat/completions"), 0)
        op.assert_not_called()


class FitMovesTest(unittest.TestCase):
    """beatfx.fit_moves (used by face_track): with beats a punch over the head limit keeps its size instead of being flattened."""

    def setUp(self):
        self.fit = _load("beatfx").fit_moves

    def test_each_move_slides_under_the_limit(self):
        keys = [[0.0, 1.1], [0.5, 1.1], [0.7, 1.2], [6.3, 1.2], [6.34, 1.1], [6.8, 1.18]]
        self.assertEqual(self.fit(keys, 1.15), [[0.0, 1.05], [0.5, 1.05], [0.7, 1.15], [6.3, 1.15], [6.34, 1.07], [6.8, 1.15]])

    def test_floor_is_no_zoom_out(self):
        self.assertEqual(self.fit([[0.0, 1.0], [0.2, 1.0], [0.4, 1.2]], 1.05), [[0.0, 1.0], [0.2, 1.0], [0.4, 1.05]])


class BeatFxTest(unittest.TestCase):
    """One 60 s keep cut into 6 shots of 10 s; src2out is the identity."""

    def setUp(self):
        self.W = words(" ".join(f"w{i}" for i in range(150)), step=0.4)
        self.segs = [{"src": "A", "in": k * 10.0, "out": k * 10.0 + 10.0, "zoom": 1.0} for k in range(6)]
        self.P = {"keeps": [[0, 149]], "emph": [], "beats": []}

    def fx(self, beats_, vertical=False, ovs=(), specs=None, emph=()):
        P = {**self.P, "beats": beats_, "vertical": vertical, "emph": list(emph)}
        return beatfx.plan_beat_fx(P, self.W, self.segs, list(ovs), specs or {}, lambda t: t, G)

    def test_primary_beats_by_priority_and_min_gap(self):
        # stat (90) and contrast (80) 2 s apart, long min_gap 10 s: stat wins, contrast reported as spacing
        emph, rows = self.fx([{"type": "contrast", "word": 30}, {"type": "stat", "word": 35}])
        self.assertEqual([e["beat"] for e in emph if "beat" in e], ["stat"])
        self.assertEqual({r["type"]: r["fx"] for r in rows if r["type"] != "pace"}, {"stat": "punch", "contrast": "spacing"})

    def test_confession_gets_no_punch(self):
        _, rows = self.fx([{"type": "confession", "word": 30}])
        self.assertEqual(rows[0]["fx"], "none")

    def test_auto_emph_dropped_manual_kept(self):
        emph, _ = self.fx([], emph=[{"word": 10, "amt": 0.1, "auto": True}, {"word": 60, "amt": 0.2}])
        self.assertEqual([e["word"] for e in emph if "beat" not in e], [60])

    def test_report_never_claims_a_punch_build_edl_skips(self):
        # word 30 = 12 s in shot 1 (10-20 s); build_edl skips a punch when a side box starts later in the same shot
        later_box = [{"id": "k", "at": 16.0, "dur": 3.0}]
        _, rows = self.fx([{"type": "stat", "word": 30}], ovs=later_box, specs={"k": {"type": "kw"}})
        self.assertEqual(rows[0]["fx"], "box")
        _, rows = self.fx([{"type": "stat", "word": 30}], ovs=[{"id": "k", "at": 21.0, "dur": 3.0}], specs={"k": {"type": "kw"}})
        self.assertEqual(rows[0]["fx"], "punch")  # next shot: visible
        self.P["seg_zoom"] = {"10.00": 1.1}  # the editor fixed this shot's zoom in the panel (applied after punches)
        emph, rows = self.fx([{"type": "stat", "word": 30}])
        self.assertEqual((rows[0]["fx"], [e for e in emph if e.get("beat") == "stat"]), ("manual", []))

    def test_type_missing_from_the_grammar_is_reported(self):
        _, rows = self.fx([{"type": "renamed_type", "word": 30}])
        self.assertEqual(rows[0]["fx"], "unknown")

    def test_two_moments_in_one_long_shot_both_punch(self):
        # short: min_gap 4 s; both in shot 1 (10-20 s): build_edl turns the second one into zoom keyframes
        emph, _ = self.fx([{"type": "maxim", "word": 26}, {"type": "stat", "word": 46}], vertical=True)
        self.assertEqual(sorted(e["beat"] for e in emph if e.get("beat") != "pace"), ["maxim", "stat"])

    def test_hook_moves_off_the_first_frame(self):
        _, rows = self.fx([{"type": "hook", "word": 0}], vertical=True)
        hook = next(r for r in rows if r["type"] == "hook")
        self.assertTrue(0.5 <= hook["at"] <= 1.5, hook)

    def test_side_box_blocks_punch_but_vertical_card_does_not(self):
        ov = [{"id": "g", "at": 13.0, "dur": 3.0}]
        _, rows = self.fx([{"type": "stat", "word": 35}], ovs=ov, specs={"g": {"type": "kw"}})
        self.assertEqual(rows[0]["fx"], "box")
        _, rows = self.fx([{"type": "stat", "word": 35}], vertical=True, ovs=ov, specs={"g": {"type": "kw", "canvas": "v"}})
        self.assertEqual(next(r for r in rows if r["type"] == "stat")["fx"], "punch")

    def test_off_and_force(self):
        _, rows = self.fx([{"type": "stat", "word": 35, "off": True}])
        self.assertEqual(rows[0]["fx"], "off")
        emph, _ = self.fx([{"type": "stat", "word": 35}, {"type": "relatable", "word": 40, "force": True}])
        self.assertNotIn("relatable", [e.get("beat") for e in emph])  # relatable has no punch in the long grammar
        emph, _ = self.fx([{"type": "stat", "word": 35}, {"type": "contrast", "word": 60, "force": True}])
        self.assertEqual(sorted(e["beat"] for e in emph if e.get("beat") != "pace"), ["contrast", "stat"])

    def test_pace_fills_short_static_stretch(self):
        # short max_static 7 s and a 60 s film with one moment: pace punches keep every gap <= 7 s where shots allow
        self.W[49] = (self.W[49][0], self.W[49][1], " koniec,")
        _, rows = self.fx([{"type": "stat", "word": 5}], vertical=True)
        self.assertIn("pace", [r["type"] for r in rows])
        self.assertTrue(all(r["fx"] == "punch" for r in rows if r["type"] == "pace"))

    def test_beat_sfx_in_rows(self):
        _, rows = self.fx([{"type": "stat", "word": 35}], vertical=True)
        self.assertEqual(next(r for r in rows if r["type"] == "stat")["sfx"], "ding")


class CriticGrammarTest(unittest.TestCase):
    """critic.py on a synthetic plan with beats (subprocess, files in a temp dir): C1 limit = grammar max_static,
    --fix forces the strongest detected moment in the static stretch (never a random word) and drops auto emph."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="montaz-critic-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.lim = G["rules"]["short"]["max_static"]

    def critic(self, length, beat_fx, beats_, *args, grammar=None):
        n = int(length)
        tr = [{"text": f" s{i}.", "offsets": {"from": i * 1000, "to": i * 1000 + 800}} for i in range(n)]
        (self.d / "w.json").write_text(json.dumps({"transcription": tr}))
        (self.d / "edl.json").write_text(json.dumps({"segments": [{"in": 0.0, "out": length, "zoom": 1.0}],
                                                    "overlays": [], "beat_fx": beat_fx}))
        plan = {"edl_out": "edl.json", "words": "w.json", "keeps": [[0, n - 1]], "vertical": True,
                "emph": [{"word": 2, "amt": 1.1, "auto": True}], **({"beats": beats_} if beats_ is not None else {})}
        (self.d / "plan.json").write_text(json.dumps(plan))
        # grammar: run critic with beatfx.DEFAULT_GRAMMAR pointed at a test copy (the CLI never takes a grammar path)
        boot = (f"import sys, runpy; sys.path.insert(0, {str(PIPE)!r}); import beatfx; beatfx.DEFAULT_GRAMMAR = {grammar!r}; "
                f"sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')") if grammar else None
        cmd = [sys.executable, *(["-c", boot] if boot else []), str(PIPE / "critic.py"), "plan.json", "--json", "c.json", *args]
        r = subprocess.run(cmd, cwd=self.d, capture_output=True, text=True, timeout=60, check=False)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return json.loads((self.d / "c.json").read_text())["issues"], json.loads((self.d / "plan.json").read_text()), r.stdout

    def test_c9_quote_card_numbers_compared_by_value(self):
        said = ["Mamy", "25", "procent", "i", "1", "200", "osób", "oraz", "3,5", "roku."]
        tr = [{"text": " " + t, "offsets": {"from": i * 1000, "to": i * 1000 + 800}} for i, t in enumerate(said)]
        (self.d / "w.json").write_text(json.dumps({"transcription": tr}))
        cards = {"g_dec": "2,5", "g_int": "25", "g_k": "1.200", "g_dot": "3.5"}
        (self.d / "s.json").write_text(json.dumps([{"id": k, "type": "kw", "text": v, "dur": 2, "quote": True}
                                                  for k, v in cards.items()]))
        (self.d / "edl.json").write_text(json.dumps({"segments": [{"in": 0.0, "out": 10.0, "zoom": 1.0}], "beat_fx": [],
                                                    "overlays": [{"id": k, "at": 3.0, "dur": 4.0} for k in cards]}))
        (self.d / "plan.json").write_text(json.dumps({"edl_out": "edl.json", "words": "w.json", "gfx_specs": "s.json",
                                                      "keeps": [[0, 9]], "vertical": True, "beats": []}))
        r = subprocess.run([sys.executable, str(PIPE / "critic.py"), "plan.json", "--json", "c.json"], cwd=self.d,
                           capture_output=True, text=True, timeout=60, check=False)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        c9 = {i["msg"].split()[1].rstrip(":"): i["sev"] for i in json.loads((self.d / "c.json").read_text())["issues"]
              if i["code"] == "C9"}
        # '2,5' on a card is not '25' said (a digits-only compare passed it); '1.200' is '1 200' said in two tokens
        self.assertEqual(c9, {"g_dec": "error", "g_int": "info", "g_k": "info", "g_dot": "info"})

    def test_c1_limit_is_the_grammar_max_static(self):
        g = json.loads((PIPE / "style" / "grammar.json").read_text())
        g["rules"]["short"]["max_static"] = 12.0  # != the old hardcoded 7 s
        (self.d / "g.json").write_text(json.dumps(g))
        under, _, _ = self.critic(11.5, [], [], grammar=str(self.d / "g.json"))
        over, _, _ = self.critic(12.5, [], [], grammar=str(self.d / "g.json"))
        self.assertFalse([i for i in under if i["code"] == "C1"], under)
        self.assertTrue([i for i in over if i["code"] == "C1"], over)
        no_beats, _, _ = self.critic(self.lim + 0.5, [], None)  # without the beats key: the old fixed limit
        self.assertTrue([i for i in no_beats if i["code"] == "C1"])

    def test_fix_forces_the_strongest_moment_in_the_stretch(self):
        rows = [{"type": "question", "word": 9, "at": 9.0, "fx": "quiet"}, {"type": "stat", "word": 14, "at": 14.0, "fx": "spacing"},
                {"type": "maxim", "word": 20, "at": 20.0, "fx": "punch"}]
        bts = [{"type": "question", "word": 9}, {"type": "stat", "word": 14}, {"type": "maxim", "word": 20}]
        _, plan, out = self.critic(24.0, rows, bts, "--fix")
        self.assertEqual([b["type"] for b in plan["beats"] if b.get("force")], ["stat"], out)  # priority 90 > question 65
        self.assertIn("wymuszony", out)
        self.assertFalse([e for e in plan["emph"] if e.get("auto")])
        self.assertFalse(list(self.d.glob("*.tmp")))  # atomic write leaves no temp file

    def test_fix_without_a_moment_suggests_a_cut(self):
        issues, plan, _ = self.critic(24.0, [], [{"type": "stat", "word": 14}], "--fix")
        self.assertTrue([i for i in issues if i["code"] == "C1" and "rozważ cięcie" in i["msg"]], issues)
        self.assertFalse([b for b in plan["beats"] if b.get("force")])


if __name__ == "__main__":
    unittest.main()

"""Stock b-roll: where broll.py proposes clips, what stock.py downloads, the grammar section, the preview slice, the
timeline view. No network: the provider is replaced by fakes (stock.search/fetch) or a mocked stock._open."""
import copy
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from montazownia import preview, timeline
from tests.test_beats import _load

stock = _load("stock")
beatfx = _load("beatfx")
beats = _load("beats")
broll = _load("broll")

G = beatfx.load_grammar()
CFG = {"dur": 2.5, "lead": 0.0, "min_gap": 5.0, "max_share": 0.35, "not_before": 3.0, "per_topic": 1, "avoid": ["stat"]}
TOPICS = [["^aplikac", "using smartphone app"], ["^faktur", "office paperwork"], ["^magazyn", "warehouse shelves"]]


def grammar(**cfg):
    g = copy.deepcopy(G)
    g["broll"] = {"provider": "pexels", "topics": TOPICS, "short": {**CFG, **cfg}}
    return g


def film(text, step=0.5):
    """(plan, W, edl) of a short that keeps every word, one word every `step` s."""
    ws = text.split()
    W = [(k * step, k * step + step * 0.8, " " + w) for k, w in enumerate(ws)]
    plan = {"vertical": True, "keeps": [[0, len(ws) - 1]], "beats": []}
    return plan, W, {"segments": [{"in": 0.0, "out": len(ws) * step}], "overlays": []}


def clip(i):
    return stock.Clip("pexels", i, f"https://videos.pexels.com/{i}.mp4", 1080, 1920, 10.0, f"autor {i}", f"https://www.pexels.com/video/{i}/", 1000)


class Finder:
    def __init__(self, fail=(), down=False, empty=()):
        self.calls, self.fail, self.n, self.down, self.empty = [], set(fail), 0, down, set(empty)

    def find(self, provider, query, orientation, w, h, min_dur):
        self.calls.append((provider, query, orientation, w, h, min_dur))
        if self.down:
            raise stock.ProviderDown("Pexels: brak połączenia")
        if query in self.fail:
            raise stock.StockError("Pexels: wyszukiwanie nie powiodło się")
        if query in self.empty:
            return []
        self.n += 1
        return [clip(self.n), clip(self.n + 100)]

    @staticmethod
    def fetch(c, cache):
        return f"{cache}/{stock.cache_name(c)}"


FILLER = "to jest zwykłe zdanie o niczym ważnym i trwa dość długo."


class ProposeTest(unittest.TestCase):
    def propose(self, plan, W, edl, g=None, finder=None):
        f = finder or Finder()
        return broll.propose(plan, W, edl, g or grammar(), find=f.find, fetch=f.fetch, cache="/c"), f

    def test_topic_word_gets_a_clip_after_the_hook_band(self):
        plan, W, edl = film(f"aplikacja startuje {FILLER} {FILLER} a potem aplikacją robię {FILLER}")
        prop, f = self.propose(plan, W, edl)
        self.assertEqual([b["said"] for b in prop["broll"]], ["aplikacją"])  # the first one is under the hook title
        b = prop["broll"][0]
        self.assertEqual((b["query"], b["file"], b["dur"], b["clip_dur"]), ("using smartphone app", "/c/pexels_1_1080x1920.mp4", 2.5, 10.0))
        self.assertEqual(f.calls[0][2:], ("portrait", 1080, 1920, 3.0))
        self.assertEqual(prop["credits"], ["B-roll: Pexels — autor 1 (https://www.pexels.com/video/1/)"])

    def test_gap_per_topic_and_avoided_sentences(self):
        text = f"{FILLER} faktury magazyn {FILLER} {FILLER} faktury {FILLER} {FILLER} magazyn rośnie o 40 procent. {FILLER}"
        plan, W, edl = film(text)
        stat = next(i for i, w in enumerate(W) if w[2].strip() == "40")
        plan["beats"] = [{"type": "stat", "word": stat}]
        prop, _ = self.propose(plan, W, edl)
        self.assertEqual([b["said"] for b in prop["broll"]], ["faktury"])  # magazyn: too close; faktury again: per_topic; last: stat sentence
        plan["beats"][0]["off"] = True  # a switched-off moment does not protect its sentence
        self.assertEqual([b["said"] for b in self.propose(plan, W, edl)[0]["broll"]], ["faktury", "magazyn"])

    def test_share_limit_and_provider_errors_are_reported(self):
        plan, W, edl = film(f"{FILLER} faktury {FILLER} {FILLER} magazyn {FILLER} {FILLER} aplikacja {FILLER}")
        prop, _ = self.propose(plan, W, edl, grammar(max_share=0.1))
        self.assertEqual(len(prop["broll"]), 1)
        self.assertEqual(prop["skipped"][-1]["reason"], "limit udziału b-rollu w filmie")
        prop, _ = self.propose(plan, W, edl, finder=Finder(fail={"warehouse shelves"}))
        self.assertEqual([b["said"] for b in prop["broll"]], ["faktury", "aplikacja"])
        self.assertIn("nie powiodło się", prop["skipped"][0]["reason"])

    def test_provider_down_stops_after_the_first_query(self):
        plan, W, edl = film(f"{FILLER} faktury {FILLER} {FILLER} magazyn {FILLER} {FILLER} aplikacja {FILLER}")
        prop, f = self.propose(plan, W, edl, finder=Finder(down=True))
        self.assertEqual((len(f.calls), prop["broll"], prop["error"]), (1, [], "Pexels: brak połączenia"))
        self.assertEqual(self.propose(plan, W, edl)[0]["error"], None)

    def test_existing_clips_stay_and_are_not_reused(self):
        plan, W, edl = film(f"{FILLER} faktury {FILLER} {FILLER} magazyn {FILLER}")
        mag = next(i for i, w in enumerate(W) if w[2].strip() == "magazyn")
        plan["broll"] = [{"word": mag, "dur": 2.5, "lead": 0.0, "query": "warehouse shelves", "provider": "pexels", "id": 1,
                          "file": "/c/pexels_1_1080x1920.mp4"}]
        prop, _ = self.propose(plan, W, edl)
        self.assertEqual(prop["new"], 1)
        self.assertEqual([b["word"] for b in prop["broll"]], sorted([mag, prop["broll"][0]["word"]]))
        new = next(b for b in prop["broll"] if b["word"] != mag)
        self.assertEqual(new["id"], 101)  # clip 1 is taken by the plan's own b-roll

    def test_clips_and_words_the_editor_removed_never_come_back(self):
        plan, W, edl = film(f"{FILLER} faktury {FILLER} {FILLER} magazyn {FILLER}")
        fak = next(i for i, w in enumerate(W) if w[2].strip() == "faktury")
        plan["broll_rejected"] = [{"word": fak, "provider": "pexels", "id": 1}, "junk"]
        prop, _ = self.propose(plan, W, edl)
        self.assertEqual([b["said"] for b in prop["broll"]], ["magazyn"])
        self.assertEqual(prop["broll"][0]["id"], 101)  # clip 1 was rejected: the next result

    def test_a_query_that_failed_is_not_asked_again(self):
        text = f"{FILLER} faktury {FILLER} {FILLER} faktury {FILLER} {FILLER} faktury {FILLER}"
        for finder in (Finder(fail={"office paperwork"}), Finder(empty={"office paperwork"})):
            plan, W, edl = film(text)
            prop, f = self.propose(plan, W, edl, grammar(per_topic=3), finder=finder)
            self.assertEqual((len(f.calls), prop["broll"], len(prop["skipped"])), (1, [], 1))  # limit 200/h: one request

    def test_odd_plan_shapes_do_not_stop_the_proposal(self):
        plan, W, edl = film(f"{FILLER} faktury {FILLER}")
        plan["broll"] = [{"word": "1"}, {}, {"word": 10**6}, "x", {"word": 1, "dur": "x"}, {"word": 2, "dur": -1000},
                         {"word": 3, "id": [1]}, {"word": 4, "query": ["q"]}, {"word": 5, "provider": {"p": 1}}]
        plan["broll_rejected"] = None
        plan["beats"] = [{"type": "stat", "word": 0}]
        g = grammar()
        del g["broll"]["short"]["avoid"]  # optional in the validator: optional here too
        prop, _ = self.propose(plan, W, edl, g)
        self.assertEqual([b["said"] for b in prop["broll"]], ["faktury"])  # a negative dur no longer frees the share limit
        plan["broll"], plan["broll_rejected"] = [], [{"word": [1], "id": {"a": 1}}, {"word": 2, "provider": "pexels", "id": 7}]
        self.assertEqual([b["said"] for b in self.propose(plan, W, edl, g)[0]["broll"]], ["faktury"])

    def test_long_format_without_config_proposes_nothing(self):
        plan, W, edl = film(f"{FILLER} faktury {FILLER}")
        plan["vertical"] = False
        f = Finder()
        prop, f = self.propose(plan, W, edl, finder=f)
        self.assertEqual((prop["broll"], prop["format"], f.calls), ([], "long", []))

    def test_graphics_block_their_time(self):
        plan, W, edl = film(f"{FILLER} faktury {FILLER}")
        i = next(i for i, w in enumerate(W) if w[2].strip() == "faktury")
        edl["overlays"] = [{"id": "k", "at": W[i][0] + 1.0, "dur": 2.0}]
        self.assertEqual(self.propose(plan, W, edl)[0]["broll"], [])

    def test_topic_matches_one_word_without_punctuation(self):
        self.assertEqual(broll.topic_of(" Faktury,", TOPICS), "office paperwork")
        self.assertIsNone(broll.topic_of(" fak", TOPICS))
        for rx, q in G["broll"]["topics"]:  # the real list: every regex compiles, every query is plain English
            self.assertTrue(q.isascii() and q.strip(), q)
            self.assertIsNone(broll.topic_of(" ", [[rx, q]]))


class FakeResponse(io.BytesIO):
    def __init__(self, data, url, length=None):
        super().__init__(data)
        self.url = url
        self.headers = Message()
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Trickle(BaseHTTPRequestHandler):
    """/trickle: 1 byte every 0.1 s for 5 s; /cut: Content-Length 3000, 10 bytes, then the connection drops."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "3000")
        self.end_headers()
        if self.path == "/cut":
            self.wfile.write(b"x" * 10)
            return
        for _ in range(50):
            self.wfile.write(b"x")
            self.wfile.flush()
            time.sleep(0.1)

    def log_message(self, format, *args):
        pass


class StockSocketTest(unittest.TestCase):
    """Real sockets (127.0.0.1): read() would wait for a whole buffer; the deadline must hold between bytes."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="broll-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"

    def fetch(self, path):
        c = stock.Clip("pexels", 5, self.base + path, 1080, 1920, 10.0, "", "", 0)
        with mock.patch.object(stock, "_host_ok", return_value=True), mock.patch.object(stock, "DEADLINE", 0.5), \
             mock.patch.object(stock, "_open", side_effect=lambda req, _: stock.urllib.request.urlopen(req, timeout=2)):
            return stock.fetch(c, str(self.dir))

    def test_trickling_stream_stops_at_the_deadline(self):
        t = time.monotonic()
        with self.assertRaises(stock.StockError) as e:
            self.fetch("/trickle")
        self.assertIn("dłuższe", str(e.exception))
        self.assertLess(time.monotonic() - t, 2.0)  # not the 5 s the server would take
        self.assertEqual(os.listdir(self.dir), [])

    def test_dropped_connection_leaves_no_clip(self):
        with self.assertRaises(stock.StockError):
            self.fetch("/cut")
        self.assertEqual(os.listdir(self.dir), [])


class StockTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="broll-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_downloads_only_from_the_provider_host_and_only_once(self):
        c = clip(7)
        with mock.patch.object(stock, "_open", return_value=FakeResponse(b"mp4", c.url)) as op:
            p = stock.fetch(c, str(self.dir))
            self.assertEqual(Path(p).read_bytes(), b"mp4")
            self.assertEqual(stock.fetch(c, str(self.dir)), p)  # cache hit
            self.assertEqual(op.call_count, 1)
        for bad in ("http://videos.pexels.com/x.mp4", "https://evil.example/x.mp4", "https://videos.pexels.com.evil.example/x.mp4", "file:///etc/passwd"):
            with self.subTest(url=bad), mock.patch.object(stock, "_open") as op:
                with self.assertRaises(stock.StockError):
                    stock.fetch(stock.Clip("pexels", 8, bad, 1080, 1920, 10.0, "", "", 0), str(self.dir))
                op.assert_not_called()

    def test_timeout_stall_and_broken_stream_leave_no_file(self):
        c = clip(11)

        class Broken(FakeResponse):
            def read1(self, *a):
                raise TimeoutError("read timed out")

        class Cut(FakeResponse):
            def read1(self, *a):
                raise stock.http.client.IncompleteRead(b"x", 10)
        for resp in (Broken(b"", c.url), Cut(b"", c.url)):
            with self.subTest(resp=type(resp).__name__), mock.patch.object(stock, "_open", return_value=resp), \
                 self.assertRaises(stock.StockError):
                stock.fetch(c, str(self.dir))
        with mock.patch.object(stock, "_open", side_effect=TimeoutError("connect")), \
             self.assertRaises(stock.StockError):
            stock.fetch(c, str(self.dir))
        clock = iter([0.0, 0.0, 1000.0, 1000.0])  # deadline set at 0, passed after the first chunk
        with mock.patch.object(stock, "_open", return_value=FakeResponse(b"x" * (3 << 20), c.url)), \
             mock.patch.object(stock.time, "monotonic", side_effect=lambda: next(clock)), self.assertRaises(stock.StockError) as e:
            stock.fetch(c, str(self.dir))
        self.assertIn("dłuższe", str(e.exception))
        self.assertEqual(os.listdir(self.dir), [])
        old = self.dir / "pexels_1_1x1.mp4.99.part"  # left by a killed job an hour ago: swept on the next download
        old.write_bytes(b"x")
        os.utime(old, (0, 0))
        with mock.patch.object(stock, "_open", return_value=FakeResponse(b"mp4", c.url)):
            stock.fetch(c, str(self.dir))
        self.assertFalse(old.exists())

    def test_cut_connection_is_never_cached(self):
        c = clip(12)  # a dropped connection ends like EOF: shorter than Content-Length, or nothing at all
        for resp in (FakeResponse(b"mp4", c.url, length=1000), FakeResponse(b"", c.url)):
            with self.subTest(n=len(resp.getvalue())), mock.patch.object(stock, "_open", return_value=resp), \
                 self.assertRaises(stock.StockError) as e:
                stock.fetch(c, str(self.dir))
            self.assertIn("niecały", str(e.exception))
        self.assertEqual(os.listdir(self.dir), [])
        with mock.patch.object(stock, "_open", return_value=FakeResponse(b"mp4", c.url, length="abc")):
            self.assertTrue(Path(stock.fetch(c, str(self.dir))).exists())  # a bad header = size unknown, not a crash

    def test_redirect_is_checked_before_it_is_followed(self):
        req = stock.urllib.request.Request("https://videos.pexels.com/1.mp4")
        h = stock._SameHost({"videos.pexels.com"})
        for bad in ("https://evil.example/x", "http://videos.pexels.com/x", "http://127.0.0.1:5678/", "ftp://videos.pexels.com/x"):
            with self.subTest(url=bad), self.assertRaises(stock.StockError):
                h.redirect_request(req, None, 302, "Found", Message(), bad)
        self.assertIsNotNone(h.redirect_request(req, None, 302, "Found", Message(), "https://videos.pexels.com/2.mp4"))

    def test_redirect_away_and_oversize_leave_no_file(self):
        c = clip(9)
        with mock.patch.object(stock, "_open", return_value=FakeResponse(b"x", "https://evil.example/x")), \
             self.assertRaises(stock.StockError):
            stock.fetch(c, str(self.dir))
        with mock.patch.object(stock, "MAX_BYTES", 4), \
             mock.patch.object(stock, "_open", return_value=FakeResponse(b"123456789", c.url)), \
             self.assertRaises(stock.StockError):
            stock.fetch(c, str(self.dir))
        with self.assertRaises(stock.StockError):
            stock.fetch(stock.Clip("pexels", 10, c.url, 1080, 1920, 10.0, "", "", stock.MAX_BYTES + 1), str(self.dir))
        self.assertEqual(os.listdir(self.dir), [])

    def test_pexels_picks_the_smallest_rendition_that_fills_the_frame(self):
        def f(w, h, host="videos.pexels.com"):
            return {"file_type": "video/mp4", "width": w, "height": h, "link": f"https://{host}/{w}x{h}.mp4", "size": 5}
        data = {"videos": [
            {"id": 1, "duration": 9, "url": "u1", "user": {"name": "A"}, "video_files": [f(720, 1280), f(1080, 1920, "evil.example")]},
            {"id": 2, "duration": 2, "url": "u2", "user": {"name": "B"}, "video_files": [f(1080, 1920)]},  # too short
            {"id": 3, "duration": 9, "url": "u3", "user": None, "video_files": [f(2160, 3840), f(1080, 1920), f(720, 1280)]},
            {"id": "4", "duration": 9, "video_files": [f(1080, 1920)]}, "junk"]}
        resp = FakeResponse(json.dumps(data).encode(), "https://api.pexels.com/videos/search")
        with mock.patch.dict(os.environ, {"Pexels": "k"}), mock.patch.object(stock, "_open", return_value=resp) as op:
            got = stock.search("pexels", "warehouse", "portrait", 1080, 1920, 3.0)
        req = op.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "k")
        self.assertNotIn("Authorization", req.headers)  # unredirected: never sent to a redirect's host
        self.assertEqual(op.call_args[0][1], {"api.pexels.com"})  # the search follows redirects only on its own host
        moved = stock._SameHost({"api.pexels.com"}).redirect_request(req, None, 302, "Found", Message(), "https://api.pexels.com/v2")
        self.assertIsNotNone(moved)
        self.assertNotIn("authorization", {k.lower() for k, _ in moved.header_items()} if moved else set())
        self.assertIn("orientation=portrait", req.full_url)
        self.assertEqual([(c.id, c.width, c.author) for c in got], [(3, 1080, "")])

    def test_missing_key_and_unknown_provider(self):
        for code, why in ((401, "zły klucz"), (429, "limit zapytań"), (500, "HTTP 500")):
            err = stock.urllib.error.HTTPError("u", code, "x", Message(), None)
            with self.subTest(code=code), mock.patch.dict(os.environ, {"Pexels": "k"}), \
                 mock.patch.object(stock, "_open", side_effect=err), self.assertRaises(stock.ProviderDown) as e:
                stock.search("pexels", "x", "portrait", 1, 1, 1)
            self.assertIn(why, str(e.exception))
        for err in (stock.urllib.error.URLError("dns"), stock.http.client.IncompleteRead(b"{", 10)):
            with self.subTest(err=type(err).__name__), mock.patch.dict(os.environ, {"Pexels": "k"}), \
                 self.assertRaises(stock.ProviderDown), mock.patch.object(stock, "_open", side_effect=err):
                stock.search("pexels", "x", "portrait", 1, 1, 1)
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(stock.ProviderDown) as e:
            stock.search("pexels", "x", "portrait", 1, 1, 1)
        self.assertIn("infisical_bridge", str(e.exception))
        with self.assertRaises(stock.ProviderDown):
            stock.search("nope", "x", "portrait", 1, 1, 1)


class GrammarTest(unittest.TestCase):
    def test_broll_section_is_checked(self):
        self.assertEqual(beatfx._broll_bad(None, G["beats"]), [])
        self.assertEqual(beatfx._broll_bad(G["broll"], G["beats"]), [])
        ok = grammar()["broll"]
        for bad, key in ((dict(ok, topics=[["(", "x"]]), "broll.topics"), (dict(ok, short={**CFG, "dur": 20}), "broll.short.dur"),
                         (dict(ok, short={**CFG, "avoid": ["zzz"]}), "broll.short.avoid"), (dict(ok, short={**CFG, "avoid": [["x"]]}), "broll.short.avoid"), (dict(ok, provider=1), "broll.provider"),
                         (dict(ok, short={**CFG, "per_topic": 1.5}), "broll.short.per_topic"), ("x", "broll")):
            with self.subTest(key=key):
                self.assertIn(key, beatfx._broll_bad(bad, G["beats"]))


EDL = {"segments": [{"in": 0.0, "out": 30.0, "zoom": 1.0}], "overlays": [], "tail": 0,
           "broll": [{"file": "/c/a.mp4", "at": 8.0, "dur": 3.0, "ss": 1.0, "word": 20, "query": "q", "id": "broll_20"},
                     {"file": "/c/b.mp4", "at": 25.0, "dur": 2.5, "ss": 0.0, "word": 60, "query": "q2", "id": "broll_60"}]}


class PreviewAndTimelineTest(unittest.TestCase):

    def test_slice_cuts_clips_at_the_window(self):
        out = preview.slice_edl(EDL, 10.0, 26.0)
        self.assertEqual(out["broll"], [{**EDL["broll"][0], "at": 0.0, "ss": 3.0, "dur": 1.0},
                                        {**EDL["broll"][1], "at": 15.0, "dur": 1.0}])
        self.assertEqual(preview.slice_edl(EDL, 12.0, 20.0)["broll"], [])
        self.assertNotIn("broll", preview.slice_edl({k: v for k, v in EDL.items() if k != "broll"}, 10.0, 26.0))

    def test_timeline_lists_clips_with_their_plan_position(self):
        plan = {"broll": [{"word": 60, "said": "magazyn", "author": "A", "url": "u"}, {"word": 20, "said": "faktury"}]}
        v = timeline.broll_view(EDL, plan)
        self.assertEqual([(b["pos"], b["said"], b["start"], b["end"]) for b in v], [(1, "faktury", 8.0, 11.0), (0, "magazyn", 25.0, 27.5)])

    def test_timeline_survives_odd_plans_and_points_at_the_placed_entry(self):
        for odd in ([{"word": [1]}, {"word": {"a": 1}}, "x", {"word": True}], None, 5):
            with self.subTest(broll=odd):
                self.assertEqual([b["pos"] for b in timeline.broll_view(EDL, {"broll": odd})], [None, None])
        two = {"broll": [{"word": 20, "said": "first"}, {"word": 20, "said": "second"}]}  # place() keeps the first
        first = timeline.broll_view(EDL, two)[0]
        self.assertEqual((first["pos"], first["said"]), (0, "first"))
        # the first entry has no cached file, so place() skipped it and laid out the second: point at that one
        gone = {"broll": [{"word": 20, "said": "gone", "file": "/c/gone.mp4"}, {"word": 20, "said": "placed", "file": "/c/a.mp4"}]}
        hit = timeline.broll_view(EDL, gone)[0]
        self.assertEqual((hit["pos"], hit["said"]), (1, "placed"))


if __name__ == "__main__":
    unittest.main()

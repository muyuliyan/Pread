from argparse import Namespace
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError

from pread.arxiv import Arxiv, parse_feed
from pread.cli import ensure_week, main
from pread.ranking import load_config, rank, score_signals
from pread.store import Paper, connect, latest_papers, save_papers

ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2601.12345v2</id>
<title>FlashAttention and GPU Kernels</title><summary>Roofline experiments</summary>
<published>2026-01-01T00:00:00Z</published><updated>2026-01-02T00:00:00Z</updated>
<category term="cs.DC"/><category term="cs.LG"/><author><name>Example Author</name></author>
</entry></feed>"""


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.directory = Path(workspace.name)
        self.db_path = self.directory / "research.db"
        self.paper = parse_feed(ATOM)[0]
        self.today = date(2026, 1, 2)
        self.config = load_config()

    def test_atom_versions_dedup_and_rank(self):
        self.assertEqual(self.paper.categories, ["cs.DC", "cs.LG"])
        with connect(self.db_path) as db:
            save_papers(db, [self.paper, self.paper, replace(self.paper, version=1)])
            self.assertEqual(latest_papers(db), [self.paper])
            card, = rank(db, self.config, self.today)
            self.assertEqual(card["coverage"], .45)
            self.assertIsNone(card["signals"]["quality"])
            self.assertTrue(card["needs_review"])
            db.execute("INSERT INTO paper_state VALUES (?, 'skipped')", (self.paper.id,))
            self.assertEqual(rank(db, self.config, self.today), [])

    def test_week_does_not_recommend_again_and_respects_pause(self):
        args = Namespace(no_collect=True, days=30, semantic=False)
        with connect(self.db_path) as db:
            self.assertEqual(ensure_week(db, args, self.config, self.today)["status"], "no-candidates")
            save_papers(db, [self.paper])
            first = ensure_week(db, args, self.config, self.today)
            save_papers(db, [replace(self.paper, version=3)])
            self.assertEqual(ensure_week(db, args, self.config, self.today), first)
            db.execute("INSERT INTO settings VALUES ('paused', '1')")
            self.assertEqual(ensure_week(db, args, self.config, self.today)["status"], "paused")

    def test_arxiv_rate_limit_backoff(self):
        current = [0.0]
        attempts = []

        def open_url(url):
            attempts.append(current[0])
            if len(attempts) == 1:
                raise HTTPError(url, 429, "limited", {"Retry-After": "7"}, None)
            return BytesIO(ATOM.encode())

        def sleep(delay):
            current[0] += delay

        client = Arxiv(open_url, sleep, lambda: current[0])
        self.assertEqual(list(client.pages(["cs.DC"], 30, 3)), [[self.paper]])
        self.assertEqual(attempts, [0, 7])

    def test_cli_marker_round_trip(self):
        fixture = self.directory / "feed.xml"
        fixture.write_text(ATOM, encoding="utf-8")
        snapshot = self.directory / "snapshot.json"
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--db", str(self.db_path), "collect", "--atom", str(fixture)]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"collected": 1})
        with snapshot.open("w", encoding="utf-8") as stream, redirect_stdout(stream):
            self.assertEqual(main(["--db", str(self.db_path), "export-markers"]), 0)
        other = self.directory / "local.db"
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["--db", str(other), "import-markers", str(snapshot)]), 0)
        with connect(other) as db:
            self.assertEqual(latest_papers(db), [self.paper])
        with self.assertRaises(ValueError):
            Paper("../../other", 1, "bad", "", "2026-01-01", "2026-01-01", [], [])

    def test_coverage_keeps_unknown_distinct_from_zero(self):
        self.assertEqual(score_signals({"a": 0, "b": None}, {"a": .3, "b": .7}), (0, .3))

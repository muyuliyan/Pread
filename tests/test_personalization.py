from argparse import Namespace
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pread.cli import ensure_week
from pread.personalization import calibrated_config, feedback_report, link, links, record_feedback, review
from pread.ranking import load_config, rank, rerank
from pread.store import Paper, connect, save_papers


class ModelStub:
    name = "stub"

    def __init__(self, quote):
        self.quote = quote
        self.calls = 0

    def identity(self):
        return "stub:" + self.quote

    def complete(self, prompt, payload):
        self.calls += 1
        return json.dumps({
            "quality": 0.9, "novelty": 0.8, "reproducibility": 0.6, "learning_value": 0.7,
            "contribution": "加速内核", "limitations": [], "prerequisites": ["CUDA"],
            "reading_minutes": [180, 225], "evidence": [
                {"key": json.loads(payload)["evidence"][0]["key"], "quote": self.quote},
            ],
        })


class PersonalizationTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / "research.db"
        self.paper = Paper("2601.10001", 1, "Mamba Transformer", "State Space Model baseline.",
                           "2026-01-01", "2026-01-01", [], [])
        self.prior = Paper("2512.10001", 1, "Transformer baseline", "An older baseline.",
                           "2025-12-10", "2025-12-10", [], [])
        self.config = load_config()
        with connect(self.path) as db:
            save_papers(db, [self.paper, self.prior])
            db.execute("INSERT INTO document VALUES (?, 1, 'test')", (self.paper.id,))
            db.execute("INSERT INTO paragraph VALUES (?, 1, 0, 'p0003-001', 3, 'Results', ?)",
                       (self.paper.id, "The baseline uses 16 GPUs."))

    def test_review_checks_exact_evidence_and_caches(self):
        with connect(self.path) as db:
            with self.assertRaisesRegex(ValueError, "validation"):
                review(db, self.paper, ModelStub("32 GPUs"))
            model = ModelStub("16 GPUs")
            result = review(db, self.paper, model)
            self.assertIsNone(result["novelty"])
            self.assertEqual(result["evidence"][0]["page"], 3)
            self.assertEqual(review(db, self.paper, model), result)
            self.assertEqual(model.calls, 1)

    def test_feedback_calibration_and_evidence_link(self):
        args = Namespace(no_collect=True, days=30, semantic=False, review=False)
        with connect(self.path) as db:
            self.assertEqual(calibrated_config(db, self.config), self.config)
            for day in (2, 9, 16, 23):
                result = ensure_week(db, args, self.config, date(2026, 1, day))
                record_feedback(db, result["week"], self.paper.id, 211, 5, 4, 5, True, "")
            self.assertEqual(feedback_report(db)["budget_fit_rate"], 1.0)
            adjusted = calibrated_config(db, self.config)
            self.assertGreater(adjusted["topics"]["model_architecture"]["weight"], .9)
            self.assertEqual(self.config["topics"]["model_architecture"]["weight"], .9)
            with self.assertRaises(ValueError):
                link(db, self.paper.id, self.prior.id, "compares", "")
            link(db, self.paper.id, self.prior.id, "compares", "Results, page 3")
            self.assertEqual(links(db, self.paper.id)[0]["evidence"], "Results, page 3")

    def test_reranking_updates_relevance_without_inventing_evidence(self):
        class RerankerStub:
            def predict(self, pairs):
                return [.9 for _ in pairs]

        with connect(self.path) as db:
            cards = rank(db, self.config, date(2026, 1, 2))
            result = rerank(cards, self.config, RerankerStub())
        card = next(card for card in result if card["id"] == self.paper.id)
        self.assertAlmostEqual(card["reranker_relevance"], .81)
        self.assertIsNone(card["signals"]["quality"])
        self.assertEqual(card["coverage"], .45)

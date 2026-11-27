from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit

from pread.evidence import SOURCES, enrich
from pread.ranking import load_config, rank
from pread.store import Paper, connect, paper_evidence, save_papers


class EvidenceTests(unittest.TestCase):
    def test_store_source_metadata_and_preserve_cached_result_on_error(self):
        paper = Paper("2601.10001", 1, "GPU Kernel", "Roofline", "2026-01-01", "2026-01-01", [], [])
        fixtures = {
            "api.semanticscholar.org": {
                "url": "https://www.semanticscholar.org/paper/example", "externalIds": {"ArXiv": paper.id},
                "citationCount": 10, "referenceCount": 20, "influentialCitationCount": 2,
            },
            "huggingface.co": {"id": paper.id, "upvotes": 10},
            "api.github.com": {"html_url": "https://github.com/example/kernel", "stargazers_count": 500},
            "api.openalex.org": {"id": "https://openalex.org/W100", "cited_by_count": 8},
            "api2.openreview.net": {"notes": [
                {"id": "review", "forum": "example", "invitations": ["venue/Submission1/Review"],
                 "content": {"summary": {"value": "Public assessment"}}},
                {"id": "decision", "forum": "example", "content": {"decision": {"value": "Accept"}}},
            ]},
        }

        def fetch(url, headers=None):
            return fixtures[urlsplit(url).hostname]

        def unavailable(url, headers=None):
            raise HTTPError(url, 429, "limited", {}, None)

        with TemporaryDirectory() as folder, connect(Path(folder) / "test.db") as db:
            save_papers(db, [paper])
            result = enrich(db, paper, SOURCES, {
                "github": "example/kernel", "openreview": "example", "openalex": "W100",
            }, fetch)
            self.assertEqual(set(result["updated"]), set(SOURCES))
            self.assertEqual(result["updated"]["openreview"]["reviews"][0]["summary"], "Public assessment")
            failed = enrich(db, paper, ["semantic-scholar"], {}, unavailable)
            self.assertEqual(failed["unavailable"], {"semantic-scholar": "HTTP 429"})
            self.assertEqual(paper_evidence(db, paper.id)[paper.id], result["updated"])
            card, = rank(db, load_config(), date(2026, 1, 2))
            self.assertGreater(card["signals"]["impact"], 0)
            self.assertIsNone(card["signals"]["quality"])
            self.assertEqual(card["coverage"], .5)

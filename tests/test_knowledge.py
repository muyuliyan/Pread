from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pread.cli import main
from pread.knowledge import add_term, answer, search
from pread.store import Paper, connect, save_papers


class Encoder:
    def encode(self, texts, normalize_embeddings=True):
        return [[1.0, 0.0] if "GPU" in text else [0.0, 1.0] for text in texts]


class Model:
    def __init__(self, source):
        self.source = source

    def complete(self, instructions, payload):
        return json.dumps({"answer": "结论", "sources": [self.source]})


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "research.db"
        self.first = Paper("2601.10001", 1, "Algorithms", "", "2026-01-01", "2026-01-01", [], [])
        self.second = Paper("2601.10002", 1, "Systems", "", "2026-01-01", "2026-01-01", [], [])
        with connect(self.path) as db:
            save_papers(db, [self.first, self.second])
            for paper, text in [(self.first, "GPU matrix multiplication."), (self.second, "Kernel scheduling.")]:
                db.execute("INSERT INTO document VALUES (?, 1, 'test')", (paper.id,))
                db.execute("INSERT INTO paragraph VALUES (?, 1, 0, 'p0004-001', 4, 'Results', ?)", (paper.id, text))

    def test_glossary_precedes_model_and_cli_cites_page(self):
        with connect(self.path) as db:
            add_term(db, "  GPU  ", "图形处理器", "Hardware notes")
            self.assertEqual(answer(db, "gpu", model=self.fail)["status"], "glossary")
        stream = StringIO()
        with redirect_stdout(stream):
            self.assertEqual(main(["--db", str(self.path), "lookup", "matrix", "--paper", self.first.id]), 0)
        result = json.loads(stream.getvalue())
        self.assertIn("#page=4", result["sources"][0]["url"])

    def test_hybrid_scope_and_validation(self):
        with connect(self.path) as db:
            self.assertEqual(search(db, "kernel", self.first), [])
            sources = search(db, "GPU kernel", model_name="stub", encoder=Encoder())
            self.assertEqual([source["id"] for source in sources], [self.first.id, self.second.id])
            result = answer(db, "kernel", model=Model("S1"))
            self.assertEqual(result["sources"][0]["id"], self.second.id)
            with self.assertRaisesRegex(ValueError, "evidence"):
                answer(db, "kernel", model=Model("S99"))

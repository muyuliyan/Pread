from contextlib import redirect_stdout
from io import BytesIO, StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pread.cli import main
from pread.reading import (
    add_note, export_notes, paragraphs, prepare, progress, save_progress, translate,
)
from pread.store import Paper, connect, save_papers

try:
    import pymupdf
except ImportError:
    pymupdf = None


class ModelStub:
    def __init__(self, name="test"):
        self.name = name
        self.calls = []

    def identity(self):
        return self.name

    def complete(self, instructions, source):
        self.calls.append((instructions, source))
        return "中文译文"


@unittest.skipUnless(pymupdf is not None, "Install the reader extra to test PDF handling")
class ReaderTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.db_path = self.root / "research.db"
        self.paper = Paper("2601.12345", 2, "Paper", "Summary", "2026-01-01", "2026-01-02", [], [])
        self.source = self.root / "source.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((60, 60), "Introduction\nGPU kernel throughput improved.")
            pdf.new_page().insert_text((60, 60), "Results\nThe baseline uses 16 GPUs.")
            pdf.save(self.source)
        with connect(self.db_path) as db:
            save_papers(db, [self.paper])

    def test_download_parse_and_keep_exact_version(self):
        urls = []

        def fetch(url):
            urls.append(url)
            return BytesIO(self.source.read_bytes())

        with connect(self.db_path) as db:
            with self.assertRaises(ValueError):
                prepare(db, self.root, self.paper, open_url=lambda _: BytesIO(b"html"))
            first = prepare(db, self.root, self.paper, open_url=fetch)
            self.assertEqual(prepare(db, self.root, self.paper, open_url=fetch), first)
            self.assertEqual(first["paragraphs"], 2)
            self.assertEqual(Path(first["pdf"]).read_bytes(), self.source.read_bytes())
            self.assertEqual([row["section"] for row in paragraphs(db, self.paper)], ["Introduction", "Results"])
        self.assertEqual(urls, ["https://arxiv.org/pdf/2601.12345v2"])

    def test_cache_progress_notes_and_cli_version(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["--db", str(self.db_path), "prepare", self.paper.id,
                                   "--version", "2", "--pdf", str(self.source)]), 0)
        with connect(self.db_path) as db:
            first, second = paragraphs(db, self.paper)
            model = ModelStub()
            self.assertIsNone(translate(db, self.paper, first, model, cached_only=True))
            self.assertEqual(translate(db, self.paper, first, model), "中文译文")
            self.assertEqual(translate(db, self.paper, first, model), "中文译文")
            self.assertEqual(len(model.calls), 1)
            self.assertIsNone(translate(db, self.paper, first, ModelStub("other"), cached_only=True))
            save_progress(db, self.paper, first["anchor"], 32)
            save_progress(db, self.paper, second["anchor"], 13)
            self.assertEqual(tuple(progress(db, self.paper)), (second["anchor"], 45))
            add_note(db, self.paper, second["anchor"], "Benchmark note")
            self.assertIn("Results · p. 2", export_notes(db, self.paper))

    def test_desktop_navigation_pdf_and_modes(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtTest import QTest
            from PySide6.QtWidgets import QApplication
            from pread.desktop import Reader
        except ImportError:
            self.skipTest("Qt is unavailable")
        app = QApplication.instance() or QApplication([])
        with connect(self.db_path) as db:
            prepare(db, self.root, self.paper, self.source)
        window = Reader(self.db_path, self.paper.id, self.paper.version)
        window.show()
        app.processEvents()
        self.assertTrue(window.pdf_loaded)
        window.outline.setCurrentRow(1)
        self.assertIn("16 GPUs", window.original.toPlainText())
        self.assertEqual(window.pdf.pageNavigator().currentPage(), 1)
        window.modes.setCurrentIndex(2)
        self.assertTrue(window.original.isHidden())
        window.modes.setCurrentIndex(3)
        self.assertEqual(window.views.currentWidget(), window.pdf)
        window.lookup_window.query.setText("throughput")
        window.lookup_window.lookup()
        for _ in range(100):
            if not window.busy:
                break
            QTest.qWait(20)
        self.assertEqual(window.lookup_window.sources.count(), 1)
        window.lookup_window.open_source(window.lookup_window.sources.item(0))
        self.assertEqual(window.paragraph_list.currentRow(), 0)
        self.assertTrue(window.close())
        window.deleteLater()
        QTest.qWait(50)

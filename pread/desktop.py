import json
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QMainWindow, QPushButton, QSplitter, QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .model import Model
from .reading import (
    add_note, export_notes, get_document, paragraphs, pdf_path, prepare, progress, save_progress, translate,
)
from .store import connect, get_paper, latest_papers


class JobSignals(QObject):
    done = Signal(object)


class Job(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = JobSignals()

    def run(self):
        try:
            self.signals.done.emit((True, self.operation()))
        except Exception as error:
            self.signals.done.emit((False, str(error)))


class Reader(QMainWindow):
    def __init__(self, db_path, paper_id=None, version=None, today=None):
        super().__init__()
        self.db_path = db_path
        self.root = db_path.parent
        self.paper = None
        self.rows = []
        self.started = monotonic()
        self.timing = True
        self.elapsed = 0
        self.busy = False
        self.setWindowTitle("Pread · 双语论文精读")
        self.resize(1120, 740)
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)
        self.library = QComboBox()
        self.library.setMinimumContentsLength(32)
        self.library.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        toolbar.addWidget(self.library, 1)
        for label, action in (
            ("准备 PDF", self.prepare_paper), ("翻译当前段", self.translate_current),
            ("添加笔记", self.note), ("导出笔记", self.export),
        ):
            button = QPushButton(label)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        self.modes = QComboBox()
        self.modes.addItems(["中英双栏", "英文原文", "中文译文", "原版 PDF"])
        toolbar.addWidget(self.modes)

        content = QSplitter(Qt.Orientation.Horizontal)
        self.outline = QListWidget()
        content.addWidget(self.outline)
        self.paragraph_list = QListWidget()
        content.addWidget(self.paragraph_list)
        self.views = QStackedWidget()
        content.addWidget(self.views)
        content.setSizes([160, 240, 700])
        layout.addWidget(content, 1)
        self.dual = QSplitter(Qt.Orientation.Horizontal)
        self.original = self.text_pane()
        self.translated = self.text_pane()
        self.dual.addWidget(self.original)
        self.dual.addWidget(self.translated)
        self.views.addWidget(self.dual)
        self.document = QPdfDocument(self)
        self.pdf = QPdfView()
        self.pdf.setDocument(self.document)
        self.pdf.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.views.addWidget(self.pdf)
        self.detail = QLabel()
        footer = QHBoxLayout()
        layout.addLayout(footer)
        footer.addWidget(self.detail, 1)
        self.budget = QComboBox()
        self.budget.addItem("3 小时预算", 180)
        self.budget.addItem("4 小时预算", 240)
        self.budget.currentIndexChanged.connect(self.show_clock)
        footer.addWidget(self.budget)
        self.timer_button = QPushButton("暂停计时")
        self.timer_button.clicked.connect(self.toggle_timer)
        footer.addWidget(self.timer_button)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.show_clock)
        self.clock.start(1000)

        self.outline.currentRowChanged.connect(self.jump_section)
        self.paragraph_list.currentRowChanged.connect(self.show_paragraph)
        self.library.currentIndexChanged.connect(self.open_paper)
        self.modes.currentIndexChanged.connect(self.change_mode)
        with connect(db_path) as db:
            papers = latest_papers(db, eligible=False)
            chosen = get_paper(db, paper_id, version) if paper_id else None
            recommendation = None
            if today is not None:
                week = today.isocalendar()
                recommendation = db.execute("SELECT payload FROM recommendation WHERE week = ?",
                                            (f"{week.year}-W{week.week:02d}",)).fetchone()
            ordered = []
            if recommendation:
                rec = json.loads(recommendation[0])
                ordered = [(get_paper(db, card["id"], card["version"]), label)
                           for label, card in zip(["主推荐", "备选 1", "备选 2"],
                                                  [rec["primary"], *rec["alternatives"]])]
        if chosen is not None and chosen not in [paper for paper, _ in ordered]:
            ordered.insert(0, (chosen, ""))
        featured = {(paper.id, paper.version) for paper, _ in ordered}
        ordered.extend((paper, "") for paper in papers if (paper.id, paper.version) not in featured)
        self.library.blockSignals(True)
        for paper, label in ordered:
            prefix = f"{label} · " if label else ""
            self.library.addItem(f"{prefix}{paper.title} · v{paper.version}", paper)
        if chosen is not None:
            self.library.setCurrentIndex(next(index for index, (paper, _) in enumerate(ordered) if paper == chosen))
        self.library.blockSignals(False)
        self.open_paper(self.library.currentIndex())

    @staticmethod
    def text_pane():
        pane = QTextEdit()
        pane.setReadOnly(True)
        return pane

    def show_clock(self):
        if self.paper is None:
            self.detail.setText("论文库为空，请先运行 research collect。")
            return
        seconds = self.elapsed + self.current_seconds()
        self.detail.setText(f"已阅读 {seconds // 60} 分钟 · 预算剩余 "
                            f"{max(0, self.budget.currentData() - seconds // 60)} 分钟")

    def current_seconds(self):
        return int(monotonic() - self.started) if self.timing else 0

    def toggle_timer(self):
        self.flush_progress()
        self.timing = not self.timing
        self.timer_button.setText("暂停计时" if self.timing else "继续计时")
        self.show_clock()

    def flush_progress(self):
        if self.paper is not None:
            index = self.paragraph_list.currentRow()
            anchor = self.rows[index]["anchor"] if index >= 0 else ""
            with connect(self.db_path) as db:
                save_progress(db, self.paper, anchor, self.current_seconds())
                self.elapsed = progress(db, self.paper)["seconds"]
            self.started = monotonic()

    def open_paper(self, index):
        self.flush_progress()
        self.paper = self.library.itemData(index) if index >= 0 else None
        self.started = monotonic()
        self.load_paper()

    def load_paper(self):
        self.rows = []
        self.section_rows = []
        self.pdf_loaded = False
        self.document.close()
        self.outline.clear()
        self.paragraph_list.clear()
        self.original.clear()
        self.translated.clear()
        if self.paper is None:
            self.show_clock()
            return
        with connect(self.db_path) as db:
            saved = get_document(db, self.paper)
            self.rows = paragraphs(db, self.paper)
            previous = progress(db, self.paper)
        self.elapsed = previous["seconds"] if previous else 0
        if saved:
            path = pdf_path(self.root, self.paper, saved["sha256"])
            if path.is_file():
                self.pdf_loaded = self.document.load(str(path)) == QPdfDocument.Error.None_
        if not self.rows:
            self.original.setPlainText(self.paper.abstract)
            self.translated.setPlainText("暂无可翻译的段落，点击“准备 PDF”。扫描版可切换至原版 PDF。")
        prior_section = None
        selected = 0
        for index, row in enumerate(self.rows):
            if row["section"] != prior_section:
                self.outline.addItem(row["section"])
                self.section_rows.append(index)
                prior_section = row["section"]
            self.paragraph_list.addItem(f"p. {row['page']} · {row['text'][:70]}")
            if previous and row["anchor"] == previous["anchor"]:
                selected = index
        if self.rows:
            self.paragraph_list.setCurrentRow(selected)
        self.show_clock()

    def jump_section(self, index):
        if 0 <= index < len(self.section_rows):
            self.paragraph_list.setCurrentRow(self.section_rows[index])

    def show_paragraph(self, index):
        if not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        self.original.setPlainText(row["text"])
        try:
            with connect(self.db_path) as db:
                cached = translate(db, self.paper, row, Model.from_env(), cached_only=True)
        except ValueError:
            cached = None
        self.translated.setPlainText(cached or "点击“翻译当前段”生成中文译文。")
        if self.pdf_loaded:
            self.pdf.pageNavigator().jump(row["page"] - 1, self.pdf.pageNavigator().currentLocation())

    def change_mode(self, mode):
        self.views.setCurrentIndex(int(mode == 3))
        self.original.setVisible(mode != 2)
        self.translated.setVisible(mode != 1)

    def start_job(self, operation, completed):
        if self.busy:
            return
        self.busy = True
        self.library.setEnabled(False)
        self.statusBar().showMessage("后台处理中…")
        self.job = Job(operation)
        self.job_done = completed
        self.job.signals.done.connect(self.finish_job)
        QThreadPool.globalInstance().start(self.job)

    @Slot(object)
    def finish_job(self, result):
        self.busy = False
        self.library.setEnabled(True)
        succeeded, data = result
        self.statusBar().showMessage("已完成" if succeeded else data, 10000)
        if succeeded:
            self.job_done(data)

    def prepare_paper(self):
        if self.paper is None:
            return
        paper = self.paper

        def operation():
            with connect(self.db_path) as db:
                return prepare(db, self.root, paper)

        self.start_job(operation, self.prepared)

    def prepared(self, _):
        self.flush_progress()
        self.started = monotonic()
        self.load_paper()

    def translate_current(self):
        index = self.paragraph_list.currentRow()
        if index < 0:
            return
        paper, row = self.paper, self.rows[index]

        def operation():
            with connect(self.db_path) as db:
                return translate(db, paper, row, Model.from_env())

        def completed(text):
            if self.paragraph_list.currentRow() == index:
                self.translated.setPlainText(text)

        self.start_job(operation, completed)

    def note(self):
        if self.paper is None:
            return
        text, accepted = QInputDialog.getMultiLineText(self, "论文笔记", "Markdown")
        if accepted and text.strip():
            index = self.paragraph_list.currentRow()
            anchor = self.rows[index]["anchor"] if index >= 0 else ""
            with connect(self.db_path) as db:
                add_note(db, self.paper, anchor, text)
            self.statusBar().showMessage("已保存笔记", 5000)

    def export(self):
        if self.paper is None:
            return
        name, _ = QFileDialog.getSaveFileName(self, "导出 Markdown", "notes.md", "Markdown (*.md)")
        if name:
            with connect(self.db_path) as db:
                Path(name).write_text(export_notes(db, self.paper), encoding="utf-8")
            self.statusBar().showMessage("已导出笔记", 5000)

    def closeEvent(self, event):
        self.flush_progress()
        super().closeEvent(event)


def run(db_path, paper_id, version, today):
    app = QApplication([])
    window = Reader(db_path, paper_id, version, today)
    window.show()
    return app.exec()

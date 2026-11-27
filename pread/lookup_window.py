from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QTextBrowser, QVBoxLayout,
)
from PySide6.QtGui import QDesktopServices

from .knowledge import answer
from .model import Model
from .reading import TRANSLATE_PROMPT
from .store import connect


class LookupWindow(QDialog):
    def __init__(self, reader):
        super().__init__(reader, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.reader = reader
        self.setWindowTitle("Pread · 论文查询")
        self.resize(620, 470)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("术语、问题或待翻译文本"))
        self.query = QLineEdit()
        self.query.returnPressed.connect(self.lookup)
        layout.addWidget(self.query)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.all_papers = QCheckBox("全库")
        controls.addWidget(self.all_papers)
        self.semantic = QCheckBox("语义检索")
        controls.addWidget(self.semantic)
        for label, callback in (("查找", self.lookup), ("带出处解释", self.explain), ("翻译文本", self.translate)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        self.result = QTextBrowser()
        layout.addWidget(self.result, 2)
        self.sources = QListWidget()
        self.sources.itemActivated.connect(self.open_source)
        layout.addWidget(self.sources, 1)

    def show_query(self, text=""):
        if text:
            self.query.setText(" ".join(text.split())[:6000])
        self.show()
        self.raise_()
        self.activateWindow()
        self.query.selectAll()
        self.query.setFocus()

    @Slot()
    def lookup(self):
        self.start("lookup")

    @Slot()
    def explain(self):
        self.start("explain")

    @Slot()
    def translate(self):
        self.start("translation")

    def start(self, mode):
        query = self.query.text().strip()
        if not query:
            return
        paper = None if self.all_papers.isChecked() else self.reader.paper
        db_path = self.reader.db_path
        model_name = self.reader.embedding_model if self.semantic.isChecked() else None

        def work():
            if mode == "translation":
                return {"answer": Model.from_env().complete(TRANSLATE_PROMPT, query), "sources": []}
            model = Model.from_env if mode == "explain" else None
            with connect(db_path) as db:
                return answer(db, query, paper, model=model, model_name=model_name)

        self.result.setPlainText("正在处理…")
        self.sources.clear()
        if not self.reader.start_job(work, self.show_answer, self.show_error):
            self.show_error("另一项后台任务正在运行，请稍后重试。")

    def show_error(self, message):
        self.result.setPlainText(message)

    def show_answer(self, result):
        fallback = "下方列出匹配的原文段落。" if result["sources"] else "本地证据不足。"
        self.result.setPlainText(result["answer"] or fallback)
        for source in result["sources"]:
            if "term" in source:
                label = f"{source['term']} · {source['source']}"
            else:
                label = f"{source['title']} · p. {source['page']} · {source['section']}\n{source['text'][:240]}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, source)
            self.sources.addItem(item)

    def open_source(self, item):
        source = item.data(Qt.ItemDataRole.UserRole)
        if source.get("anchor") and self.reader.locate(source):
            return
        url = QUrl(source.get("url", source.get("source", "")))
        if url.scheme() in {"http", "https"}:
            QDesktopServices.openUrl(url)

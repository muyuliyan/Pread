import json
from bisect import bisect_right
from pathlib import Path
from textwrap import shorten
from time import monotonic

from PySide6.QtCore import QObject, QPoint, QPointF, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QKeySequence, QPainter, QPen, QShortcut, QTextBlockFormat, QTextCursor,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QMainWindow, QMenu, QPushButton, QStackedWidget, QStyle, QSystemTrayIcon, QTabBar,
    QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from .hotkeys import DEFAULT_KEYS, GlobalHotkeys
from .lookup_window import LookupWindow
from .model import Model
from .reader_dialogs import ModelSettings, PaperPicker
from .reader_style import READER_STYLE
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


class ReaderCombo(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#53665c"), 1.4))
        x, y = self.width() - 13, self.height() // 2
        painter.drawLine(QPointF(x - 3.5, y - 1), QPointF(x, y + 2.5))
        painter.drawLine(QPointF(x, y + 2.5), QPointF(x + 3.5, y - 1))


class Reader(QMainWindow):
    def __init__(self, db_path, paper_id=None, version=None, today=None, config=None):
        super().__init__()
        self.db_path = db_path
        self.root = db_path.parent
        self.paper = None
        self.rows = []
        self.started = monotonic()
        self.timing = True
        self.elapsed = 0
        self.busy = False
        self.type_size = 14
        self.section_range = None
        self.anchor_positions = []
        self.following_scroll = False
        self.translation_ready = False
        self.session_model = None
        self.paper_index = -1
        config = config or {}
        self.embedding_model = config.get("embedding_model", "BAAI/bge-m3")
        self.lookup_window = LookupWindow(self)
        self.hotkeys = None
        self.setWindowTitle("Pread · 双语论文精读")
        self.resize(1280, 840)
        self.setMinimumSize(780, 530)
        self.setStyleSheet(READER_STYLE)
        self.build_layout()
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.show_clock)
        self.clock.start(1000)
        self.scroll_pause = QTimer(self)
        self.scroll_pause.setSingleShot(True)
        self.scroll_pause.setInterval(140)
        self.scroll_pause.timeout.connect(self.follow_scroll)
        self.original.verticalScrollBar().valueChanged.connect(lambda _: self.scroll_pause.start())
        self.outline.currentRowChanged.connect(self.jump_section)
        self.paragraph_list.currentRowChanged.connect(self.show_paragraph)
        self.modes.currentChanged.connect(self.change_mode)
        self.open_paper(self.populate_library(db_path, paper_id, version, today))
        self.setup_desktop_actions(config.get("hotkeys", {}))

    @staticmethod
    def label(text="", name=""):
        label = QLabel(text)
        label.setObjectName(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        return label

    @staticmethod
    def button(text, action, name="", tip=""):
        button = QPushButton(text)
        button.setObjectName(name)
        button.setToolTip(tip)
        button.clicked.connect(action)
        return button

    @staticmethod
    def rule():
        line = QFrame()
        line.setObjectName("rule")
        line.setFixedHeight(1)
        return line

    def build_layout(self):
        body = QWidget()
        body.setObjectName("reader")
        self.setCentralWidget(body)
        shell = QHBoxLayout(body)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.rail = QWidget()
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(242)
        shell.addWidget(self.rail)
        navigation = QVBoxLayout(self.rail)
        navigation.setContentsMargins(16, 19, 16, 19)
        navigation.setSpacing(12)
        navigation.addWidget(self.label("Pread", "brand"))
        navigation.addSpacing(6)
        navigation.addWidget(self.label("当前论文", "rail-label"))
        active = QWidget()
        active.setObjectName("active-paper")
        active_layout = QVBoxLayout(active)
        active_layout.setContentsMargins(12, 10, 12, 10)
        active_layout.setSpacing(6)
        self.active_title = self.label("", "active-title")
        self.active_title.setWordWrap(True)
        self.active_details = self.label("", "rail-label")
        active_layout.addWidget(self.active_title)
        active_layout.addWidget(self.active_details)
        navigation.addWidget(active)
        self.library = self.button("切换论文  ›", self.choose_paper, "paper-switch", "打开论文库（Ctrl+O）")
        navigation.addWidget(self.library)
        navigation.addSpacing(12)
        self.nav_tabs = QTabBar()
        self.nav_tabs.setObjectName("nav-tabs")
        self.nav_tabs.setExpanding(False)
        self.nav_tabs.setDrawBase(False)
        self.nav_tabs.addTab("章节")
        self.nav_tabs.addTab("段落")
        navigation.addWidget(self.nav_tabs)
        self.nav_pages = QStackedWidget()
        self.outline = QListWidget()
        self.outline.setObjectName("outline")
        self.outline.setAccessibleName("章节")
        self.paragraph_list = QListWidget()
        self.paragraph_list.setObjectName("outline")
        self.paragraph_list.setAccessibleName("段落")
        self.nav_pages.addWidget(self.outline)
        self.nav_pages.addWidget(self.paragraph_list)
        self.nav_tabs.currentChanged.connect(self.nav_pages.setCurrentIndex)
        navigation.addWidget(self.nav_pages, 1)
        self.rail_hint = self.label("", "rail-hint")
        self.rail_hint.setWordWrap(True)
        navigation.addWidget(self.rail_hint)

        workspace = QWidget()
        shell.addWidget(workspace, 1)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("topbar")
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(18, 9, 18, 9)
        toolbar.setSpacing(8)
        bar.setLayout(toolbar)
        layout.addWidget(bar)
        layout.addWidget(self.rule())
        self.rail_toggle = self.button("目录", self.toggle_rail, tip="显示或隐藏目录")
        self.rail_toggle.setCheckable(True)
        self.rail_toggle.setChecked(True)
        toolbar.addWidget(self.rail_toggle)
        self.picker_button = self.button("论文", self.choose_paper, tip="打开论文库（Ctrl+O）")
        toolbar.addWidget(self.picker_button)
        self.modes = QTabBar()
        self.modes.setAccessibleName("阅读模式")
        self.modes.setExpanding(False)
        self.modes.setDrawBase(False)
        for mode in ("中英对照", "英文原文", "中文译文", "PDF"):
            self.modes.addTab(mode)
        self.modes.setCurrentIndex(1)
        toolbar.addWidget(self.modes)
        toolbar.addStretch()
        toolbar.addWidget(self.button("A−", lambda: self.resize_type(-1), tip="缩小正文"))
        toolbar.addWidget(self.button("A+", lambda: self.resize_type(1), tip="放大正文"))
        toolbar.addWidget(self.button("查询", self.open_lookup))
        self.model_button = self.button("模型", self.configure_model, tip="配置翻译和问答使用的模型")
        toolbar.addWidget(self.model_button)
        self.notes_button = QToolButton()
        self.notes_button.setText("笔记")
        self.notes_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.notes_button)
        menu.addAction("添加笔记", self.note)
        menu.addAction("导出 Markdown", self.export)
        self.notes_button.setMenu(menu)
        toolbar.addWidget(self.notes_button)

        self.hero = QWidget()
        intro = QVBoxLayout(self.hero)
        intro.setContentsMargins(30, 20, 30, 13)
        intro.setSpacing(7)
        facts = QHBoxLayout()
        self.metadata = self.label("", "metadata")
        self.state = self.label("", "document-state")
        self.prepare_button = self.button("准备 PDF", self.prepare_paper, "primary", "下载并解析此版本的 PDF")
        facts.addWidget(self.metadata)
        facts.addStretch()
        facts.addWidget(self.state)
        facts.addWidget(self.prepare_button)
        intro.addLayout(facts)
        self.title = self.label("", "title")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        intro.addWidget(self.title)
        self.authors = self.label("", "authors")
        self.authors.setWordWrap(True)
        intro.addWidget(self.authors)
        layout.addWidget(self.hero)

        self.views = QStackedWidget()
        layout.addWidget(self.views, 1)
        self.text_view = QWidget()
        pages = QHBoxLayout(self.text_view)
        pages.setContentsMargins(28, 3, 28, 16)
        pages.setSpacing(12)
        self.original = self.text_pane()
        self.translated = self.text_pane()
        self.source_card, self.source_heading, _ = self.reading_card("英文原文", self.original)
        self.translation_card, _, translation_header = self.reading_card("中文译文", self.translated)
        self.translate_button = self.button("翻译当前段", self.translate_current, "link")
        translation_header.addWidget(self.translate_button)
        pages.addStretch()
        pages.addWidget(self.source_card, 20)
        pages.addWidget(self.translation_card, 20)
        pages.addStretch()
        self.views.addWidget(self.text_view)
        self.document = QPdfDocument(self)
        self.pdf = QPdfView()
        self.pdf.setDocument(self.document)
        self.pdf.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.views.addWidget(self.pdf)
        self.pdf_empty = QWidget()
        pdf_placeholder = QVBoxLayout(self.pdf_empty)
        pdf_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pdf_message = self.label("", "pane-heading")
        self.pdf_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pdf_placeholder.addWidget(self.pdf_message)
        self.external_pdf = self.button("在默认应用中打开", self.open_pdf_external, "primary")
        pdf_placeholder.addWidget(self.external_pdf, alignment=Qt.AlignmentFlag.AlignHCenter)
        pdf_placeholder.addWidget(self.button("查看 arXiv 页面", self.open_source_page, "link"),
                                  alignment=Qt.AlignmentFlag.AlignHCenter)
        self.views.addWidget(self.pdf_empty)

        layout.addWidget(self.rule())
        foot = QWidget()
        foot.setObjectName("footer")
        footer = QHBoxLayout(foot)
        footer.setContentsMargins(18, 7, 18, 7)
        footer.setSpacing(7)
        layout.addWidget(foot)
        self.previous_button = self.button("‹", lambda: self.move_paragraph(-1), tip="上一段（Alt+左方向键）")
        self.next_button = self.button("›", lambda: self.move_paragraph(1), tip="下一段（Alt+右方向键）")
        self.previous_button.setAccessibleName("上一段")
        self.next_button.setAccessibleName("下一段")
        self.location = self.label("", "location")
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        footer.addWidget(self.location)
        footer.addStretch()
        self.detail = self.label("", "metadata")
        footer.addWidget(self.detail)
        self.budget = ReaderCombo()
        self.budget.addItem("3 小时预算", 180)
        self.budget.addItem("4 小时预算", 240)
        self.budget.currentIndexChanged.connect(self.show_clock)
        footer.addWidget(self.budget)
        self.timer_button = self.button("暂停", self.toggle_timer, tip="暂停阅读计时")
        footer.addWidget(self.timer_button)
        for key, action in (("Alt+Left", lambda: self.move_paragraph(-1)),
                            ("Alt+Right", lambda: self.move_paragraph(1)),
                            ("Ctrl+O", self.choose_paper)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)

    def reading_card(self, title, pane):
        card = QFrame()
        card.setObjectName("paper-card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 12, 24, 17)
        layout.setSpacing(12)
        header = QHBoxLayout()
        label = self.label(title, "pane-heading")
        label.setMinimumHeight(32)
        header.addWidget(label, 1)
        layout.addLayout(header)
        layout.addWidget(self.rule())
        layout.addWidget(pane, 1)
        return card, label, header

    def populate_library(self, db_path, paper_id, version, today):
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
        self.papers = ordered
        if chosen is not None:
            return next(index for index, (paper, _) in enumerate(ordered) if paper == chosen)
        return 0 if ordered else -1

    def choose_paper(self):
        if self.busy:
            return
        dialog = PaperPicker(self.papers, self.paper_index, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected != self.paper_index:
            self.open_paper(dialog.selected)
        dialog.deleteLater()

    def current_model(self):
        if self.session_model is not None:
            return self.session_model
        with connect(self.db_path) as db:
            return Model.configured(db)

    def configure_model(self):
        if self.busy:
            return
        try:
            configured = self.current_model()
        except ValueError:
            configured = None
        dialog = ModelSettings(self, configured)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            with connect(self.db_path) as db:
                dialog.model.save_connection(db)
            self.session_model = dialog.model
            if self.modes.currentIndex() in (0, 2):
                self.refresh_translation(self.current_passage())
            self.statusBar().showMessage(f"已保存模型 · {self.session_model.name}", 5000)

    def setup_desktop_actions(self, keys):
        app = QApplication.instance()
        actions = {"reader": self.toggle_window, "lookup": self.open_lookup,
                   "translate": self.translate_selection, "explain": self.explain_selection, "note": self.note}
        self.hotkeys = GlobalHotkeys(app, self, actions, keys)
        for number, (name, action) in enumerate(actions.items(), 1):
            key = keys.get(name, DEFAULT_KEYS[name])
            if key and number not in self.hotkeys.registered and name not in self.hotkeys.failed:
                shortcut = QShortcut(QKeySequence(f"Ctrl+Shift+{key}"), self)
                shortcut.activated.connect(action)
        if self.hotkeys.failed:
            self.statusBar().showMessage(f"快捷键不可用：{', '.join(self.hotkeys.failed)}", 10000)
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
            self.tray = QSystemTrayIcon(icon, self)
            menu = QMenu(self)
            menu.addAction("显示或隐藏阅读器", self.toggle_window)
            menu.addAction("查询", self.open_lookup)
            menu.addAction("退出", self.close)
            self.tray.setContextMenu(menu)
            self.tray.show()

    def toggle_window(self):
        if self.isVisible() and not self.isMinimized():
            self.flush_progress()
            self.hidden_at = monotonic()
            if self.tray is not None or self.hotkeys.registered:
                self.hide()
            else:
                self.showMinimized()
        else:
            if hasattr(self, "hidden_at"):
                self.started += monotonic() - self.hidden_at
                del self.hidden_at
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def selected_text(self):
        for pane in (self.original, self.translated):
            if pane.hasFocus() and pane.textCursor().hasSelection():
                return pane.textCursor().selectedText()
        return ""

    def open_lookup(self):
        self.lookup_window.show_query(self.selected_text())

    def translate_selection(self):
        text = self.selected_text()
        self.lookup_window.show_query(text)
        if text:
            self.lookup_window.translate()

    def explain_selection(self):
        text = self.selected_text()
        self.lookup_window.show_query(text)
        if text:
            self.lookup_window.explain()

    def locate(self, source):
        for index, (paper, _) in enumerate(self.papers):
            if (paper.id, paper.version) == (source["id"], source["version"]):
                if index != self.paper_index:
                    self.open_paper(index)
                for row, paragraph in enumerate(self.rows):
                    if paragraph["anchor"] == source["anchor"]:
                        self.paragraph_list.setCurrentRow(row)
                        return True
                break
        return False

    @staticmethod
    def text_pane():
        pane = QTextEdit()
        pane.setReadOnly(True)
        pane.setObjectName("prose")
        pane.setFrameShape(QFrame.Shape.NoFrame)
        pane.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pane.document().setDocumentMargin(1)
        return pane

    def toggle_rail(self, checked):
        self.rail.setVisible(checked)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "rail_toggle") and event.size().width() < 900 and event.oldSize().width() >= 900:
            self.rail_toggle.setChecked(False)
            self.toggle_rail(False)

    def format_prose(self, pane, texts, *, original=False, hint=False):
        pane.clear()
        font = QFont()
        font.setFamilies(["Georgia", "Cambria", "DejaVu Serif"] if original else
                         ["Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI"])
        font.setPointSize(11 if hint else self.type_size)
        pane.document().setDefaultFont(font)
        format_ = QTextBlockFormat()
        format_.setLineHeight(158, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        format_.setBottomMargin(15)
        cursor = QTextCursor(pane.document())
        positions = []
        for index, text in enumerate(texts):
            if index:
                cursor.insertBlock(format_)
            else:
                cursor.setBlockFormat(format_)
            positions.append(cursor.position())
            cursor.insertText(text)
        reset = QTextCursor(pane.document())
        reset.movePosition(QTextCursor.MoveOperation.Start)
        pane.setTextCursor(reset)
        pane.verticalScrollBar().setValue(0)
        return positions

    def resize_type(self, delta):
        size = min(24, max(10, self.type_size + delta))
        if size != self.type_size:
            self.type_size = size
            self.section_range = None
            self.refresh_reading()

    def show_clock(self):
        if self.paper is None:
            self.detail.clear()
            return
        seconds = self.elapsed + self.current_seconds()
        self.detail.setText(f"已读 {seconds // 60} 分钟 · 剩余 {max(0, self.budget.currentData() - seconds // 60)} 分钟")

    def current_seconds(self):
        return int(monotonic() - self.started) if self.timing and not hasattr(self, "hidden_at") else 0

    def toggle_timer(self):
        self.flush_progress()
        self.timing = not self.timing
        self.timer_button.setText("暂停" if self.timing else "继续")
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
        self.paper_index = index
        self.paper = self.papers[index][0] if index >= 0 else None
        self.started = monotonic()
        self.load_paper()

    def load_paper(self):
        self.scroll_pause.stop()
        self.rows = []
        self.section_rows = []
        self.section_names = []
        self.section_range = None
        self.anchor_positions = []
        self.local_pdf = None
        self.pdf_loaded = False
        self.document.close()
        self.outline.blockSignals(True)
        self.paragraph_list.blockSignals(True)
        self.outline.clear()
        self.paragraph_list.clear()
        saved = previous = None
        if self.paper is not None:
            with connect(self.db_path) as db:
                saved = get_document(db, self.paper)
                self.rows = paragraphs(db, self.paper)
                previous = progress(db, self.paper)
        self.elapsed = previous["seconds"] if previous else 0
        if saved:
            path = pdf_path(self.root, self.paper, saved["sha256"])
            if path.is_file():
                self.local_pdf = path.resolve()
                self.pdf_loaded = self.document.load(str(self.local_pdf)) == QPdfDocument.Error.None_
        prior_section = None
        selected = 0
        page_navigation = len({row["section"] for row in self.rows}) == 1 and len({row["page"] for row in self.rows}) > 1
        self.nav_tabs.setTabText(0, "页码" if page_navigation else "章节")
        self.outline.setAccessibleName(self.nav_tabs.tabText(0))
        for index, row in enumerate(self.rows):
            group = row["page"] if page_navigation else row["section"]
            if group != prior_section:
                name = f"第 {group} 页" if page_navigation else self.section_label(group)
                self.outline.addItem(name)
                self.section_rows.append(index)
                self.section_names.append(name)
                prior_section = group
            self.paragraph_list.addItem(f"第 {row['page']} 页 · {row['text'][:100]}")
            self.paragraph_list.item(index).setToolTip(row["text"][:200])
            if previous and row["anchor"] == previous["anchor"]:
                selected = index
        if not self.rows:
            self.nav_tabs.setCurrentIndex(0)
            if self.paper is not None:
                self.outline.addItem("摘要")
                self.outline.setCurrentRow(0)
        self.outline.blockSignals(False)
        self.paragraph_list.blockSignals(False)
        self.nav_tabs.setVisible(bool(self.rows))
        self.rail_hint.setVisible(self.paper is not None and not self.rows)
        self.rail_hint.setText("准备 PDF 后可按章节和段落跳转。")
        self.refresh_paper_header()
        self.update_view()
        if self.rows:
            self.paragraph_list.setCurrentRow(selected)
        else:
            self.refresh_reading()
        self.show_clock()

    @staticmethod
    def section_label(section):
        return {
            "Front matter": "正文开篇", "Abstract": "摘要", "Introduction": "引言",
            "Background": "背景", "Related Work": "相关工作", "Method": "方法",
            "Methods": "方法", "Experiments": "实验", "Results": "结果",
            "Conclusion": "结论", "References": "参考文献", "Appendix": "附录",
        }.get(section, section)

    def refresh_paper_header(self):
        self.active_title.setText(shorten(self.paper.title, width=76, placeholder="…") if self.paper else "暂无论文")
        self.active_title.setToolTip(self.paper.title if self.paper else "")
        self.active_details.setText(f"arXiv:{self.paper.id}v{self.paper.version}" if self.paper else "")
        self.prepare_button.setVisible(self.paper is not None and self.local_pdf is None)
        self.prepare_button.setText("准备 PDF")
        self.prepare_button.setToolTip("下载并解析此版本的 PDF")
        self.notes_button.setEnabled(self.paper is not None)
        self.budget.setEnabled(self.paper is not None)
        self.timer_button.setEnabled(self.paper is not None)
        self.modes.setEnabled(self.paper is not None)
        if self.paper is None:
            self.metadata.clear()
            self.title.setText("论文库为空")
            self.authors.clear()
            self.state.clear()
            return
        paper = self.paper
        ranking = self.papers[self.paper_index][1]
        self.metadata.setText("  ·  ".join(part for part in
                              (ranking, f"arXiv:{paper.id}v{paper.version}", paper.published) if part))
        self.title.setText(paper.title)
        authors = "、".join(paper.authors[:4])
        if len(paper.authors) > 4:
            authors += f" 等 {len(paper.authors)} 人"
        self.authors.setText(authors)
        self.authors.setToolTip("、".join(paper.authors))
        self.state.setText(f"{len(self.rows)} 个段落" if self.rows else ("PDF 已下载" if self.local_pdf else "摘要预览"))

    def jump_section(self, index):
        if 0 <= index < len(self.section_rows):
            self.paragraph_list.setCurrentRow(self.section_rows[index])

    def move_paragraph(self, direction):
        index = self.paragraph_list.currentRow() + direction
        if 0 <= index < len(self.rows):
            self.paragraph_list.setCurrentRow(index)

    def show_paragraph(self, index):
        if not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        segment = bisect_right(self.section_rows, index) - 1
        self.outline.blockSignals(True)
        self.outline.setCurrentRow(segment)
        self.outline.blockSignals(False)
        self.refresh_reading()
        if self.pdf_loaded:
            self.pdf.pageNavigator().jump(row["page"] - 1, QPointF(0, 0))

    def current_passage(self):
        index = self.paragraph_list.currentRow()
        if 0 <= index < len(self.rows):
            return self.rows[index]
        if self.paper is not None and self.paper.abstract.strip():
            return {"anchor": "abstract", "text": self.paper.abstract}
        return None

    def refresh_reading(self):
        index = self.paragraph_list.currentRow()
        mode = self.modes.currentIndex()
        row = self.current_passage()
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(0 <= index < len(self.rows) - 1)
        self.location.setVisible(self.paper is not None)
        if index < 0:
            self.section_range = None
            self.source_heading.setText("摘要" if self.paper else "开始阅读")
            self.location.setText("摘要")
            source = row["text"] if row else (
                "先导入论文快照，或运行 research collect 采集论文。" if not self.paper else "这篇论文没有摘要。"
            )
            self.format_prose(self.original, [source], original=True)
        else:
            section = bisect_right(self.section_rows, index) - 1
            start = self.section_rows[section]
            end = self.section_rows[section + 1] if section + 1 < len(self.section_rows) else len(self.rows)
            name = self.section_names[section]
            self.source_heading.setText(name)
            self.location.setText(f"第 {row['page']} 页　·　段落 {index + 1} / {len(self.rows)}")
            if mode == 1:
                if self.section_range != (start, end):
                    self.section_range = (start, end)
                    self.anchor_positions = self.format_prose(
                        self.original, [item["text"] for item in self.rows[start:end]], original=True
                    )
                if not self.following_scroll:
                    cursor = QTextCursor(self.original.document())
                    cursor.setPosition(self.anchor_positions[index - start])
                    scrollbar = self.original.verticalScrollBar()
                    offset = self.original.cursorRect(cursor).top()
                    scrollbar.setValue(scrollbar.value() + offset)
                    self.scroll_pause.stop()
            elif mode == 0:
                self.section_range = None
                self.format_prose(self.original, [row["text"]], original=True)
        if mode in (0, 2):
            self.refresh_translation(row)

    def follow_scroll(self):
        if self.modes.currentIndex() != 1 or not self.section_range or not self.original.isVisible():
            return
        position = self.original.cursorForPosition(QPoint(4, 28)).position()
        index = self.section_range[0] + max(0, bisect_right(self.anchor_positions, position) - 1)
        self.following_scroll = True
        try:
            self.paragraph_list.setCurrentRow(index)
        finally:
            self.following_scroll = False

    def refresh_translation(self, row):
        self.translate_button.setText("翻译摘要" if row and row["anchor"] == "abstract" else "翻译当前段")
        self.translation_ready = False
        hint = "点击上方按钮生成译文。"
        cached = None
        try:
            if row:
                with connect(self.db_path) as db:
                    model = self.session_model or Model.configured(db)
                    self.translation_ready = True
                    cached = translate(db, self.paper, row, model, cached_only=True)
        except ValueError:
            hint = "点击顶部「模型」，填写服务地址和模型 ID，即可翻译。"
        self.translate_button.setEnabled(self.translation_ready and not self.busy)
        self.format_prose(self.translated, [cached or (hint if row else "暂无可翻译内容。")], hint=not cached)

    def change_mode(self, _):
        self.section_range = None
        self.update_view()
        self.refresh_reading()

    def update_view(self):
        mode = self.modes.currentIndex() if self.paper else 1
        if mode == 3:
            self.pdf_message.setText("内置阅读器无法打开此 PDF" if self.local_pdf else "先准备 PDF，再查看原版")
            self.external_pdf.setVisible(self.local_pdf is not None)
            self.views.setCurrentWidget(self.pdf if self.pdf_loaded else self.pdf_empty)
        else:
            self.views.setCurrentWidget(self.text_view)
            self.source_card.setVisible(mode != 2)
            self.translation_card.setVisible(mode != 1)
            self.source_card.setMaximumWidth(810 if mode == 1 else 16777215)
            self.translation_card.setMaximumWidth(810 if mode == 2 else 16777215)

    def open_pdf_external(self):
        if self.local_pdf:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.local_pdf)))

    def open_source_page(self):
        if self.paper:
            QDesktopServices.openUrl(QUrl(self.paper.url))

    def start_job(self, operation, completed, failed=None):
        if self.busy:
            return False
        self.busy = True
        self.library.setEnabled(False)
        self.picker_button.setEnabled(False)
        self.model_button.setEnabled(False)
        self.prepare_button.setEnabled(False)
        self.translate_button.setEnabled(False)
        self.statusBar().showMessage("后台处理中…")
        self.job = Job(operation)
        self.job_done = completed
        self.job_failed = failed
        self.job.signals.done.connect(self.finish_job)
        QThreadPool.globalInstance().start(self.job)
        return True

    @Slot(object)
    def finish_job(self, result):
        self.busy = False
        self.library.setEnabled(True)
        self.picker_button.setEnabled(True)
        self.model_button.setEnabled(True)
        self.prepare_button.setEnabled(True)
        self.translate_button.setEnabled(self.translation_ready and self.current_passage() is not None)
        succeeded, data = result
        self.statusBar().showMessage("已完成" if succeeded else data, 10000)
        if succeeded:
            self.job_done(data)
        elif self.job_failed:
            self.job_failed(data)

    def prepare_paper(self):
        if self.paper is None or self.busy:
            return
        paper = self.paper

        def operation():
            with connect(self.db_path) as db:
                return prepare(db, self.root, paper)

        self.prepare_button.setText("正在准备…")
        self.start_job(operation, self.prepared, self.prepare_failed)

    def prepared(self, _):
        self.prepare_button.setText("准备 PDF")
        self.flush_progress()
        self.started = monotonic()
        self.load_paper()

    def prepare_failed(self, message):
        self.prepare_button.setText("重试准备 PDF")
        self.prepare_button.setToolTip(message)

    def translate_current(self):
        row = self.current_passage()
        if row is None or self.busy:
            return
        paper = self.paper

        def operation():
            with connect(self.db_path) as db:
                return translate(db, paper, row, self.session_model or Model.configured(db))

        def completed(text):
            current = self.current_passage()
            if current and current["anchor"] == row["anchor"]:
                self.format_prose(self.translated, [text])

        def failed(message):
            current = self.current_passage()
            if current and current["anchor"] == row["anchor"]:
                self.format_prose(self.translated, [message])

        self.start_job(operation, completed, failed)

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
        if self.hotkeys:
            self.hotkeys.close()
        if self.tray:
            self.tray.hide()
        self.document.close()
        self.flush_progress()
        super().closeEvent(event)


def run(db_path, paper_id, version, today, config):
    app = QApplication([])
    window = Reader(db_path, paper_id, version, today, config)
    window.show()
    return app.exec()

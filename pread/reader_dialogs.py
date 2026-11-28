from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QStyle, QStyledItemDelegate, QVBoxLayout,
)

from .model import Model

TITLE = Qt.ItemDataRole.UserRole + 1
DETAILS = Qt.ItemDataRole.UserRole + 2
WRAP = Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft


class PaperRows(QStyledItemDelegate):
    @staticmethod
    def font(option):
        font = QFont(option.font)
        font.setPointSize(11)
        font.setWeight(font.Weight.DemiBold)
        return font

    def sizeHint(self, option, index):
        width = max(240, self.parent().viewport().width() - 32)
        metrics = QFontMetrics(self.font(option))
        title = metrics.boundingRect(QRect(0, 0, width, 800), WRAP, index.data(TITLE))
        return QSize(0, title.height() + 42)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        selected = option.state & QStyle.StateFlag.State_Selected
        hovered = option.state & QStyle.StateFlag.State_MouseOver
        rect = option.rect.adjusted(1, 1, -1, -1)
        painter.setPen(QColor("#78a687" if selected else "#dce5da"))
        painter.setBrush(QColor("#e8f2e7" if selected else "#f4f7ef" if hovered else "#fffefa"))
        painter.drawRoundedRect(rect, 8, 8)
        title_rect = rect.adjusted(14, 9, -14, -28)
        painter.setFont(self.font(option))
        painter.setPen(QColor("#20392e"))
        painter.drawText(title_rect, WRAP, index.data(TITLE))
        detail_font = QFont(option.font)
        detail_font.setPointSizeF(9.0)
        painter.setFont(detail_font)
        painter.setPen(QColor("#576d61"))
        metadata = QFontMetrics(detail_font).elidedText(index.data(DETAILS), Qt.TextElideMode.ElideRight,
                                                       title_rect.width())
        painter.drawText(rect.adjusted(14, rect.height() - 24, -14, -5),
                         Qt.AlignmentFlag.AlignVCenter, metadata)
        painter.restore()


class PaperPicker(QDialog):
    def __init__(self, papers, selected, parent):
        super().__init__(parent)
        self.papers = papers
        self.selected = selected
        self.setWindowTitle("Pread · 论文库")
        self.resize(690, 510)
        self.setMinimumSize(480, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        headline = QLabel("选择论文")
        headline.setObjectName("dialog-title")
        layout.addWidget(headline)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题、作者或 arXiv ID")
        self.search.setAccessibleName("搜索论文")
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.setObjectName("paper-results")
        self.results.setAccessibleName("论文搜索结果")
        self.results.setMouseTracking(True)
        self.results.setSpacing(4)
        self.results.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setItemDelegate(PaperRows(self.results))
        layout.addWidget(self.results, 1)
        footer = QHBoxLayout()
        self.count = QLabel()
        self.count.setObjectName("metadata")
        footer.addWidget(self.count, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.choose = buttons.addButton("打开论文", QDialogButtonBox.ButtonRole.AcceptRole)
        self.choose.setObjectName("primary")
        self.choose.setDefault(True)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        buttons.accepted.connect(self.accept_paper)
        buttons.rejected.connect(self.reject)
        self.results.itemActivated.connect(self.accept_paper)
        self.search.textChanged.connect(self.filter)
        self.filter("")
        self.search.setFocus()

    def filter(self, query):
        self.results.clear()
        terms = query.casefold().split()
        matches = []
        for index, (paper, badge) in enumerate(self.papers):
            searchable = f"{paper.id} {paper.title} {' '.join(paper.authors)}".casefold()
            if all(term in searchable for term in terms):
                matches.append((index, paper, badge))
        for index, paper, badge in matches:
            authors = "、".join(paper.authors[:2])
            metadata = "  ·  ".join(part for part in
                                    (badge, f"arXiv:{paper.id}v{paper.version}", paper.published, authors) if part)
            item = QListWidgetItem(paper.title)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setData(TITLE, paper.title)
            item.setData(DETAILS, metadata)
            self.results.addItem(item)
            if index == self.selected:
                self.results.setCurrentItem(item)
        if matches and self.results.currentRow() < 0:
            self.results.setCurrentRow(0)
        self.choose.setEnabled(bool(matches))
        self.count.setText(f"{len(matches)} 篇论文" if matches else "没有匹配的论文")

    def accept_paper(self, _=None):
        if item := self.results.currentItem():
            self.selected = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class ModelSettings(QDialog):
    def __init__(self, reader, configured):
        super().__init__(reader)
        self.reader = reader
        self.model = None
        self.setWindowTitle("Pread · 翻译模型")
        self.setMinimumWidth(550)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        headline = QLabel("翻译模型")
        headline.setObjectName("dialog-title")
        layout.addWidget(headline)
        form = QFormLayout()
        self.endpoint = QLineEdit(configured.endpoint if configured else "")
        self.endpoint.setPlaceholderText("https://你的服务地址/v1")
        self.name = QLineEdit(configured.name if configured else "")
        self.name.setPlaceholderText("服务商提供的模型 ID")
        self.key = QLineEdit(reader.session_model.key if reader.session_model else "")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("可选，仅在本次运行中使用")
        for label, field in (("API 地址", self.endpoint), ("模型 ID", self.name), ("API Key", self.key)):
            form.addRow(label, field)
        layout.addLayout(form)
        self.status = QLabel("地址和模型 ID 保存到当前论文库。测试连接只发送一条测试消息。")
        self.status.setWordWrap(True)
        self.status.setObjectName("metadata")
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save.setObjectName("primary")
        self.test = buttons.addButton("测试连接", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self.save_values)
        buttons.rejected.connect(self.reject)
        self.test.clicked.connect(self.test_connection)
        layout.addWidget(buttons)
        self.endpoint.textChanged.connect(self.endpoint_changed)

    def endpoint_changed(self):
        self.key.clear()

    def values(self):
        try:
            return Model.from_values(self.endpoint.text(), self.name.text(), self.key.text())
        except ValueError as error:
            self.status.setText(str(error))
            self.status.setTextFormat(Qt.TextFormat.PlainText)
            return None

    def save_values(self):
        if model := self.values():
            self.model = model
            self.accept()

    def test_connection(self):
        if not (model := self.values()):
            return
        self.status.setText("正在测试连接…")
        self.test.setEnabled(False)
        self.save.setEnabled(False)

        def complete(message):
            self.status.setTextFormat(Qt.TextFormat.PlainText)
            self.status.setText(message)
            self.test.setEnabled(True)
            self.save.setEnabled(True)

        if not self.reader.start_job(lambda: model.complete("Reply with OK.", "Connection test."),
                                     lambda _: complete("连接成功，模型已返回响应。"), complete):
            complete("另一项后台任务正在运行，请稍后再试。")

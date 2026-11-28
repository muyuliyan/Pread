READER_STYLE = """
QWidget { font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif; font-size: 13px; color: #202e29; }
QMainWindow, QWidget#reader { background: #f6f6f0; }
QLabel { background: transparent; }
QWidget#rail { background: #192d26; }
QLabel#brand { color: #f4f5ed; font-size: 27px; font-weight: 700; }
QLabel#rail-label { color: #a5beb2; font-size: 12px; font-weight: 600; }
QLabel#rail-hint { color: #a5beb2; }
QComboBox#library { background: #263e34; border: 1px solid #41594d; color: #f6f7f1; }
QComboBox#library:hover { border-color: #81a995; }
QComboBox QAbstractItemView { background: #fffefa; color: #202e29; padding: 4px; }
QListWidget#outline { background: transparent; border: 0; color: #e1eae0; outline: 0; }
QListWidget#outline::item { padding: 9px 10px; border-radius: 6px; }
QListWidget#outline::item:hover { background: #283e35; }
QListWidget#outline::item:selected { background: #35594a; color: #ffffff; }
QWidget#topbar, QWidget#footer { background: #fcfcf7; }
QLabel#title { color: #162922; font-size: 30px; font-weight: 700; }
QLabel#metadata, QLabel#location { color: #53665c; }
QLabel#authors { color: #465b50; }
QLabel#pane-heading { color: #263d32; font-size: 14px; font-weight: 700; }
QLabel#document-state { color: #33604b; font-weight: 600; }
QFrame#rule { background: #e1e6dc; border: 0; max-height: 1px; }
QFrame#paper-card { background: #fffefa; border: 1px solid #dfe5da; border-radius: 10px; }
QTextEdit#prose { background: transparent; border: 0; selection-background-color: #d7ebdd;
  selection-color: #182e24; }
QPushButton, QToolButton, QComboBox { background: #fffefa; border: 1px solid #d2dbd1;
  border-radius: 6px; padding: 6px 11px; min-height: 19px; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { border: 0; width: 23px; }
QComboBox::down-arrow { image: none; }
QPushButton:hover, QToolButton:hover, QComboBox:hover { background: #eef4eb; border-color: #9eb6a7; }
QPushButton:checked { background: #e0eee2; border-color: #b6d2be; color: #194c36; }
QPushButton:focus, QToolButton:focus, QComboBox:focus, QTabBar::tab:focus { border: 1px solid #398465; }
QPushButton:disabled, QToolButton:disabled { background: #f0f2ec; border-color: #dfe4db; color: #78867e; }
QPushButton#primary { background: #205a44; border-color: #205a44; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #174b37; }
QPushButton#primary:disabled { background: #e3ede4; border-color: #d7e4d9; color: #546e60; }
QPushButton#link { background: transparent; border-color: transparent; color: #245b45; }
QPushButton#link:hover { background: #e9f2e8; }
QTabBar::tab { background: #f0f2ea; color: #43564b; border: 1px solid #e0e6db;
  padding: 7px 12px; min-height: 18px; }
QTabBar::tab:first { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QTabBar::tab:last { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QTabBar::tab:selected { background: #e0eee2; color: #194c36; border-color: #b6d2be; }
QTabBar::tab:disabled { color: #879189; }
QTabBar::tab:hover:!selected:enabled { background: #e9efe6; }
QTabBar#nav-tabs { background: transparent; }
QTabBar#nav-tabs::tab { background: #263e34; color: #dbe9df; border-color: #41594d; }
QTabBar#nav-tabs::tab:selected { background: #35594a; color: #f7fbf6; border-color: #537963; }
QTabBar#nav-tabs::tab:hover:!selected:enabled { background: #304b3d; }
QScrollBar:vertical { width: 9px; background: transparent; margin: 2px 1px; }
QScrollBar::handle:vertical { background: #9eb4a4; border-radius: 3px; min-height: 25px; }
QScrollBar::handle:vertical:hover { background: #678e78; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QStatusBar { color: #52685b; background: #fcfcf7; }
QStatusBar::item { border: 0; }
QDialog { background: #f6f6f0; }
QLineEdit, QTextBrowser, QListWidget { background: #fffefa; border: 1px solid #dfe5da;
  border-radius: 6px; padding: 6px; selection-background-color: #dcece0; selection-color: #193c2b; }
"""

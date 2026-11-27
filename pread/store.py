from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sqlite3
from datetime import date

ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})")


@dataclass(frozen=True)
class Paper:
    id: str
    version: int
    title: str
    abstract: str
    published: str
    updated: str
    authors: list[str]
    categories: list[str]

    def __post_init__(self):
        if not isinstance(self.id, str) or not ARXIV_ID.fullmatch(self.id):
            raise ValueError(f"Invalid arXiv ID: {self.id}")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("arXiv version must be a positive integer")
        if not all(isinstance(value, str) for value in (self.title, self.abstract, self.published, self.updated)):
            raise ValueError("Paper text fields must be strings")
        if not self.title.strip():
            raise ValueError("A paper needs a title")
        for names in (self.authors, self.categories):
            if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                raise ValueError("Authors and categories must be lists of strings")
        if date.fromisoformat(self.updated) < date.fromisoformat(self.published):
            raise ValueError("Update date precedes submission date")

    @property
    def url(self):
        return f"https://arxiv.org/abs/{self.id}v{self.version}"

    @property
    def pdf_url(self):
        return f"https://arxiv.org/pdf/{self.id}v{self.version}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_version (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    published TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE IF NOT EXISTS paper_state (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('unread', 'read', 'skipped'))
);
CREATE TABLE IF NOT EXISTS recommendation (
    week TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES paper_version(id, version)
);
CREATE TABLE IF NOT EXISTS paragraph (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    anchor TEXT NOT NULL,
    page INTEGER NOT NULL,
    section TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (id, version, anchor),
    FOREIGN KEY (id, version) REFERENCES document(id, version)
);
CREATE TABLE IF NOT EXISTS translation (
    cache_key TEXT PRIMARY KEY,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reading_progress (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    anchor TEXT NOT NULL,
    seconds INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES paper_version(id, version)
);
CREATE TABLE IF NOT EXISTS note (
    note_id INTEGER PRIMARY KEY,
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    anchor TEXT NOT NULL,
    text TEXT NOT NULL,
    FOREIGN KEY (id, version) REFERENCES paper_version(id, version)
);
"""


@contextmanager
def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(SCHEMA)
        with db:
            yield db


def save_papers(db, papers):
    db.executemany(
        """INSERT INTO paper_version VALUES (?, ?, ?, ?)
           ON CONFLICT(id, version) DO UPDATE
           SET published = excluded.published, payload = excluded.payload
           WHERE json_extract(excluded.payload, '$.updated') >= json_extract(payload, '$.updated')""",
        ((paper.id, paper.version, paper.published, json.dumps(asdict(paper))) for paper in papers),
    )


def latest_papers(db, since="0001-01-01", *, eligible=True):
    rows = db.execute(
        """SELECT p.payload FROM paper_version p
           LEFT JOIN paper_state s ON s.id = p.id
           WHERE p.published >= ?
             AND (? = 0 OR COALESCE(s.status, 'unread') = 'unread')
             AND p.version = (SELECT MAX(q.version) FROM paper_version q WHERE q.id = p.id)
           ORDER BY p.published DESC, p.id""",
        (since, eligible),
    )
    return [Paper(**json.loads(row["payload"])) for row in rows]


def get_paper(db, paper_id, version=None):
    row = db.execute(
        """SELECT payload FROM paper_version
           WHERE id = ? AND (? IS NULL OR version = ?) ORDER BY version DESC LIMIT 1""",
        (paper_id, version, version),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown paper: {paper_id}" + (f"v{version}" if version else ""))
    return Paper(**json.loads(row["payload"]))


def versions(db, since="0001-01-01"):
    return db.execute(
        "SELECT payload FROM paper_version WHERE published >= ? ORDER BY id, version", (since,)
    )


def setting(db, key):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None

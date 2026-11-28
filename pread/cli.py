import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from xml.etree.ElementTree import ParseError

from .arxiv import Arxiv, parse_feed
from .evidence import SOURCES, enrich
from .knowledge import add_term, answer
from .model import Model
from .personalization import (
    calibrated_config, feedback_report, link, links, record_feedback, related, review, review_candidates,
)
from .ranking import load_config, rank, rerank
from .reading import add_note, export_notes, paragraphs, prepare, translate
from .store import Paper, connect, get_paper, paper_evidence, save_papers, setting, versions


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return result


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def collect(args, db, config):
    if args.atom:
        papers = parse_feed(args.atom.read_bytes())
        save_papers(db, papers)
        return len(papers)
    total = 0
    for page in Arxiv().pages(config["categories"], args.days, args.limit):
        save_papers(db, page)
        db.commit()
        total += len(page)
    return total


def import_markers(db, path):
    with path.open(encoding="utf-8-sig") as stream:
        snapshot = json.load(stream)
    if not isinstance(snapshot, dict) or snapshot.get("format") != 1 or not isinstance(snapshot.get("versions"), list):
        raise ValueError("Unsupported marker format")
    markers = snapshot["versions"]
    save_papers(db, (Paper(**item) for item in markers))
    return {"imported": len(markers)}


def write_markers(db, stream, since):
    stream.write('{"format":1,"versions":[\n')
    separator = ""
    for row in versions(db, since):
        stream.write(separator)
        stream.write(row[0])
        separator = ",\n"
    stream.write("\n]}\n")


def export_markers(db, path, since):
    if path is None:
        write_markers(db, sys.stdout, since)
        return
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            write_markers(db, stream, since)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def external_search(query, limit):
    return [dict(id=paper.id, version=paper.version, title=paper.title, url=paper.url,
                 abstract=paper.abstract, published=paper.published)
            for paper in Arxiv().search(query, min(limit, 50))]


def ensure_week(db, args, config, today):
    iso = today.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    if setting(db, "paused") == "1":
        return {"week": week, "status": "paused"}
    previous = db.execute("SELECT payload FROM recommendation WHERE week = ?", (week,)).fetchone()
    if previous:
        return json.loads(previous[0])
    if getattr(args, "markers", None):
        import_markers(db, args.markers)
        db.commit()
    elif not args.no_collect:
        collect(args, db, config)
    if getattr(args, "calibrate", False):
        config = calibrated_config(db, config)
    use_reranker = getattr(args, "rerank", False)
    pool_size = 20 if use_reranker else 5
    candidates = rank(db, config, today, args.days, pool_size, args.semantic)
    candidates = candidates or rank(db, config, today, max(90, args.days), pool_size, args.semantic)
    if not candidates:
        return {"week": week, "status": "no-candidates"}
    if use_reranker:
        candidates = rerank(candidates, config)[:5]
    if getattr(args, "review", False):
        candidates = review_candidates(db, args.db.parent, candidates, config, Model.configured(db))
        if all("assessment_error" in card for card in candidates):
            return {"week": week, "status": "review-unavailable", "review_candidates": candidates}
    result = {"week": week, "status": "ready", "primary": candidates[0],
              "alternatives": candidates[1:3], "review_candidates": candidates}
    db.execute("INSERT OR IGNORE INTO recommendation VALUES (?, ?)", (week, json.dumps(result)))
    return json.loads(db.execute("SELECT payload FROM recommendation WHERE week = ?", (week,)).fetchone()[0])


def add_collection_options(parser):
    parser.add_argument("--days", type=positive, default=30)
    parser.add_argument("--limit", type=positive, default=1500)
    parser.add_argument("--atom", type=Path, help="Import an arXiv Atom snapshot")


def parser():
    cli = argparse.ArgumentParser(prog="research", description="Local AI research reader")
    cli.add_argument("--db", type=Path, default=Path("data/research.db"))
    cli.add_argument("--config", type=Path)
    commands = cli.add_subparsers(dest="command", required=True)
    add_collection_options(commands.add_parser("collect", help="Collect arXiv version metadata"))
    ranking = commands.add_parser("rank", help="Rank eligible local papers")
    ranking.add_argument("--days", type=positive, default=30)
    ranking.add_argument("--top", type=positive, default=20)
    ranking.add_argument("--semantic", action="store_true")
    ranking.add_argument("--rerank", action="store_true")
    weekly = commands.add_parser("weekly", help="Generate one recommendation per ISO week")
    add_collection_options(weekly)
    weekly.add_argument("--semantic", action="store_true")
    weekly.add_argument("--rerank", action="store_true", help="Rerank the twenty highest-scoring candidates locally")
    weekly.add_argument("--review", action="store_true", help="Download and assess up to five candidates with the configured model")
    weekly.add_argument("--calibrate", action="store_true", help="Use topic preferences calibrated from reading feedback")
    weekly.add_argument("--no-collect", action="store_true", help="Use the existing local catalog")
    weekly.add_argument("--markers", type=Path, help="Import a metadata snapshot as the weekly collection step")
    export = commands.add_parser("export-markers", help="Stream version metadata as JSON")
    export.add_argument("--output", type=Path, help="Atomically replace this file instead of writing to stdout")
    export.add_argument("--days", type=positive, help="Only include papers submitted within this many days")
    commands.add_parser("import-markers", help="Import version metadata as JSON").add_argument("path", type=Path)
    status = commands.add_parser("status", help="Include or exclude a paper from future ranking")
    status.add_argument("id")
    status.add_argument("value", choices=("unread", "read", "skipped"))
    commands.add_parser("pause", help="Pause weekly recommendations")
    commands.add_parser("resume", help="Allow the next weekly recommendation")
    for name, help_text in (
        ("prepare", "Download and parse a selected paper version"),
        ("translate", "Translate selected paragraph anchors"),
        ("note", "Save a Markdown note"),
        ("export-notes", "Export paper notes as Markdown"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("id")
        command.add_argument("--version", type=positive)
    commands.choices["prepare"].add_argument("--pdf", type=Path, help="Use an existing local PDF")
    commands.choices["translate"].add_argument("anchors", nargs="+")
    commands.choices["note"].add_argument("text")
    commands.choices["note"].add_argument("--anchor", default="")
    commands.choices["export-notes"].add_argument("path", type=Path)
    reader = commands.add_parser("reader", help="Open the desktop reader")
    reader.add_argument("id", nargs="?")
    reader.add_argument("--version", type=positive)
    lookup = commands.add_parser("lookup", help="Find glossary entries or cited paper passages")
    lookup.add_argument("query")
    lookup.add_argument("--paper")
    lookup.add_argument("--version", type=positive)
    lookup.add_argument("--top", type=positive, default=5)
    lookup.add_argument("--semantic", action="store_true")
    lookup.add_argument("--explain", action="store_true", help="Send retrieved excerpts to the configured model")
    lookup.add_argument("--external", action="store_true", help="Search public arXiv metadata when local evidence is insufficient")
    glossary = commands.add_parser("glossary-add", help="Save a definition with its source")
    for field in ("term", "definition", "source"):
        glossary.add_argument(field)
    external = commands.add_parser("research-search", help="Search public arXiv metadata")
    external.add_argument("query")
    external.add_argument("--top", type=positive, default=5)
    review_command = commands.add_parser("review", help="Assess a prepared paper and verify evidence quotes")
    review_command.add_argument("id")
    review_command.add_argument("--version", type=positive)
    feedback = commands.add_parser("feedback", help="Record a completed or partial reading session")
    feedback.add_argument("week", help="ISO week, e.g. 2026-W48")
    feedback.add_argument("id")
    feedback.add_argument("--minutes", type=positive, required=True)
    for field in ("relevance", "understanding", "quality"):
        feedback.add_argument(f"--{field}", type=int, choices=range(1, 6), required=True)
    feedback.add_argument("--completed", action="store_true")
    feedback.add_argument("--reason", default="")
    commands.add_parser("feedback-report", help="Summarize recommendation and reading outcomes")
    related_command = commands.add_parser("related", help="Find papers by shared terms and show verified links")
    related_command.add_argument("id")
    related_command.add_argument("--top", type=positive, default=5)
    link_command = commands.add_parser("link", help="Record a paper relationship with supporting evidence")
    for field in ("source", "target", "relation", "evidence"):
        link_command.add_argument(field)
    extra = commands.add_parser("enrich", help="Collect optional public evidence for a known paper")
    extra.add_argument("id")
    extra.add_argument("--source", choices=SOURCES, action="append")
    extra.add_argument("--github-repo")
    extra.add_argument("--openreview-id")
    extra.add_argument("--openalex-id")
    commands.add_parser("evidence", help="Show saved public evidence").add_argument("id")
    return cli


def execute(args, db, config, today):
    match args.command:
        case "collect":
            return {"collected": collect(args, db, config)}
        case "rank":
            if args.rerank:
                return rerank(rank(db, config, today, args.days, max(20, args.top), args.semantic), config)[:args.top]
            return rank(db, config, today, args.days, args.top, args.semantic)
        case "weekly":
            if args.markers and args.atom:
                raise ValueError("--markers and --atom are mutually exclusive")
            return ensure_week(db, args, config, today)
        case "export-markers":
            if args.output:
                database = args.db.resolve()
                protected = {Path(f"{database}{suffix}") for suffix in ("", "-wal", "-shm", "-journal")}
                if args.output.resolve() in protected:
                    raise ValueError("Marker output must not overwrite the SQLite catalog or its sidecar files")
            since = (today - timedelta(days=args.days)).isoformat() if args.days else "0001-01-01"
            export_markers(db, args.output, since)
            return None
        case "import-markers":
            return import_markers(db, args.path)
        case "status":
            get_paper(db, args.id)
            db.execute("INSERT OR REPLACE INTO paper_state VALUES (?, ?)", (args.id, args.value))
            return {"id": args.id, "status": args.value}
        case "pause" | "resume":
            db.execute("INSERT OR REPLACE INTO settings VALUES ('paused', ?)", (str(int(args.command == "pause")),))
            return {"status": args.command}
        case "prepare":
            return prepare(db, args.db.parent, get_paper(db, args.id, args.version), args.pdf)
        case "translate":
            paper = get_paper(db, args.id, args.version)
            available = {row["anchor"]: row for row in paragraphs(db, paper)}
            if missing := set(args.anchors) - available.keys():
                raise ValueError(f"Unknown paragraph anchors: {', '.join(sorted(missing))}")
            model = Model.configured(db)
            results = {}
            for anchor in args.anchors:
                results[anchor] = translate(db, paper, available[anchor], model)
                db.commit()
            return results
        case "note":
            paper = get_paper(db, args.id, args.version)
            if args.anchor and args.anchor not in {row["anchor"] for row in paragraphs(db, paper)}:
                raise ValueError(f"Unknown paragraph anchor: {args.anchor}")
            if not args.text.strip():
                raise ValueError("A note cannot be empty")
            return {"note_id": add_note(db, paper, args.anchor, args.text)}
        case "export-notes":
            paper = get_paper(db, args.id, args.version)
            args.path.write_text(export_notes(db, paper), encoding="utf-8")
            return {"path": str(args.path.resolve())}
        case "glossary-add":
            add_term(db, args.term, args.definition, args.source)
            return {"term": args.term}
        case "lookup":
            if args.version and not args.paper:
                raise ValueError("--version requires --paper")
            paper = get_paper(db, args.paper, args.version) if args.paper else None
            result = answer(db, args.query, paper, model=(lambda: Model.configured(db)) if args.explain else None,
                            model_name=config["embedding_model"] if args.semantic else None, limit=min(args.top, 50))
            if args.external and result["status"] == "insufficient-evidence":
                matches = external_search(args.query, args.top)
                if matches:
                    return {"status": "external-metadata", "answer": None, "sources": matches}
            return result
        case "research-search":
            return external_search(args.query, args.top)
        case "review":
            paper = get_paper(db, args.id, args.version)
            return review(db, paper, Model.configured(db))
        case "feedback":
            record_feedback(db, args.week, args.id, args.minutes, args.relevance,
                            args.understanding, args.quality, args.completed, args.reason)
            return {"week": args.week, "id": args.id, "completed": args.completed}
        case "feedback-report":
            return feedback_report(db)
        case "related":
            paper = get_paper(db, args.id)
            return {"suggested": related(db, paper, args.top), "links": links(db, args.id)}
        case "link":
            link(db, args.source, args.target, args.relation, args.evidence)
            return {"source": args.source, "target": args.target, "relation": args.relation}
        case "enrich":
            paper = get_paper(db, args.id)
            identifiers = {"github": args.github_repo, "openreview": args.openreview_id, "openalex": args.openalex_id}
            sources = args.source or ["semantic-scholar", "hugging-face", *(k for k, v in identifiers.items() if v)]
            return enrich(db, paper, sources, identifiers)
        case "evidence":
            get_paper(db, args.id)
            return paper_evidence(db, args.id).get(args.id, {})


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config)
        today = datetime.now(ZoneInfo(config["timezone"])).date()
        if args.command == "reader":
            try:
                from .desktop import run
            except ImportError as error:
                raise ValueError("Install pread[reader] to open the desktop reader") from error
            return run(args.db, args.id, args.version, today, config)
        with connect(args.db) as db:
            result = execute(args, db, config, today)
        if result is not None:
            output(result)
        return 0
    except (OSError, ValueError, OverflowError, TypeError, KeyError, sqlite3.Error, ParseError, ZoneInfoNotFoundError) as error:
        print(f"research: {error}", file=sys.stderr)
        return 1

from datetime import date, timedelta
from importlib.resources import files
import math
import re
import tomllib

from .store import latest_papers, paper_evidence


def load_config(path=None):
    config = tomllib.loads(files("pread").joinpath("defaults.toml").read_text(encoding="utf-8"))
    if path:
        with path.open("rb") as stream:
            overrides = tomllib.load(stream)
        overrides["weights"] = config["weights"] | overrides.get("weights", {})
        config.update(overrides)
    weights = config["weights"]
    if (not all(math.isfinite(weight) and weight >= 0 for weight in weights.values())
            or not math.isclose(sum(weights.values()), 1.0)):
        raise ValueError("Ranking weights must be nonnegative and sum to one")
    if not math.isfinite(config["half_life_days"]) or config["half_life_days"] <= 0:
        raise ValueError("Freshness half-life must be positive")
    if not config["topics"]:
        raise ValueError("Configure at least one research topic")
    for topic in config["topics"].values():
        if not 0 <= topic["weight"] <= 1 or not topic["keywords"]:
            raise ValueError("Each topic needs keywords and a weight in [0, 1]")
    return config


def tokens(text):
    return re.findall(r"\w+", text.casefold())


def relevance(paper, topics):
    text = " " + " ".join(tokens(f"{paper.title} {paper.abstract}")) + " "
    return max((
        topic["weight"] * min(1, sum(
            f" {' '.join(tokens(keyword))} " in text for keyword in topic["keywords"]
        ) / 3),
        name,
    ) for name, topic in topics.items())


def embedding_scores(papers, config):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise ValueError("Install pread[semantic] to use embeddings") from error
    model = SentenceTransformer(config["embedding_model"], trust_remote_code=False)
    topics = config["topics"]
    queries = [f"{name}: {', '.join(topic['keywords'])}" for name, topic in topics.items()]
    theme_vectors = model.encode(queries, normalize_embeddings=True)
    vectors = model.encode([f"{paper.title}\n{paper.abstract}" for paper in papers],
                           normalize_embeddings=True)
    return [max(
        (topic["weight"] * (1 + max(-1, min(1, float(vector @ theme)))) / 2, name)
        for (name, topic), theme in zip(topics.items(), theme_vectors, strict=True)
    ) for vector in vectors]


def score_signals(values, weights):
    coverage = sum(weight for key, weight in weights.items() if values.get(key) is not None)
    score = sum(weight * values[key] for key, weight in weights.items()
                if values.get(key) is not None)
    return (score / coverage if coverage else 0.0), coverage


def impact(evidence):
    citations = evidence.get("semantic-scholar", {}).get("citations")
    if citations is None:
        citations = evidence.get("openalex", {}).get("citations")
    upvotes = evidence.get("hugging-face", {}).get("upvotes")
    values = [min(1.0, math.log1p(value) / math.log1p(100))
              for value in (citations, upvotes) if type(value) is int and value >= 0]
    return math.fsum(values) / len(values) if values else None


def rerank(cards, config, model=None):
    if not cards:
        return []
    if model is None:
        try:
            from sentence_transformers import CrossEncoder
            from torch.nn import Sigmoid
        except ImportError as error:
            raise ValueError("Install pread[semantic] to use the reranker") from error
        model = CrossEncoder(config["reranker_model"], trust_remote_code=False, activation_fn=Sigmoid())
    topics = config["topics"]
    pairs = [
        (", ".join(topics[card["topic"]]["keywords"]), f"{card['title']}\n{card['abstract']}")
        for card in cards
    ]
    reordered = []
    for card, probability in zip(cards, model.predict(pairs), strict=True):
        probability = float(probability)
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Reranker score must be between zero and one")
        weighted = topics[card["topic"]]["weight"] * probability
        signals = card["signals"] | {"relevance": .65 * card["signals"]["relevance"] + .35 * weighted}
        score, coverage = score_signals(signals, config["weights"])
        reordered.append(card | {"signals": signals, "reranker_relevance": round(weighted, 6),
                                 "score": round(score, 6), "coverage": round(coverage, 6)})
    return sorted(reordered, key=lambda card: (-card["score"], card["id"]))


def rank(db, config, today: date, days=30, top=20, semantic=False):
    papers = latest_papers(db, (today - timedelta(days=days)).isoformat())
    papers = [paper for paper in papers if date.fromisoformat(paper.published) <= today]
    keyword = {paper.id: relevance(paper, config["topics"]) for paper in papers}
    semantic_by_id = {}
    if semantic and papers:
        # Keep a recent sample alongside keyword matches to surface unfamiliar terms.
        shortlist = list({paper.id: paper for paper in [
            *sorted(papers, key=lambda paper: keyword[paper.id][0], reverse=True)[:80],
            *papers[:20],
        ]}.values())
        semantic_by_id = dict(zip(
            (paper.id for paper in shortlist), embedding_scores(shortlist, config), strict=True
        ))
        papers = shortlist
    cards = []
    evidence_by_id = paper_evidence(db)
    for paper in papers:
        relevance_score, topic = semantic_by_id.get(paper.id, keyword[paper.id])
        age = (today - date.fromisoformat(paper.published)).days
        minutes = [180, 240] if len(tokens(paper.abstract)) <= 250 else [210, 280]
        signals = dict.fromkeys(config["weights"])
        evidence = evidence_by_id.get(paper.id, {})
        signals.update(relevance=relevance_score, freshness=2 ** (-age / config["half_life_days"]),
                       reading_fit=min(1.0, 240 / minutes[-1]), impact=impact(evidence))
        score, coverage = score_signals(signals, config["weights"])
        cards.append(dict(
            id=paper.id, version=paper.version, title=paper.title, url=paper.url,
            pdf_url=paper.pdf_url, abstract=paper.abstract, published=paper.published,
            topic=topic, score=round(score, 6), coverage=round(coverage, 6), signals=signals,
            estimated_read_minutes=minutes, needs_review=coverage < .75,
        ))
        if evidence:
            cards[-1]["public_evidence"] = {source: item["url"] for source, item in evidence.items()}
    return sorted(cards, key=lambda card: (-card["score"], card["id"]))[:top]

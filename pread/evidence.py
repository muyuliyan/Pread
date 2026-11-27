from datetime import datetime, timezone
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .arxiv import USER_AGENT

SOURCES = ("semantic-scholar", "openreview", "github", "hugging-face", "openalex")


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        current, target = urlsplit(req.full_url), urlsplit(newurl)
        if (target.scheme, target.netloc) != (current.scheme, current.netloc):
            raise ValueError("Unexpected redirect from data provider")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_json(url, headers=None):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    with build_opener(SameHostRedirect()).open(request, timeout=20) as response:
        payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Data provider response exceeds 4 MiB")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("Expected provider metadata as a JSON object")
    return result


def content_value(note, field):
    item = note.get("content", {}).get(field)
    return item.get("value") if isinstance(item, dict) else item


def provider_data(source, paper, identifier, fetch):
    paper_id = quote(paper.id, safe="")
    if source == "semantic-scholar":
        fields = "url,citationCount,referenceCount,influentialCitationCount,externalIds"
        headers = {"x-api-key": key} if (key := os.getenv("PREAD_S2_KEY")) else {}
        data = fetch(f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{paper_id}?{urlencode({'fields': fields})}", headers)
        if (data.get("externalIds") or {}).get("ArXiv") not in (None, paper.id):
            raise ValueError("Semantic Scholar returned a different arXiv ID")
        return {"url": data["url"], "citations": data.get("citationCount"),
                "references": data.get("referenceCount"), "influential_citations": data.get("influentialCitationCount")}
    if source == "hugging-face":
        data = fetch(f"https://huggingface.co/api/papers/{paper_id}")
        if data.get("id") != paper.id:
            raise ValueError("Hugging Face returned a different arXiv ID")
        return {"url": f"https://huggingface.co/papers/{paper_id}", "upvotes": data.get("upvotes")}
    if source == "github":
        if not identifier or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", identifier):
            raise ValueError("GitHub requires --github-repo OWNER/REPO")
        data = fetch(f"https://api.github.com/repos/{identifier}", {"Accept": "application/vnd.github+json"})
        return {"url": data["html_url"], "stars": data.get("stargazers_count"), "last_push": data.get("pushed_at"),
                "archived": data.get("archived"), "license": (data.get("license") or {}).get("spdx_id")}
    if source == "openalex":
        if not identifier or not re.fullmatch(r"W\d+", identifier):
            raise ValueError("OpenAlex requires --openalex-id W123...")
        params = {"select": "id,doi,cited_by_count,open_access"}
        if key := os.getenv("PREAD_OPENALEX_KEY"):
            params["api_key"] = key
        data = fetch(f"https://api.openalex.org/works/{identifier}?{urlencode(params)}")
        return {"url": data["id"], "doi": data.get("doi"), "citations": data.get("cited_by_count"),
                "open_access": (data.get("open_access") or {}).get("is_oa")}
    if not identifier or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError("OpenReview requires --openreview-id FORUM_ID")
    params = urlencode({"forum": identifier, "limit": 100})
    data = fetch(f"https://api2.openreview.net/notes?{params}")
    forum_url = f"https://openreview.net/forum?id={identifier}"
    if not data.get("notes"):
        raise ValueError("No public notes returned for this OpenReview forum")
    public_reviews, decisions = [], []
    for note in data["notes"]:
        if note.get("forum") != identifier:
            continue
        invitations = note.get("invitations", [])
        if note.get("invitation"):
            invitations = [*invitations, note["invitation"]]
        if any(invitation.endswith(("/Official_Review", "/Review")) for invitation in invitations):
            details = {}
            for field in ("summary", "strengths", "weaknesses", "review"):
                if isinstance(text := content_value(note, field), str):
                    details[field] = text[:1600]
            public_reviews.append({"url": f"{forum_url}&noteId={quote(note['id'], safe='')}", **details})
        if isinstance(decision := content_value(note, "decision"), str):
            decisions.append(decision[:160])
    return {"url": forum_url, "reviews": public_reviews[:8], "decisions": decisions[:8]}


def enrich(db, paper, sources, identifiers, fetch=fetch_json):
    results, unavailable = {}, {}
    for source in dict.fromkeys(sources):
        try:
            record = provider_data(source, paper, identifiers.get(source), fetch)
        except HTTPError as error:
            unavailable[source] = f"HTTP {error.code}"
            error.close()
            continue
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            unavailable[source] = str(error)
            continue
        record["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results[source] = record
    db.executemany("INSERT OR REPLACE INTO paper_evidence VALUES (?, ?, ?)", (
        (paper.id, source, json.dumps(record)) for source, record in results.items()
    ))
    return {"id": paper.id, "updated": results, "unavailable": unavailable}

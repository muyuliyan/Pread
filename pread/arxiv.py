from datetime import datetime, timedelta, timezone
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .store import Paper

API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "Pread/0.1 (https://github.com/muyuliyan/Pread)"


def request(url, *, timeout=30):
    return urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)


def parse_feed(body):
    root = ElementTree.fromstring(body)
    if root.tag != f"{ATOM}feed":
        raise ValueError("Expected an arXiv Atom feed")
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        entry_id = entry.findtext(f"{ATOM}id", "")
        parsed = urlsplit(entry_id)
        match = re.fullmatch(r"/abs/(.+?)v(\d+)", parsed.path)
        if parsed.hostname not in {"arxiv.org", "www.arxiv.org"} or not match:
            message = entry.findtext(f"{ATOM}summary", "").strip()
            raise ValueError(f"Invalid arXiv entry: {message or entry_id}")
        papers.append(Paper(
            id=match[1], version=int(match[2]),
            title=" ".join(entry.findtext(f"{ATOM}title", "").split()),
            abstract=" ".join(entry.findtext(f"{ATOM}summary", "").split()),
            published=entry.findtext(f"{ATOM}published", "")[:10],
            updated=entry.findtext(f"{ATOM}updated", "")[:10],
            authors=[name.text or "" for name in entry.findall(f"{ATOM}author/{ATOM}name")],
            categories=[category.attrib["term"] for category in entry.findall(f"{ATOM}category")],
        ))
    return papers


class Arxiv:
    def __init__(self, open_url=request, sleep=time.sleep, clock=time.monotonic):
        self.open_url = open_url
        self.sleep = sleep
        self.clock = clock
        self.next_request = 0.0

    def fetch(self, url):
        for attempt in range(3):
            self.sleep(max(0, self.next_request - self.clock()))
            self.next_request = self.clock() + 3
            try:
                with self.open_url(url) as response:
                    body = response.read(8 * 1024 * 1024 + 1)
                    if len(body) > 8 * 1024 * 1024:
                        raise ValueError("arXiv response exceeds 8 MiB")
                    return parse_feed(body)
            except HTTPError as error:
                retry_after = error.headers.get("Retry-After", "")
                status = error.code
                error.close()
                if attempt == 2 or (status != 429 and status < 500):
                    raise
                delay = int(retry_after) if retry_after.isdecimal() else 3 * 2**attempt
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise
                delay = 3 * 2**attempt
            self.next_request = max(self.next_request, self.clock() + delay)

    def pages(self, categories, days, limit, now=None):
        if not categories or not all(re.fullmatch(r"[A-Za-z0-9.-]+", term) for term in categories):
            raise ValueError("Invalid arXiv categories")
        now = now or datetime.now(timezone.utc)
        start_date = (now - timedelta(days=days)).strftime("%Y%m%d%H%M")
        end_date = now.strftime("%Y%m%d%H%M")
        filters = " OR ".join(f"cat:{term}" for term in categories)
        query = f"({filters}) AND submittedDate:[{start_date} TO {end_date}]"
        for start in range(0, limit, 100):
            page_size = min(100, limit - start)
            params = urlencode(dict(search_query=query, start=start, max_results=page_size,
                                    sortBy="submittedDate", sortOrder="descending"))
            page = self.fetch(f"{API_URL}?{params}")
            yield page
            if len(page) < page_size:
                break

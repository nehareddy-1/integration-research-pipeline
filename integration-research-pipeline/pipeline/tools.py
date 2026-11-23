"""The agent's 'hands': web search and page scraping, executed through Composio.

- search()  -> Composio TAVILY_SEARCH
- scrape()  -> Composio FIRECRAWL_SCRAPE, cached on disk, with a plain-HTTP fallback
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from . import config

STATS = {"searches": 0, "scrapes": 0, "scrape_cache_hits": 0, "scrape_fallbacks": 0, "scrape_failures": 0}

_composio = None


def _client():
    global _composio
    if _composio is None:
        from composio import Composio
        if not config.COMPOSIO_API_KEY:
            raise RuntimeError("COMPOSIO_API_KEY is missing. Add it to your .env file.")
        _composio = Composio(api_key=config.COMPOSIO_API_KEY)
    return _composio


def _execute(slug: str, args: dict) -> dict:
    """Run one Composio tool. Uses the newest tool version (fine for a research script)."""
    res = _client().tools.execute(
        slug,
        arguments=args,
        user_id=config.COMPOSIO_USER_ID,
        dangerously_skip_version_check=True,
    )
    if not res.get("successful", False):
        raise RuntimeError(f"{slug} failed: {res.get('error')}")
    return res.get("data") or {}


def _find(obj, key):
    """Composio responses can be nested (data.data.results...). Find the first value under `key`."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r is not None:
                return r
    return None


# ---------------------------------------------------------------- search
def search(query: str, max_results: int | None = None) -> list[dict]:
    """Return [{url, title, content}] from Tavily via Composio."""
    STATS["searches"] += 1
    n = max_results or config.SEARCH_RESULTS_PER_QUERY
    if config.MOCK:
        from . import mock
        return mock.fake_search(query, n)
    try:
        data = _execute(config.SEARCH_TOOL, {"query": query, "max_results": n})
    except Exception:
        data = _execute(config.SEARCH_TOOL, {"query": query})
    results = _find(data, "results") or []
    out = []
    for r in results[:n]:
        if isinstance(r, dict) and r.get("url"):
            out.append({
                "url": r["url"],
                "title": (r.get("title") or "")[:200],
                "content": (r.get("content") or r.get("snippet") or "")[:500],
            })
    return out


# ---------------------------------------------------------------- scrape
def _cache_path(url: str):
    return config.CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _plain_fetch(url: str) -> str:
    import requests
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (research-bot)"})
    r.raise_for_status()
    p = _TextExtractor()
    p.feed(r.text)
    return html.unescape("\n".join(p.parts))


def scrape(url: str) -> dict:
    """Return {url, ok, text, via}. Cached forever on disk so re-runs and the verifier cost nothing."""
    cp = _cache_path(url)
    if cp.exists():
        STATS["scrape_cache_hits"] += 1
        return json.loads(cp.read_text())

    STATS["scrapes"] += 1
    text, via, ok = "", "", False
    if config.MOCK:
        from . import mock
        text, via, ok = mock.fake_page(url), "mock", True
    else:
        try:
            try:
                data = _execute(config.SCRAPE_TOOL, {"url": url, "formats": ["markdown"], "onlyMainContent": True})
            except Exception:
                data = _execute(config.SCRAPE_TOOL, {"url": url})
            text = _find(data, "markdown") or _find(data, "content") or ""
            via, ok = "firecrawl", bool(text)
        except Exception as e:
            via = f"firecrawl_error: {str(e)[:120]}"
        if not ok:  # fallback: plain HTTP (free, works for server-rendered docs)
            try:
                text = _plain_fetch(url)
                STATS["scrape_fallbacks"] += 1
                via, ok = "http_fallback", bool(text.strip())
            except Exception as e:
                STATS["scrape_failures"] += 1
                via = f"{via} | http_error: {str(e)[:120]}"

    text = re.sub(r"\n{3,}", "\n\n", text or "")
    page = {"url": url, "ok": ok, "via": via, "text": text}
    cp.write_text(json.dumps(page))
    return page


def page_excerpt(page: dict, limit: int | None = None) -> str:
    limit = limit or config.MAX_CHARS_PER_PAGE
    return (page.get("text") or "")[:limit]


def domain(url: str) -> str:
    try:
        return urlparse(url if "://" in url else "https://" + url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""

"""
RSS news feed ingestion filtered to watchlist tickers.

Uses Python's built-in xml.etree.ElementTree + requests (already a project dep)
to parse free public RSS feeds. No new dependencies required.

Headlines are matched to ticker symbols via word-boundary regex so "AAPL" in
"Apple (AAPL) beats earnings" is correctly attributed.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

_CACHE_TTL = 15  # minutes

# Free public RSS feeds — no login required
FEEDS: dict[str, str] = {
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "MarketWatch":   "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Reuters Biz":   "https://feeds.reuters.com/reuters/businessNews",
    "CNBC":          "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "Seeking Alpha": "https://seekingalpha.com/market_currents.xml",
}


@dataclass
class NewsItem:
    ticker: str         # matched ticker symbol, or "" for general market news
    headline: str
    source: str
    url: str
    published: str      # ISO 8601
    summary: str = ""


@dataclass
class NewsFeed:
    items: list[NewsItem] = field(default_factory=list)
    fetched_at: str = ""
    error: Optional[str] = None


def _fetch_one_feed(name: str, url: str, max_entries: int = 25) -> list[dict]:
    """Fetch a single RSS/Atom feed via requests + stdlib XML parser."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Nantucket/0.1"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        # Handle both RSS (<channel><item>) and Atom (<entry>)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)

        entries = []
        for item in items[:max_entries]:
            def _text(tag: str) -> str:
                el = item.find(tag) or item.find(f"atom:{tag}", ns)
                return (el.text or "").strip() if el is not None else ""

            link_el = item.find("link")
            if link_el is None:
                link_el = item.find("atom:link", ns)
            link = (link_el.get("href") or link_el.text or "").strip() if link_el is not None else ""

            entries.append({
                "source": name,
                "title": _text("title"),
                "link": link,
                "summary": _text("description") or _text("summary"),
                "published": _text("pubDate") or _text("published") or datetime.now().isoformat(),
            })
        return entries
    except Exception:
        return []


def _match_ticker(tickers: list[str], text: str) -> str:
    """Return the first ticker whose symbol appears as a whole word in text."""
    for ticker in tickers:
        # Escape any special regex chars in the ticker (e.g. BRK.B → BRK\.B)
        pattern = rf"\b{re.escape(ticker)}\b"
        if re.search(pattern, text, re.IGNORECASE):
            return ticker
    return ""


def _cache_key_for_tickers(tickers: list[str]) -> str:
    h = hashlib.md5(",".join(sorted(tickers)).encode()).hexdigest()[:8]
    return f"news_{h}"


def get_news_for_tickers(tickers: list[str], limit: int = 20) -> NewsFeed:
    """
    Fetch RSS headlines that mention any of the given tickers.
    Results are cached per unique ticker-set for 15 minutes.
    """
    from nantucket.db.database import get_econ_cached, set_econ_cache

    cache_key = _cache_key_for_tickers(tickers)
    cached = get_econ_cached(cache_key, _CACHE_TTL)
    if cached:
        items = [NewsItem(**i) for i in cached["items"]]
        return NewsFeed(items=items, fetched_at=cached["fetched_at"])

    all_entries: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch_one_feed, name, url): name for name, url in FEEDS.items()}
        for future in as_completed(futures):
            all_entries.extend(future.result())

    seen_urls: set[str] = set()
    items: list[NewsItem] = []
    for entry in all_entries:
        url = entry["link"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        text = entry["title"] + " " + entry["summary"]
        matched = _match_ticker(tickers, text)
        if not matched:
            continue
        items.append(NewsItem(
            ticker=matched,
            headline=entry["title"],
            source=entry["source"],
            url=url,
            published=entry["published"],
            summary=entry["summary"][:200],
        ))

    items = items[:limit]
    fetched_at = datetime.now().isoformat()
    set_econ_cache(cache_key, {"items": [i.__dict__ for i in items], "fetched_at": fetched_at})
    return NewsFeed(items=items, fetched_at=fetched_at)


def get_market_news(limit: int = 20) -> NewsFeed:
    """
    Fetch general market headlines (no ticker filter).
    Cached for 15 minutes.
    """
    from nantucket.db.database import get_econ_cached, set_econ_cache

    cache_key = "news_market_general"
    cached = get_econ_cached(cache_key, _CACHE_TTL)
    if cached:
        items = [NewsItem(**i) for i in cached["items"]]
        return NewsFeed(items=items, fetched_at=cached["fetched_at"])

    all_entries: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_fetch_one_feed, name, url): name for name, url in FEEDS.items()}
        for future in as_completed(futures):
            all_entries.extend(future.result())

    seen_urls: set[str] = set()
    items: list[NewsItem] = []
    for entry in all_entries:
        url = entry["link"]
        if url in seen_urls or not entry["title"]:
            continue
        seen_urls.add(url)
        items.append(NewsItem(
            ticker="",
            headline=entry["title"],
            source=entry["source"],
            url=url,
            published=entry["published"],
            summary=entry["summary"][:200],
        ))

    items = items[:limit]
    fetched_at = datetime.now().isoformat()
    set_econ_cache(cache_key, {"items": [i.__dict__ for i in items], "fetched_at": fetched_at})
    return NewsFeed(items=items, fetched_at=fetched_at)

#!/usr/bin/env python3
"""
Tech News Data Collector
========================
Fetches top tech stories from HN, Reddit, and RSS feeds.
Scrapes article content for each top candidate.
Outputs structured JSON to stdout for Claude to editorialize.

Stealth browser setup (one-time):
    pip install scrapling patchright msgspec
    python -m patchright install chromium
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from scrapling import Fetcher
import requests

NOW = datetime.now(timezone.utc)
TOP_N = 12  # candidates to scrape (Claude will pick final 5-6)

PRIORITY_KEYWORDS = [
    "ai", "artificial intelligence", "llm", "gpt", "openai", "anthropic", "gemini",
    "claude", "machine learning", "deep learning", "neural", "transformer",
    "apple", "google", "microsoft", "meta", "amazon", "nvidia", "tesla",
    "open source", "opensource", "github", "linux", "rust", "python",
    "startup", "funding", "acquisition", "ipo", "layoff",
    "security", "vulnerability", "breach", "hack", "malware",
    "regulation", "antitrust", "privacy", "gdpr",
    "chip", "semiconductor", "quantum",
    "developer", "dev tools", "framework",
]

fetcher = Fetcher()

# ── Stealth browser helper ────────────────────────────────────────────────
# StealthyFetcher uses Patchright (anti-detection Playwright fork) with 40+
# stealth flags to bypass TLS fingerprinting, Cloudflare, and bot detection.
# Falls back to plain requests when the stealth browser is unavailable.

_stealth_available = None  # lazy-init


def _check_stealth():
    """Check once whether StealthyFetcher + browser are available."""
    global _stealth_available
    if _stealth_available is not None:
        return _stealth_available
    try:
        from scrapling import StealthyFetcher
        # Quick probe to verify the browser binary exists
        StealthyFetcher.fetch(
            "https://httpbin.org/status/200",
            headless=True, disable_resources=True, timeout=15000,
        )
        _stealth_available = True
        print("[Stealth] Browser available — using StealthyFetcher", file=sys.stderr)
    except Exception as e:
        _stealth_available = False
        print(f"[Stealth] Browser unavailable ({e.__class__.__name__}), falling back to requests", file=sys.stderr)
    return _stealth_available


def stealth_fetch_xml(url):
    """Fetch a URL using the stealth browser, return bytes content."""
    from scrapling import StealthyFetcher
    resp = StealthyFetcher.fetch(
        url,
        headless=True,
        disable_resources=True,
        network_idle=True,
        timeout=20000,
    )
    if resp.status >= 400:
        raise Exception(f"HTTP {resp.status}")
    # resp.text has the page source; encode back to bytes for XML parsing
    return resp.text.encode("utf-8")


REQUESTS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def fetch_xml(url, timeout=15):
    """Fetch XML content, trying requests first then stealth browser on 403/503."""
    try:
        resp = requests.get(url, headers=REQUESTS_HEADERS, timeout=timeout)
        if resp.status_code < 400:
            return resp.content
        # Got a 4xx/5xx — try stealth if available
        if _check_stealth():
            print(f"  [Stealth retry] {url}", file=sys.stderr)
            return stealth_fetch_xml(url)
        resp.raise_for_status()  # will raise
    except requests.exceptions.HTTPError:
        raise
    except requests.exceptions.RequestException as e:
        # Connection-level failure (proxy block, DNS, etc.) — try stealth
        if _check_stealth():
            print(f"  [Stealth retry] {url}", file=sys.stderr)
            return stealth_fetch_xml(url)
        raise
    return resp.content


# ── Article scraping ───────────────────────────────────────────────────────
def scrape_article(url):
    """Scrape article page and return the first ~800 words of body text."""
    try:
        resp = fetcher.get(url)
    except Exception:
        return ""

    paragraphs = []
    selectors = [
        "article p",
        '[class*="article-body"] p',
        '[class*="post-content"] p',
        '[class*="entry-content"] p',
        '[class*="story-body"] p',
        "main p",
        ".content p",
    ]

    for sel in selectors:
        elems = resp.css(sel)
        if elems and len(elems) >= 2:
            for el in elems:
                text = el.get_all_text().strip() if hasattr(el, 'get_all_text') else (el.text or "").strip()
                text = re.sub(r"\s+", " ", text)
                if len(text) > 50 and not text.startswith(
                    ("Share", "Comment", "Subscribe", "Sign up", "Read more",
                     "Advertisement", "Cookie", "We use cookies")
                ):
                    paragraphs.append(text)
            if paragraphs:
                break

    # Fallback: meta descriptions
    if not paragraphs:
        for sel, attr in [('meta[name="description"]', "content"),
                          ('meta[property="og:description"]', "content")]:
            meta = resp.css(sel)
            if meta:
                desc = meta[0].attrib.get(attr, "").strip()
                if desc and len(desc) > 40:
                    paragraphs.append(desc)

    # Return first ~800 words worth of content
    combined = " ".join(paragraphs)
    words = combined.split()
    if len(words) > 800:
        combined = " ".join(words[:800]) + "…"
    return combined


# ── Source fetchers ─────────────────────────────────────────────────────────
def fetch_hn_stories(limit=30):
    stories = []
    try:
        resp = fetcher.get("https://hacker-news.firebaseio.com/v0/topstories.json")
        ids = resp.json()[:limit]
    except Exception as e:
        print(f"[HN] Failed: {e}", file=sys.stderr)
        return stories

    def fetch_item(item_id):
        try:
            r = fetcher.get(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
            return r.json()
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_item, sid): sid for sid in ids}
        for fut in as_completed(futures):
            item = fut.result()
            if item and item.get("title"):
                stories.append({
                    "title": item["title"],
                    "url": item.get("url", f"https://news.ycombinator.com/item?id={item['id']}"),
                    "source": "Hacker News",
                    "score": item.get("score", 0),
                    "comments": item.get("descendants", 0),
                })
    print(f"[HN] {len(stories)} stories", file=sys.stderr)
    return stories


def fetch_reddit_stories():
    """Fetch Reddit stories via RSS feeds, with stealth browser fallback."""
    stories = []
    subreddits = ["technology", "programming", "MachineLearning"]
    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit=10"
        try:
            content = fetch_xml(url)
            root = ET.fromstring(content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)
            for entry in entries[:10]:
                title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                if title and link:
                    stories.append({
                        "title": title,
                        "url": link.strip(),
                        "source": f"Reddit r/{sub}",
                        "score": 0,
                        "comments": 0,
                    })
        except Exception as e:
            print(f"[Reddit r/{sub}] Failed: {e}", file=sys.stderr)
    print(f"[Reddit] {len(stories)} stories", file=sys.stderr)
    return stories


def fetch_lobsters_stories():
    """Fetch stories from Lobsters (community-curated tech news, like HN)."""
    stories = []
    try:
        resp = requests.get("https://lobste.rs/hottest.json", headers=REQUESTS_HEADERS, timeout=15)
        resp.raise_for_status()
        items = resp.json()
        for item in items[:25]:
            title = item.get("title", "").strip()
            url = item.get("url") or item.get("comments_url", "")
            if title and url:
                stories.append({
                    "title": title,
                    "url": url.strip(),
                    "source": "Lobsters",
                    "score": item.get("score", 0),
                    "comments": item.get("comment_count", 0),
                })
    except Exception as e:
        print(f"[Lobsters] Failed: {e}", file=sys.stderr)
    print(f"[Lobsters] {len(stories)} stories", file=sys.stderr)
    return stories


def fetch_google_news():
    """Fetch stories from Google News RSS — aggregates stories from major outlets.

    This is especially valuable when direct RSS feeds from outlets like Reuters,
    NYT, CNN, etc. are blocked by network restrictions, since Google News
    re-publishes their headlines through its own accessible RSS endpoint.
    """
    stories = []
    feeds = [
        # Technology topic
        ("https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en",
         "technology"),
        # AI / artificial intelligence search
        ("https://news.google.com/rss/search?q=artificial+intelligence+OR+AI+OR+LLM+when:1d&hl=en-US&gl=US&ceid=US:en",
         "ai"),
    ]
    seen_titles = set()
    for url, label in feeds:
        try:
            resp = requests.get(url, headers=REQUESTS_HEADERS, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            count = 0
            for item in root.findall(".//item")[:20]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                source_el = item.find("source")
                source_name = source_el.text.strip() if source_el is not None and source_el.text else "Google News"
                desc = item.findtext("description") or ""
                desc = re.sub(r"<[^>]+>", "", desc)
                desc = re.sub(r"&nbsp;", " ", desc)
                desc = re.sub(r"\s+", " ", desc).strip()

                # De-duplicate within Google News feeds (same story appears in multiple feeds)
                title_key = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()
                if not title or not link or title_key in seen_titles:
                    continue
                seen_titles.add(title_key)

                stories.append({
                    "title": title,
                    "url": link,
                    "source": f"Google News ({source_name})",
                    "score": 0,
                    "comments": 0,
                    "rss_summary": desc[:500] if desc else "",
                })
                count += 1
            print(f"[Google News/{label}] {count} items", file=sys.stderr)
        except Exception as e:
            print(f"[Google News/{label}] Failed: {e}", file=sys.stderr)
    print(f"[Google News] {len(stories)} stories total", file=sys.stderr)
    return stories


def _parse_rss_items(content):
    """Parse RSS/Atom XML content and return (root, items, namespace)."""
    root = ET.fromstring(content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    return root, items, ns


def fetch_rss_stories():
    feeds = [
        # ── Reliable feeds (tested accessible from cloud runtimes) ──
        ("https://feeds.bbci.co.uk/news/technology/rss.xml", "BBC"),
        ("https://feeds.npr.org/1019/rss.xml", "NPR"),
        ("https://www.cnbc.com/id/19854910/device/rss/rss.html", "CNBC"),
        ("https://techcrunch.com/feed/", "TechCrunch"),
        ("https://www.theverge.com/rss/index.xml", "The Verge"),
        ("https://www.zdnet.com/news/rss.xml", "ZDNet"),
        ("https://www.engadget.com/rss.xml", "Engadget"),
        ("https://www.theregister.com/headlines.atom", "The Register"),
        ("https://venturebeat.com/feed/", "VentureBeat"),
        ("https://www.technologyreview.com/feed/", "MIT Tech Review"),
        # ── Often blocked by proxies/firewalls (try anyway, short timeout) ──
        ("https://feeds.reuters.com/reuters/technologyNews", "Reuters"),
        ("https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "NYT"),
        ("https://feeds.washingtonpost.com/rss/business/technology", "Washington Post"),
        ("https://rss.cnn.com/rss/edition_technology.rss", "CNN"),
        ("https://www.wired.com/feed/rss", "Wired"),
        ("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica"),
    ]
    # Sources known to be blocked in some cloud runtimes — use short timeout
    _flaky_sources = {"Reuters", "NYT", "Washington Post", "CNN", "Wired", "Ars Technica"}
    stories = []
    succeeded_sources = set()
    for url, source_name in feeds:
        try:
            timeout = 8 if source_name in _flaky_sources else 15
            content = fetch_xml(url, timeout=timeout)
            _, items, ns = _parse_rss_items(content)

            count = 0
            for item in items[:10]:
                title = (item.findtext("title") or item.findtext("atom:title", namespaces=ns) or "").strip()
                link = item.findtext("link") or ""
                if not link:
                    link_el = item.find("atom:link", ns)
                    if link_el is not None:
                        link = link_el.get("href", "")
                desc = (
                    item.findtext("description")
                    or item.findtext("atom:summary", namespaces=ns)
                    or item.findtext("atom:content", namespaces=ns)
                    or ""
                )
                # Clean HTML from RSS description
                desc = re.sub(r"<[^>]+>", "", desc)
                desc = re.sub(r"\s+", " ", desc).strip()

                if title and link:
                    stories.append({
                        "title": title,
                        "url": link.strip(),
                        "source": source_name,
                        "score": 0,
                        "comments": 0,
                        "rss_summary": desc[:500] if desc else "",
                    })
                    count += 1
            succeeded_sources.add(source_name)
            print(f"[RSS {source_name}] {count} items", file=sys.stderr)
        except Exception as e:
            print(f"[RSS {source_name}] Failed: {e}", file=sys.stderr)
    print(f"[RSS] {len(stories)} stories from {len(succeeded_sources)} sources", file=sys.stderr)
    return stories


# ── Scoring & dedup ────────────────────────────────────────────────────────
def normalize_url(url):
    url = re.sub(r"^https?://(www\.)?", "", url)
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/").lower()


def normalize_title(title):
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def compute_priority(story):
    score = 0
    title_lower = story["title"].lower()

    kw_hits = sum(1 for kw in PRIORITY_KEYWORDS if kw in title_lower)
    score += min(kw_hits * 10, 30)

    # Mainstream / wire services get highest bonus
    source_bonus = {
        "Reuters": 40, "AP": 40, "NYT": 38, "BBC": 38,
        "NPR": 35, "CNBC": 35, "Washington Post": 35, "CNN": 33,
        "Wired": 28, "MIT Tech Review": 28, "ZDNet": 28,
        "TechCrunch": 25, "Ars Technica": 22, "The Verge": 22,
        "The Register": 22, "VentureBeat": 22, "Engadget": 25,
        "Lobsters": 10,
    }
    src = story["source"]
    score += source_bonus.get(src, 0)
    # Google News items carry the original publisher name — check for known outlets
    if src.startswith("Google News ("):
        inner_source = src[len("Google News ("):-1]
        # Give credit based on the original publisher
        score += source_bonus.get(inner_source, 15)  # 15 default for Google News unknowns
    if "Reddit" in src:
        score += 8  # secondary source

    # HN/Reddit/Lobsters scores still contribute but are capped lower
    if story["source"] == "Hacker News":
        score += min(story["score"] / 20, 20)  # secondary source
    elif "Reddit" in story["source"]:
        score += min(story["score"] / 40, 20)
    elif story["source"] == "Lobsters":
        score += min(story["score"] / 5, 20)

    if any(tag in title_lower for tag in ["show hn:", "ask hn:", "tell hn:", "launch hn:"]):
        score -= 25

    return score


def deduplicate(stories):
    """Merge stories about the same topic, keeping track of all sources."""
    groups = []  # list of {"canonical": story, "all_sources": [...]}

    for s in stories:
        norm_url = normalize_url(s["url"])
        norm_title = normalize_title(s["title"])
        merged = False

        for g in groups:
            canon = g["canonical"]
            # Check URL match
            if normalize_url(canon["url"]) == norm_url:
                merged = True
            else:
                # Check title similarity
                canon_title = normalize_title(canon["title"])
                words_a, words_b = set(norm_title.split()), set(canon_title.split())
                if len(words_a) > 3 and len(words_b) > 3:
                    overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                    if overlap > 0.65:
                        merged = True

            if merged:
                src_entry = {"source": s["source"], "url": s["url"],
                             "score": s.get("score", 0), "comments": s.get("comments", 0)}
                g["all_sources"].append(src_entry)
                # Prefer the version from a mainstream source as canonical
                if compute_priority(s) > compute_priority(canon):
                    s["all_sources"] = g["all_sources"]
                    g["canonical"] = s
                break

        if not merged:
            src_entry = {"source": s["source"], "url": s["url"],
                         "score": s.get("score", 0), "comments": s.get("comments", 0)}
            groups.append({"canonical": s, "all_sources": [src_entry]})

    # Attach all_sources list to each canonical story
    unique = []
    for g in groups:
        story = g["canonical"]
        story["all_sources"] = g["all_sources"]
        unique.append(story)
    return unique


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    print("Fetching stories from all sources...", file=sys.stderr)

    # Pre-check stealth browser availability (done once, reused by all fetchers)
    _check_stealth()

    all_stories = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(fetch_hn_stories),
            pool.submit(fetch_reddit_stories),
            pool.submit(fetch_lobsters_stories),
            pool.submit(fetch_google_news),
            pool.submit(fetch_rss_stories),
        ]
        for fut in as_completed(futures):
            try:
                all_stories.extend(fut.result())
            except Exception as e:
                print(f"Source failed: {e}", file=sys.stderr)

    print(f"Total raw: {len(all_stories)}", file=sys.stderr)
    unique = deduplicate(all_stories)
    print(f"After dedup: {len(unique)}", file=sys.stderr)

    for s in unique:
        s["priority"] = compute_priority(s)
        # Boost stories covered by multiple sources — these are bigger news
        n_sources = len(s.get("all_sources", []))
        if n_sources >= 3:
            s["priority"] += 20
        elif n_sources >= 2:
            s["priority"] += 10
    unique.sort(key=lambda s: s["priority"], reverse=True)

    # Source diversity: max 2 from any single source to ensure breadth
    # Normalize Google News sources to a single bucket for diversity limiting
    def _diversity_key(source):
        if source.startswith("Google News ("):
            return "Google News"
        return source

    top = []
    source_counts = {}
    for s in unique:
        key = _diversity_key(s["source"])
        if source_counts.get(key, 0) >= 2:
            continue
        top.append(s)
        source_counts[key] = source_counts.get(key, 0) + 1
        if len(top) >= TOP_N:
            break

    print(f"Selected {len(top)} stories for deep scraping...", file=sys.stderr)

    # Scrape full articles in parallel
    article_texts = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {pool.submit(scrape_article, s["url"]): i for i, s in enumerate(top)}
        for fut in as_completed(future_map):
            idx = future_map[fut]
            try:
                article_texts[idx] = fut.result()
            except Exception:
                article_texts[idx] = ""

    # Build output
    output = {
        "date": NOW.strftime("%Y-%m-%d"),
        "collected_at": NOW.isoformat(),
        "stories": [],
    }
    for i, s in enumerate(top):
        body = article_texts.get(i, "")
        # Use RSS summary as fallback
        if not body and s.get("rss_summary"):
            body = s["rss_summary"]

        # Deduplicate source names in all_sources
        all_sources = s.get("all_sources", [{"source": s["source"], "url": s["url"],
                                              "score": s["score"], "comments": s.get("comments", 0)}])
        seen_src_names = set()
        deduped_sources = []
        for src in all_sources:
            if src["source"] not in seen_src_names:
                seen_src_names.add(src["source"])
                deduped_sources.append(src)

        story = {
            "title": s["title"],
            "url": s["url"],
            "source": s["source"],
            "score": s["score"],
            "comments": s.get("comments", 0),
            "article_text": body,
            "all_sources": deduped_sources,
            "source_count": len(deduped_sources),
        }
        output["stories"].append(story)
        src_names = ", ".join(src["source"] for src in deduped_sources)
        print(f"  [{i+1}] {s['title'][:60]} ({len(body)} chars) [{src_names}]", file=sys.stderr)

    # Output JSON to stdout
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

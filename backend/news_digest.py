#!/usr/bin/env python3
"""
Tech News Data Collector
========================
Fetches top tech stories from HN, Reddit, and RSS feeds.
Scrapes article content for each top candidate.
Outputs structured JSON to stdout for Claude to editorialize.
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
    """Fetch Reddit stories via RSS feeds (no OAuth required)."""
    stories = []
    subreddits = ["technology", "programming", "MachineLearning"]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    for sub in subreddits:
        try:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit=10",
                headers=headers, timeout=15,
            )
            r.raise_for_status()
            root = ET.fromstring(r.content)
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


def fetch_rss_stories():
    feeds = [
        # ── Mainstream / wire services ──
        ("https://feeds.bbci.co.uk/news/technology/rss.xml", "BBC"),
        ("https://feeds.npr.org/1019/rss.xml", "NPR"),
        ("https://www.cnbc.com/id/19854910/device/rss/rss.html", "CNBC"),
        ("https://feeds.washingtonpost.com/rss/business/technology", "Washington Post"),
        ("https://www.zdnet.com/news/rss.xml", "ZDNet"),
        ("https://www.engadget.com/rss.xml", "Engadget"),
        # ── Tech-focused outlets ──
        ("https://techcrunch.com/feed/", "TechCrunch"),
        ("https://www.theverge.com/rss/index.xml", "The Verge"),
        ("https://www.theregister.com/headlines.atom", "The Register"),
        ("https://venturebeat.com/feed/", "VentureBeat"),
        ("https://www.technologyreview.com/feed/", "MIT Tech Review"),
    ]
    # Use requests with browser-like headers for RSS feeds (scrapling's Fetcher
    # gets blocked by many RSS endpoints that return 403/503).
    rss_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    stories = []
    for url, source_name in feeds:
        try:
            resp = requests.get(url, headers=rss_headers, timeout=15)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)

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
            print(f"[RSS {source_name}] {len(items[:10])} items", file=sys.stderr)
        except Exception as e:
            print(f"[RSS {source_name}] Failed: {e}", file=sys.stderr)
    print(f"[RSS] {len(stories)} stories total", file=sys.stderr)
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
        "BBC": 38, "NPR": 35, "CNBC": 35, "Washington Post": 35,
        "ZDNet": 28, "Engadget": 25,
        "TechCrunch": 25, "The Verge": 22, "The Register": 22,
        "VentureBeat": 22, "MIT Tech Review": 28,
    }
    score += source_bonus.get(story["source"], 0)
    if "Reddit" in story["source"]:
        score += 8  # secondary source

    # HN/Reddit scores still contribute but are capped lower
    if story["source"] == "Hacker News":
        score += min(story["score"] / 20, 20)  # secondary source
    elif "Reddit" in story["source"]:
        score += min(story["score"] / 40, 20)

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

    all_stories = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(fetch_hn_stories),
            pool.submit(fetch_reddit_stories),
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
    top = []
    source_counts = {}
    for s in unique:
        src = s["source"]
        if source_counts.get(src, 0) >= 2:
            continue
        top.append(s)
        source_counts[src] = source_counts.get(src, 0) + 1
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

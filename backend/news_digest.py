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
TOP_N = 8  # candidates to scrape (Claude will pick final 5-6)

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
    stories = []
    subreddits = [("technology", 10), ("programming", 10), ("MachineLearning", 10)]
    headers = {"User-Agent": "TechNewsBot/1.0"}
    for sub, limit in subreddits:
        try:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/top.json?t=day&limit={limit}",
                headers=headers, timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            for post in data.get("data", {}).get("children", []):
                d = post["data"]
                stories.append({
                    "title": d["title"],
                    "url": d.get("url", f"https://reddit.com{d['permalink']}"),
                    "source": f"Reddit r/{sub}",
                    "score": d.get("score", 0),
                    "comments": d.get("num_comments", 0),
                })
        except Exception as e:
            print(f"[Reddit r/{sub}] Failed: {e}", file=sys.stderr)
    print(f"[Reddit] {len(stories)} stories", file=sys.stderr)
    return stories


def fetch_rss_stories():
    feeds = [
        ("https://techcrunch.com/feed/", "TechCrunch"),
        ("https://feeds.arstechnica.com/arstechnica/index", "Ars Technica"),
        ("https://www.theverge.com/rss/index.xml", "The Verge"),
    ]
    stories = []
    for url, source_name in feeds:
        try:
            resp = fetcher.get(url)
            root = ET.fromstring(resp.body.decode("utf-8", errors="replace"))
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
        except Exception as e:
            print(f"[RSS {source_name}] Failed: {e}", file=sys.stderr)
    print(f"[RSS] {len(stories)} stories", file=sys.stderr)
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

    source_bonus = {"TechCrunch": 25, "Ars Technica": 22, "The Verge": 22}
    score += source_bonus.get(story["source"], 0)
    if "Reddit" in story["source"]:
        score += 12

    if story["source"] == "Hacker News":
        score += min(story["score"] / 15, 35)
    elif "Reddit" in story["source"]:
        score += min(story["score"] / 40, 30)

    if any(tag in title_lower for tag in ["show hn:", "ask hn:", "tell hn:", "launch hn:"]):
        score -= 25

    return score


def deduplicate(stories):
    seen_urls = set()
    seen_titles = set()
    unique = []
    for s in stories:
        norm_url = normalize_url(s["url"])
        norm_title = normalize_title(s["title"])
        if norm_url in seen_urls:
            continue
        is_dup = False
        for st in seen_titles:
            words_a, words_b = set(norm_title.split()), set(st.split())
            if len(words_a) > 3 and len(words_b) > 3:
                if len(words_a & words_b) / min(len(words_a), len(words_b)) > 0.65:
                    is_dup = True
                    break
        if is_dup:
            continue
        seen_urls.add(norm_url)
        seen_titles.add(norm_title)
        unique.append(s)
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
    unique.sort(key=lambda s: s["priority"], reverse=True)

    # Source diversity: max 3 from any single source
    top = []
    source_counts = {}
    for s in unique:
        src = s["source"]
        if source_counts.get(src, 0) >= 3:
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
        story = {
            "title": s["title"],
            "url": s["url"],
            "source": s["source"],
            "score": s["score"],
            "comments": s.get("comments", 0),
            "article_text": body,
        }
        output["stories"].append(story)
        print(f"  [{i+1}] {s['title'][:70]} ({len(body)} chars)", file=sys.stderr)

    # Output JSON to stdout
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

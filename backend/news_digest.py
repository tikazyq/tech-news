#!/usr/bin/env python3
"""Tech News Digest — fetches top stories from multiple sources, summarizes, sends to Telegram."""

import json
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

from scrapling import Fetcher
import requests

# ── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = "8082240790:AAGTsbXS_GGtN7sEvDBbEkbm4_RveYOfEAs"
TELEGRAM_CHAT_ID = "5465534784"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

NOW = datetime.now(timezone.utc)
DAY_AGO = NOW - timedelta(hours=24)

TOP_N = 10  # final stories to send

PRIORITY_KEYWORDS = [
    "ai", "artificial intelligence", "llm", "gpt", "openai", "anthropic", "gemini",
    "machine learning", "deep learning", "neural", "transformer",
    "apple", "google", "microsoft", "meta", "amazon", "nvidia", "tesla",
    "open source", "opensource", "github", "linux", "rust", "python",
    "startup", "funding", "acquisition", "ipo", "layoff",
    "security", "vulnerability", "breach", "hack",
    "regulation", "antitrust", "privacy", "gdpr",
    "chip", "semiconductor", "quantum",
    "developer", "dev tools", "ide", "compiler", "framework",
]

fetcher = Fetcher()


# ── Source fetchers ─────────────────────────────────────────────────────────
def fetch_hn_stories(limit=30):
    """Fetch top Hacker News stories."""
    stories = []
    try:
        resp = fetcher.get("https://hacker-news.firebaseio.com/v0/topstories.json")
        ids = resp.json()[:limit]
    except Exception as e:
        print(f"[HN] Failed to get top stories: {e}")
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
                    "source_detail": f"⬆{item.get('score', 0)}",
                    "time": item.get("time", 0),
                })
    print(f"[HN] Fetched {len(stories)} stories")
    return stories


def fetch_reddit_stories():
    """Fetch top stories from Reddit tech subreddits."""
    stories = []
    subreddits = [
        ("technology", 10),
        ("programming", 10),
        ("MachineLearning", 10),
    ]
    for sub, limit in subreddits:
        try:
            resp = fetcher.get(
                f"https://www.reddit.com/r/{sub}/top.json?t=day&limit={limit}",
            )
            data = resp.json()
            for post in data.get("data", {}).get("children", []):
                d = post["data"]
                stories.append({
                    "title": d["title"],
                    "url": d.get("url", f"https://reddit.com{d['permalink']}"),
                    "source": "Reddit",
                    "score": d.get("score", 0),
                    "source_detail": f"r/{sub}",
                    "time": d.get("created_utc", 0),
                })
        except Exception as e:
            print(f"[Reddit r/{sub}] Failed: {e}")
    print(f"[Reddit] Fetched {len(stories)} stories")
    return stories


def fetch_rss_stories():
    """Fetch stories from RSS feeds using scrapling + xml parsing."""
    import xml.etree.ElementTree as ET

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
            # Handle both RSS and Atom
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item")  # RSS
            if not items:
                items = root.findall(".//atom:entry", ns)  # Atom

            for item in items[:10]:
                title = item.findtext("title") or item.findtext("atom:title", namespaces=ns) or ""
                link = item.findtext("link") or ""
                if not link:
                    link_el = item.find("atom:link", ns)
                    if link_el is not None:
                        link = link_el.get("href", "")
                # Try to get description for summary
                desc = (
                    item.findtext("description")
                    or item.findtext("atom:summary", namespaces=ns)
                    or item.findtext("atom:content", namespaces=ns)
                    or ""
                )
                title = title.strip()
                if title and link:
                    stories.append({
                        "title": title,
                        "url": link.strip(),
                        "source": source_name,
                        "score": 0,
                        "source_detail": source_name,
                        "time": time.time(),  # approximate as now
                        "description": desc,
                    })
        except Exception as e:
            print(f"[RSS {source_name}] Failed: {e}")
    print(f"[RSS] Fetched {len(stories)} stories")
    return stories


def scrape_article_summary(url, timeout=8):
    """Scrape the article page and extract the first ~2 sentences as a summary."""
    try:
        resp = fetcher.get(url)
        # Try meta description first (most reliable)
        meta = resp.css('meta[name="description"]')
        if meta:
            desc = meta[0].attrib.get("content", "").strip()
            if desc and len(desc) > 30:
                return _truncate(desc, 200)

        # Try og:description
        og = resp.css('meta[property="og:description"]')
        if og:
            desc = og[0].attrib.get("content", "").strip()
            if desc and len(desc) > 30:
                return _truncate(desc, 200)

        # Fallback: first <p> tags in article body
        for selector in ["article p", ".post-content p", ".article-body p", "main p", "p"]:
            paras = resp.css(selector)
            if paras:
                text = " ".join(p.text.strip() for p in paras[:2] if p.text)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 50:
                    return _truncate(text, 200)
    except Exception:
        pass
    return ""


def _truncate(text, max_len):
    """Truncate text at sentence boundary."""
    text = re.sub(r"<[^>]+>", "", text)  # strip any HTML tags
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    # Try to break at last sentence end
    for sep in [". ", "! ", "? "]:
        idx = cut.rfind(sep)
        if idx > max_len // 2:
            return cut[: idx + 1]
    return cut.rsplit(" ", 1)[0] + "…"


# ── Scoring & dedup ────────────────────────────────────────────────────────
def normalize_url(url):
    """Normalize URL for dedup."""
    url = re.sub(r"^https?://(www\.)?", "", url)
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/").lower()


def normalize_title(title):
    """Normalize title for fuzzy dedup."""
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def compute_priority(story):
    """Score a story for ranking. Higher = more relevant mainstream tech news."""
    score = 0
    title_lower = story["title"].lower()

    # Keyword relevance
    for kw in PRIORITY_KEYWORDS:
        if kw in title_lower:
            score += 15
            break  # one match is enough

    # Source diversity bonus — boost non-HN sources to balance
    if story["source"] == "TechCrunch":
        score += 20
    elif story["source"] == "Ars Technica":
        score += 18
    elif story["source"] == "The Verge":
        score += 18
    elif story["source"] == "Reddit":
        score += 10

    # Engagement score (normalized)
    if story["source"] == "Hacker News":
        score += min(story["score"] / 20, 30)  # cap HN boost at 30
    elif story["source"] == "Reddit":
        score += min(story["score"] / 50, 25)

    # Penalize HN self-posts / Show HN / Ask HN (less mainstream)
    if any(tag in title_lower for tag in ["show hn:", "ask hn:", "tell hn:", "launch hn:"]):
        score -= 20

    return score


def deduplicate(stories):
    """Remove duplicate stories based on URL and fuzzy title matching."""
    seen_urls = set()
    seen_titles = set()
    unique = []
    for s in stories:
        norm_url = normalize_url(s["url"])
        norm_title = normalize_title(s["title"])
        # Check URL
        if norm_url in seen_urls:
            continue
        # Check title similarity (simple word overlap)
        is_dup = False
        for st in seen_titles:
            words_a = set(norm_title.split())
            words_b = set(st.split())
            if len(words_a) > 3 and len(words_b) > 3:
                overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
                if overlap > 0.7:
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
    print("Fetching stories from all sources...")

    # Fetch from all sources in parallel
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
                print(f"Source failed: {e}")

    print(f"\nTotal raw stories: {len(all_stories)}")

    # Deduplicate
    unique = deduplicate(all_stories)
    print(f"After dedup: {len(unique)}")

    # Score and rank
    for s in unique:
        s["priority"] = compute_priority(s)
    unique.sort(key=lambda s: s["priority"], reverse=True)

    # Enforce source diversity: max 4 from any single source in top 10
    top = []
    source_counts = {}
    for s in unique:
        src = s["source"]
        if source_counts.get(src, 0) >= 4:
            continue
        top.append(s)
        source_counts[src] = source_counts.get(src, 0) + 1
        if len(top) >= TOP_N:
            break

    print(f"Selected top {len(top)} stories")

    # Fetch summaries for top stories in parallel
    print("Fetching article summaries...")
    summaries = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {pool.submit(scrape_article_summary, s["url"]): i for i, s in enumerate(top)}
        for fut in as_completed(future_map):
            idx = future_map[fut]
            try:
                summaries[idx] = fut.result()
            except Exception:
                summaries[idx] = ""

    # Also check RSS descriptions as fallback
    for i, s in enumerate(top):
        if not summaries.get(i) and s.get("description"):
            desc = re.sub(r"<[^>]+>", "", s["description"])
            desc = re.sub(r"\s+", " ", desc).strip()
            if len(desc) > 30:
                summaries[i] = _truncate(desc, 200)

    # Format message
    date_str = NOW.strftime("%Y-%m-%d")
    lines = [f"\U0001f4f0 *Tech News Digest — {date_str}*\n"]

    for i, s in enumerate(top, 1):
        title = s["title"].replace("[", "(").replace("]", ")")  # escape markdown links
        # Escape markdown special chars in title for Telegram
        for ch in ("*", "_", "`"):
            title = title.replace(ch, "")
        source_tag = s["source_detail"]
        line = f"{i}. [{title}]({s['url']}) — _{source_tag}_"
        summary = summaries.get(i - 1, "")
        if summary:
            # Escape markdown in summary
            for ch in ("*", "_", "`", "[", "]"):
                summary = summary.replace(ch, "")
            line += f"\n   {summary}"
        lines.append(line)

    lines.append(f"\n_@MarvinZhangTelegramableBot_")
    message = "\n\n".join(lines)

    # Ensure under 4096 chars
    if len(message) > 4090:
        message = message[:4087] + "…"

    print(f"\nMessage length: {len(message)} chars")
    print("=" * 60)
    print(message)
    print("=" * 60)

    # Send to Telegram
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(TELEGRAM_API, json=payload, timeout=15)
        r.raise_for_status()
        result = r.json()
        if result.get("ok"):
            print("\nDigest sent to Telegram successfully!")
        else:
            print(f"\nTelegram API error: {result}")
    except Exception as e:
        print(f"\nFailed to send to Telegram: {e}")


if __name__ == "__main__":
    main()

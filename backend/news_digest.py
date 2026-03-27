#!/usr/bin/env python3
"""Tech News Digest — in-depth briefing with article summaries, sent to Telegram."""

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from scrapling import Fetcher
import requests

# ── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = "8082240790:AAGTsbXS_GGtN7sEvDBbEkbm4_RveYOfEAs"
TELEGRAM_CHAT_ID = "5465534784"
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

NOW = datetime.now(timezone.utc)
DATE_STR = NOW.strftime("%Y-%m-%d")
TOP_N = 6  # fewer stories, deeper coverage

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
    """Scrape article page and extract substantive content for summarization."""
    try:
        resp = fetcher.get(url)
    except Exception:
        return ""

    paragraphs = []

    # Try structured article selectors first, then broader fallbacks
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
                # Skip short fragments, nav items, captions
                if len(text) > 60 and not text.startswith(("Share", "Comment", "Subscribe", "Sign up", "Read more", "Advertisement")):
                    paragraphs.append(text)
            if paragraphs:
                break

    # Fallback: meta description + og:description
    if not paragraphs:
        for attr_name, attr_key in [('meta[name="description"]', "content"),
                                     ('meta[property="og:description"]', "content")]:
            meta = resp.css(attr_name)
            if meta:
                desc = meta[0].attrib.get(attr_key, "").strip()
                if desc and len(desc) > 40:
                    paragraphs.append(desc)

    return "\n".join(paragraphs)


def extract_summary(full_text, max_chars=500):
    """Extract a coherent summary from scraped article text.

    Takes the opening paragraphs (which typically contain the lede and key facts)
    and truncates at sentence boundaries.
    """
    if not full_text:
        return ""

    # Clean HTML remnants
    text = re.sub(r"<[^>]+>", "", full_text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) <= max_chars:
        return text

    # Take first max_chars and cut at the last sentence boundary
    cut = text[:max_chars]
    # Find last sentence-ending punctuation
    best = -1
    for sep in [". ", "! ", "? ", ".\n"]:
        idx = cut.rfind(sep)
        if idx > best:
            best = idx

    if best > max_chars * 0.4:
        return cut[: best + 1].strip()

    # Fall back to word boundary
    return cut.rsplit(" ", 1)[0].strip() + "…"


# ── Source fetchers ─────────────────────────────────────────────────────────
def fetch_hn_stories(limit=30):
    """Fetch top Hacker News stories."""
    stories = []
    try:
        resp = fetcher.get("https://hacker-news.firebaseio.com/v0/topstories.json")
        ids = resp.json()[:limit]
    except Exception as e:
        print(f"[HN] Failed: {e}")
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
                    "source_detail": f"HN ⬆{item.get('score', 0)}",
                    "time": item.get("time", 0),
                })
    print(f"[HN] {len(stories)} stories")
    return stories


def fetch_reddit_stories():
    """Fetch top stories from Reddit tech subreddits."""
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
                    "source": "Reddit",
                    "score": d.get("score", 0),
                    "source_detail": f"r/{sub}",
                    "time": d.get("created_utc", 0),
                })
        except Exception as e:
            print(f"[Reddit r/{sub}] Failed: {e}")
    print(f"[Reddit] {len(stories)} stories")
    return stories


def fetch_rss_stories():
    """Fetch stories from RSS feeds."""
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
                if title and link:
                    stories.append({
                        "title": title,
                        "url": link.strip(),
                        "source": source_name,
                        "score": 0,
                        "source_detail": source_name,
                        "time": time.time(),
                        "rss_description": desc,
                    })
        except Exception as e:
            print(f"[RSS {source_name}] Failed: {e}")
    print(f"[RSS] {len(stories)} stories")
    return stories


# ── Scoring & dedup ────────────────────────────────────────────────────────
def normalize_url(url):
    url = re.sub(r"^https?://(www\.)?", "", url)
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/").lower()


def normalize_title(title):
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def compute_priority(story):
    """Score story for ranking — mainstream tech news first."""
    score = 0
    title_lower = story["title"].lower()

    # Keyword relevance (count multiple matches)
    kw_hits = sum(1 for kw in PRIORITY_KEYWORDS if kw in title_lower)
    score += min(kw_hits * 10, 30)

    # Source bonuses to diversify away from HN
    source_bonus = {"TechCrunch": 25, "Ars Technica": 22, "The Verge": 22, "Reddit": 12}
    score += source_bonus.get(story["source"], 0)

    # Engagement (capped)
    if story["source"] == "Hacker News":
        score += min(story["score"] / 15, 35)
    elif story["source"] == "Reddit":
        score += min(story["score"] / 40, 30)

    # Penalize niche HN formats
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


# ── Telegram formatting ───────────────────────────────────────────────────
def escape_md(text):
    """Escape Telegram Markdown special characters."""
    for ch in ("*", "_", "`", "[", "]"):
        text = text.replace(ch, "")
    return text


def format_message(stories_with_summaries):
    """Format the digest as a Telegram message with in-depth summaries."""
    lines = [f"\U0001f4f0 *Tech News Digest — {DATE_STR}*"]
    lines.append("")

    for i, (story, summary) in enumerate(stories_with_summaries, 1):
        title = story["title"].replace("[", "(").replace("]", ")")
        for ch in ("*", "_", "`"):
            title = title.replace(ch, "")
        source_tag = story["source_detail"]

        # Title line with link
        lines.append(f"*{i}. {escape_md(story['title'])}*")
        lines.append(f"[Read full article \u2192]({story['url']}) | _{source_tag}_")

        # Summary paragraph
        if summary:
            lines.append(escape_md(summary))

        lines.append("")  # blank line between stories

    lines.append("_@MarvinZhangTelegramableBot_")
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    print("Fetching stories from all sources...")

    # Fetch all sources in parallel
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

    print(f"\nTotal raw: {len(all_stories)}")
    unique = deduplicate(all_stories)
    print(f"After dedup: {len(unique)}")

    # Score and rank
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

    print(f"Selected {len(top)} stories for deep scraping...")

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

    # Build summaries (scrape-based, with RSS fallback)
    stories_with_summaries = []
    for i, s in enumerate(top):
        raw = article_texts.get(i, "")
        summary = extract_summary(raw, max_chars=450)

        # Fallback to RSS description if scraping yielded nothing
        if not summary and s.get("rss_description"):
            desc = re.sub(r"<[^>]+>", "", s["rss_description"])
            desc = re.sub(r"\s+", " ", desc).strip()
            if len(desc) > 40:
                summary = extract_summary(desc, max_chars=450)

        if not summary:
            summary = "(Could not extract summary — click link to read)"

        stories_with_summaries.append((s, summary))
        print(f"  [{i+1}] {s['title'][:60]}... => {len(summary)} chars summary")

    # Format and send
    message = format_message(stories_with_summaries)

    # Telegram limit is 4096 chars; trim if needed
    if len(message) > 4090:
        # Re-try with shorter summaries
        shorter = []
        for s, summ in stories_with_summaries:
            shorter.append((s, extract_summary(summ, max_chars=250)))
        message = format_message(shorter)
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

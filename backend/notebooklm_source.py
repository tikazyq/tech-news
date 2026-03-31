#!/usr/bin/env python3
"""
NotebookLM Source Document Generator
=====================================
Generates a rich, well-structured document from the news digest output,
optimized for NotebookLM to consume and convert into an Audio Overview
(podcast-style briefing).

The output is a comprehensive Markdown document containing:
- Full article texts with context
- Source credibility signals (multi-source coverage, community engagement)
- Editorial guidance (topic priorities, audience context)

Usage:
    python3 backend/news_digest.py 2>/dev/null | python3 backend/notebooklm_source.py
    python3 backend/notebooklm_source.py --input /tmp/digest.json
    python3 backend/notebooklm_source.py --input /tmp/digest.json --output /tmp/source.md
"""

import argparse
import json
import sys
from datetime import datetime, timezone


def format_source_doc(digest: dict) -> str:
    """Convert news digest JSON into a rich document for NotebookLM."""
    date = digest.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    stories = digest.get("stories", [])

    sections = []

    # Header with editorial context — helps NotebookLM understand the audience
    sections.append(f"""# Tech News Briefing — {date}

## About This Briefing

This is a daily technology news briefing for busy professionals. The audience
listens during their morning commute. Coverage priorities: AI/ML developments,
big tech company news, cybersecurity, developer tools, and tech regulation.

The following {len(stories)} stories are today's top candidates, ranked by
importance. Each includes the full article text, source attribution, and
community engagement signals. Stories covered by multiple independent sources
are considered more significant.
""")

    # Each story as a rich section
    for i, story in enumerate(stories, 1):
        title = story.get("title", "Untitled")
        url = story.get("url", "")
        source = story.get("source", "Unknown")
        score = story.get("score", 0)
        comments = story.get("comments", 0)
        article_text = story.get("article_text", "").strip()
        all_sources = story.get("all_sources", [])
        source_count = story.get("source_count", len(all_sources))

        # Source attribution line
        if source_count > 1:
            source_names = ", ".join(s["source"] for s in all_sources)
            coverage = f"Covered by {source_count} sources: {source_names}"
        else:
            coverage = f"Source: {source}"

        # Community engagement signals
        engagement_parts = []
        for src in all_sources:
            src_score = src.get("score", 0)
            src_comments = src.get("comments", 0)
            if src_score > 0 or src_comments > 0:
                parts = []
                if src_score > 0:
                    parts.append(f"{src_score} points")
                if src_comments > 0:
                    parts.append(f"{src_comments} comments")
                engagement_parts.append(f"{src['source']}: {', '.join(parts)}")

        engagement = ""
        if engagement_parts:
            engagement = f"\nCommunity engagement: {'; '.join(engagement_parts)}"

        # Article body
        if article_text:
            body = article_text
        else:
            body = "(Full article text unavailable — summarize based on the headline and source context.)"

        sections.append(f"""---

## Story {i}: {title}

{coverage}{engagement}
URL: {url}

### Full Article

{body}
""")

    # Footer with editorial notes
    sources_used = set()
    for story in stories:
        for src in story.get("all_sources", []):
            sources_used.add(src["source"])

    sections.append(f"""---

## Editorial Notes

- Total stories collected: {len(stories)}
- Sources represented: {', '.join(sorted(sources_used))}
- Stories are pre-ranked by importance (AI/tech policy weighted highest)
- Multi-source stories indicate higher news significance
- Community engagement scores reflect Hacker News, Reddit, and Lobsters activity
""")

    return '\n'.join(sections)


def main():
    parser = argparse.ArgumentParser(
        description="Generate a NotebookLM-optimized source document from news digest JSON")
    parser.add_argument("--input", help="Path to digest JSON file (default: stdin)")
    parser.add_argument("--output", help="Path to output Markdown file (default: stdout)")
    args = parser.parse_args()

    # Read digest JSON
    if args.input:
        with open(args.input) as f:
            digest = json.load(f)
    else:
        digest = json.load(sys.stdin)

    # Generate source document
    doc = format_source_doc(digest)

    # Write output
    if args.output:
        with open(args.output, "w") as f:
            f.write(doc)
        print(f"Source document written to {args.output} ({len(doc)} chars)", file=sys.stderr)
    else:
        print(doc)


if __name__ == "__main__":
    main()

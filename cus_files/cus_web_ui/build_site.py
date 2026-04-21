#!/usr/bin/env python3
# coding=utf-8
"""
Generate a lightweight static homepage from TrendRadar SQLite data.

This script is intentionally isolated under cus_files/ so the upstream project
can remain mostly untouched while we still ship a friendlier personal-news UI.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class KeywordGroup:
    label: str
    terms: List[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build custom static news UI")
    parser.add_argument(
        "--project-root",
        default=Path(__file__).resolve().parents[2],
        help="Project root directory",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory where the generated site should be written",
    )
    parser.add_argument(
        "--title",
        default="Engineer News Radar",
        help="Site title",
    )
    parser.add_argument(
        "--max-topic-items",
        type=int,
        default=10,
        help="Maximum items per topic section",
    )
    parser.add_argument(
        "--max-platform-items",
        type=int,
        default=8,
        help="Maximum items shown per platform in the latest snapshot",
    )
    parser.add_argument(
        "--max-rss-items",
        type=int,
        default=18,
        help="Maximum RSS items to show",
    )
    return parser.parse_args()


def load_yaml(path: Path) -> Dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def parse_keyword_groups(path: Path) -> List[KeywordGroup]:
    if not path.exists():
        return []

    groups: List[KeywordGroup] = []
    current_label: Optional[str] = None
    current_terms: List[str] = []

    def flush() -> None:
        nonlocal current_label, current_terms
        if current_terms:
            label = current_label or current_terms[0]
            groups.append(KeywordGroup(label=label, terms=current_terms[:]))
        current_label = None
        current_terms = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue

        if line.startswith("#"):
            if not current_terms:
                current_label = line.lstrip("#").strip() or current_label
            continue

        # Keep the parser intentionally simple for the custom UI backup file.
        # We only consume visible search terms and ignore advanced upstream syntax.
        if line.startswith(("@", "!", "+", "[")):
            continue
        if "=>" in line:
            line = line.split("=>", 1)[0].strip()
        if line.startswith("/") and line.endswith("/"):
            line = line[1:-1].strip()
        if line:
            current_terms.append(line)

    flush()
    return groups


def latest_db_path(db_dir: Path) -> Optional[Path]:
    if not db_dir.exists():
        return None
    db_files = sorted(db_dir.glob("*.db"))
    return db_files[-1] if db_files else None


def fetch_all_news(news_db: Path) -> Dict:
    conn = sqlite3.connect(news_db)
    conn.row_factory = sqlite3.Row
    try:
        latest_crawl = conn.execute(
            "SELECT crawl_time, total_items FROM crawl_records ORDER BY id DESC LIMIT 1"
        ).fetchone()
        rows = conn.execute(
            """
            SELECT
                n.id,
                n.title,
                n.platform_id,
                COALESCE(p.name, n.platform_id) AS platform_name,
                n.rank,
                n.url,
                n.mobile_url,
                n.last_crawl_time,
                n.first_crawl_time,
                n.crawl_count
            FROM news_items n
            LEFT JOIN platforms p ON n.platform_id = p.id
            ORDER BY n.last_crawl_time DESC, n.crawl_count DESC, n.rank ASC
            """
        ).fetchall()
    finally:
        conn.close()

    return {
        "latest_crawl_time": latest_crawl["crawl_time"] if latest_crawl else None,
        "latest_total_items": latest_crawl["total_items"] if latest_crawl else 0,
        "items": [dict(row) for row in rows],
        "date": news_db.stem,
    }


def fetch_rss_items(rss_db: Optional[Path], limit: int) -> List[Dict]:
    if not rss_db or not rss_db.exists():
        return []

    conn = sqlite3.connect(rss_db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                i.title,
                i.url,
                i.published_at,
                i.last_crawl_time,
                COALESCE(f.name, i.feed_id) AS feed_name
            FROM rss_items i
            LEFT JOIN rss_feeds f ON i.feed_id = f.id
            ORDER BY
                COALESCE(i.published_at, '') DESC,
                i.last_crawl_time DESC,
                i.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]


def match_topic_groups(items: List[Dict], groups: List[KeywordGroup], max_items: int) -> List[Dict]:
    topics: List[Dict] = []
    for group in groups:
        matched = []
        seen_titles = set()
        terms_lower = [term.lower() for term in group.terms]
        for item in items:
            title_lower = item["title"].lower()
            if any(term in title_lower for term in terms_lower):
                if item["title"] in seen_titles:
                    continue
                seen_titles.add(item["title"])
                enriched = dict(item)
                enriched["is_fresh"] = item["last_crawl_time"] == item.get("_latest_crawl_time")
                matched.append(enriched)
            if len(matched) >= max_items:
                break
        if matched:
            topics.append(
                {
                    "label": group.label,
                    "terms": group.terms,
                    "count": len(matched),
                    "items": matched,
                }
            )
    return topics


def build_snapshot(items: List[Dict], latest_crawl_time: Optional[str], max_platform_items: int) -> List[Dict]:
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    platform_names: Dict[str, str] = {}
    for item in items:
        if item["last_crawl_time"] != latest_crawl_time:
            continue
        grouped[item["platform_id"]].append(item)
        platform_names[item["platform_id"]] = item["platform_name"]

    snapshot = []
    for platform_id, platform_items in grouped.items():
        ordered = sorted(platform_items, key=lambda x: (x["rank"], x["title"]))
        snapshot.append(
            {
                "platform_id": platform_id,
                "platform_name": platform_names.get(platform_id, platform_id),
                "count": len(ordered),
                "items": ordered[:max_platform_items],
            }
        )
    snapshot.sort(key=lambda x: x["platform_name"].lower())
    return snapshot


def build_site_data(
    project_root: Path,
    output_dir: Path,
    title: str,
    max_topic_items: int,
    max_platform_items: int,
    max_rss_items: int,
) -> Dict:
    config = load_yaml(project_root / "cus_files" / "config.yaml")
    keyword_groups = parse_keyword_groups(project_root / "cus_files" / "frequency_words.txt")
    rss_enabled = bool(config.get("rss", {}).get("enabled", False))
    enabled_platform_ids = {
        src.get("id")
        for src in config.get("platforms", {}).get("sources", [])
        if src.get("id")
    }

    news_db = latest_db_path(output_dir / "news")
    if not news_db:
        return {
            "title": title,
            "empty": True,
            "message": "No news database found yet. Wait for the first crawl to finish.",
        }

    news_data = fetch_all_news(news_db)
    items = [
        item for item in news_data["items"]
        if not enabled_platform_ids or item["platform_id"] in enabled_platform_ids
    ]
    latest_crawl_time = news_data["latest_crawl_time"]
    for item in items:
        item["_latest_crawl_time"] = latest_crawl_time

    snapshot = build_snapshot(items, latest_crawl_time, max_platform_items)
    topics = match_topic_groups(items, keyword_groups, max_topic_items)
    rss_db = latest_db_path(output_dir / "rss") if rss_enabled else None
    rss_items = fetch_rss_items(rss_db, max_rss_items) if rss_enabled else []

    enabled_platforms = [
        src.get("name", src.get("id", ""))
        for src in config.get("platforms", {}).get("sources", [])
        if src.get("id")
    ]
    enabled_feeds = [
        feed.get("name", feed.get("id", ""))
        for feed in config.get("rss", {}).get("feeds", [])
        if feed.get("enabled", True)
    ]

    return {
        "title": title,
        "empty": False,
        "generated_from": "cus_files/cus_web_ui",
        "news_date": news_data["date"],
        "latest_crawl_time": latest_crawl_time,
        "latest_total_items": news_data["latest_total_items"],
        "total_topics": len(topics),
        "total_snapshot_platforms": len(snapshot),
        "platforms": enabled_platforms,
        "rss_enabled": rss_enabled,
        "rss_feeds": enabled_feeds if rss_enabled else [],
        "topics": topics,
        "snapshot": snapshot,
        "rss_items": rss_items,
    }


def write_site(project_root: Path, output_dir: Path, site_data: Dict) -> None:
    source_dir = project_root / "cus_files" / "cus_web_ui"
    target_assets_dir = output_dir / "custom-ui"
    target_assets_dir.mkdir(parents=True, exist_ok=True)

    template_html = (source_dir / "index.template.html").read_text(encoding="utf-8")
    rendered_index = template_html.replace("__SITE_TITLE__", site_data.get("title", "Engineer News Radar"))

    (target_assets_dir / "styles.css").write_text(
        (source_dir / "styles.css").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (target_assets_dir / "app.js").write_text(
        (source_dir / "app.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (target_assets_dir / "site-data.json").write_text(
        json.dumps(site_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (target_assets_dir / "index.html").write_text(rendered_index, encoding="utf-8")
    (output_dir / "index.html").write_text(rendered_index, encoding="utf-8")


def main() -> None:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    site_data = build_site_data(
        project_root=project_root,
        output_dir=output_dir,
        title=args.title,
        max_topic_items=args.max_topic_items,
        max_platform_items=args.max_platform_items,
        max_rss_items=args.max_rss_items,
    )
    write_site(project_root, output_dir, site_data)

    print(f"[custom-ui] Static site written to: {output_dir / 'index.html'}")


if __name__ == "__main__":
    main()

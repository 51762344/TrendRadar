"""
TrendRadar REST API service.

This module intentionally uses the Python standard library so the API can be
started as a lightweight sidecar without adding deployment dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from mcp_server.services.data_service import DataService
from mcp_server.tools.analytics import AnalyticsTools, calculate_news_weight
from mcp_server.tools.system import SystemManagementTools
from mcp_server.utils.errors import DataNotFoundError, MCPError
from trendradar import __version__


MAX_LIMIT = 200
MAX_RSS_DAYS = 30
CRAWL_MIN_INTERVAL_SECONDS = 60


class ApiError(Exception):
    """HTTP API error with a stable JSON payload."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z"


def parse_list(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_int(value: Optional[str], default: int, name: str, minimum: int = 1, maximum: Optional[int] = None) -> int:
    if value is None or value == "":
        result = default
    else:
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise ApiError("BAD_REQUEST", f"{name} must be an integer", 400)

    if result < minimum:
        raise ApiError("BAD_REQUEST", f"{name} must be >= {minimum}", 400)
    if maximum is not None and result > maximum:
        result = maximum
    return result


def normalize_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(normalized, fmt)
            return dt.isoformat(timespec="milliseconds") + "Z"
        except ValueError:
            continue
    return text


def stable_id(*parts: Any) -> str:
    source = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def get_query(query: Dict[str, List[str]], name: str) -> Optional[str]:
    values = query.get(name)
    if not values:
        return None
    return values[-1]


class TrendRadarApi:
    def __init__(self, project_root: Optional[str] = None, token: str = "", cors_origin: str = ""):
        self.project_root = str(Path(project_root or os.getcwd()).resolve())
        self.token = token
        self.cors_origin = cors_origin.strip()
        self.data_service = DataService(self.project_root)
        self.analytics_tools = AnalyticsTools(self.project_root)
        self.system_tools = SystemManagementTools(self.project_root)
        self._last_crawl_trigger = 0.0

    def check_auth(self, headers: Any) -> None:
        if not self.token:
            return
        auth_header = headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        if not hmac.compare_digest(auth_header, expected):
            raise ApiError("UNAUTHORIZED", "Unauthorized", 401)

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "trendradar-api",
            "version": __version__,
            "generatedAt": utc_now_iso(),
        }

    def latest_news(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        platforms = parse_list(get_query(query, "platforms"))
        limit = parse_int(get_query(query, "limit"), 50, "limit", maximum=MAX_LIMIT)
        include_url = parse_bool(get_query(query, "includeUrl"), True)

        latest_date = self._latest_date("news")
        try:
            all_titles, id_to_name, timestamps = self.data_service.parser.read_all_titles_for_date(
                date=latest_date,
                platform_ids=platforms,
                db_type="news",
            )
        except DataNotFoundError as exc:
            raise ApiError(exc.code, exc.message, 503)

        collected_at = self._latest_timestamp_iso(timestamps) or latest_date.strftime("%Y-%m-%d")
        raw_items = []
        for platform_id, titles in all_titles.items():
            platform_name = id_to_name.get(platform_id, platform_id)
            for title, info in titles.items():
                raw_items.append({
                    "title": title,
                    "platform": platform_id,
                    "platform_name": platform_name,
                    "rank": info.get("ranks", [None])[0] if info.get("ranks") else None,
                    "ranks": info.get("ranks", []),
                    "count": info.get("count", len(info.get("ranks", [])) or 1),
                    "url": info.get("url", ""),
                    "date": latest_date.strftime("%Y-%m-%d"),
                })
        raw_items.sort(key=lambda item: item.get("rank") or 9999)

        return self._ok({
            "items": [self._format_news_item(item, include_url=include_url, collected_at=collected_at) for item in raw_items[:limit]],
            "generatedAt": utc_now_iso(),
        })

    def latest_rss(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        feeds = parse_list(get_query(query, "feeds"))
        days = parse_int(get_query(query, "days"), 1, "days", maximum=MAX_RSS_DAYS)
        limit = parse_int(get_query(query, "limit"), 50, "limit", maximum=MAX_LIMIT)
        include_summary = parse_bool(get_query(query, "includeSummary"), False)

        items = self._collect_rss_items(
            feeds=feeds,
            days=days,
            limit=limit,
            include_summary=include_summary,
        )
        return self._ok({
            "items": items,
            "generatedAt": utc_now_iso(),
        })

    def search_news(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        keyword = (get_query(query, "q") or "").strip()
        if not keyword:
            raise ApiError("BAD_REQUEST", "q is required", 400)

        days = parse_int(get_query(query, "days"), 7, "days", maximum=MAX_RSS_DAYS)
        platforms = parse_list(get_query(query, "platforms"))
        feeds = parse_list(get_query(query, "feeds"))
        include_rss = parse_bool(get_query(query, "includeRss"), True)
        limit = parse_int(get_query(query, "limit"), 50, "limit", maximum=MAX_LIMIT)
        rss_limit = parse_int(get_query(query, "rssLimit"), 20, "rssLimit", maximum=MAX_LIMIT)

        news_items = self._search_news_items(keyword, platforms, days, limit)
        rss_items = self._search_rss_items(keyword, feeds, days, rss_limit) if include_rss else []
        return self._ok({
            "query": keyword,
            "news": news_items,
            "rss": rss_items,
            "generatedAt": utc_now_iso(),
        })

    def trending_topics(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        top_n = parse_int(get_query(query, "topN"), 10, "topN", maximum=MAX_LIMIT)
        mode = get_query(query, "mode") or "current"
        extract_mode = get_query(query, "extractMode") or "keywords"
        if mode not in {"current", "daily"}:
            raise ApiError("BAD_REQUEST", "mode must be current or daily", 400)
        if extract_mode not in {"keywords", "auto_extract"}:
            raise ApiError("BAD_REQUEST", "extractMode must be keywords or auto_extract", 400)

        result = self._trending_topics_from_latest(top_n, mode, extract_mode)
        return self._ok({
            "topics": result,
            "generatedAt": utc_now_iso(),
        })

    def summary_report(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        report_type = get_query(query, "type") or "daily"
        if report_type not in {"daily", "weekly"}:
            raise ApiError("BAD_REQUEST", "type must be daily or weekly", 400)

        start = get_query(query, "start")
        end = get_query(query, "end")
        date_range = None
        if start or end:
            if not start or not end:
                raise ApiError("BAD_REQUEST", "start and end must be provided together", 400)
            date_range = {"start": start, "end": end}

        result = self.analytics_tools.generate_summary_report(report_type=report_type, date_range=date_range)
        if not result.get("success"):
            raise self._api_error_from_tool(result)
        return self._ok({
            "type": report_type,
            "markdown": result.get("markdown_report", ""),
            "generatedAt": utc_now_iso(),
        })

    def trigger_crawl(self, body: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        if self._last_crawl_trigger and now - self._last_crawl_trigger < CRAWL_MIN_INTERVAL_SECONDS:
            raise ApiError("RATE_LIMITED", "Crawl trigger is too frequent", 429)
        self._last_crawl_trigger = now

        platforms = body.get("platforms")
        if platforms is not None and not isinstance(platforms, list):
            raise ApiError("BAD_REQUEST", "platforms must be an array", 400)
        save_to_local = bool(body.get("saveToLocal", True))
        include_url = bool(body.get("includeUrl", True))

        result = self.system_tools.trigger_crawl(
            platforms=platforms,
            save_to_local=save_to_local,
            include_url=include_url,
        )
        if not result.get("success"):
            raise self._api_error_from_tool(result)

        summary = result.get("summary", {})
        success_platforms = [p for p in summary.get("platforms", []) if p not in summary.get("failed_platforms", [])]
        return self._ok({
            "message": "Crawl finished",
            "successPlatforms": success_platforms,
            "failedPlatforms": summary.get("failed_platforms", []),
            "generatedAt": utc_now_iso(),
        })

    def _latest_date(self, db_type: str) -> datetime:
        _, latest = self.data_service.get_available_date_range(db_type=db_type)
        if latest is None:
            raise ApiError("DATA_NOT_FOUND", f"No {db_type} data available", 503)
        return latest

    def _collect_rss_items(
        self,
        feeds: Optional[List[str]],
        days: int,
        limit: int,
        include_summary: bool,
    ) -> List[Dict[str, Any]]:
        latest_date = self._latest_date("rss")
        raw_items: List[Dict[str, Any]] = []
        seen_urls = set()

        for offset in range(days):
            target_date = latest_date - timedelta(days=offset)
            try:
                all_items, id_to_name, timestamps = self.data_service.parser.read_all_titles_for_date(
                    date=target_date,
                    platform_ids=feeds,
                    db_type="rss",
                )
            except DataNotFoundError:
                continue

            collected_at = self._latest_timestamp_iso(timestamps) or target_date.strftime("%Y-%m-%d")
            for feed_id, items in all_items.items():
                feed_name = id_to_name.get(feed_id, feed_id)
                for title, info in items.items():
                    url = info.get("url", "")
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    raw_items.append(self._format_rss_item(
                        {
                            "title": title,
                            "feed_id": feed_id,
                            "feed_name": feed_name,
                            "url": url,
                            "summary": info.get("summary", ""),
                            "published_at": info.get("published_at", ""),
                            "fetch_time": collected_at,
                        },
                        include_summary=include_summary,
                    ))

        raw_items.sort(key=lambda item: item.get("publishedAt") or "", reverse=True)
        return raw_items[:limit]

    def _search_news_items(
        self,
        keyword: str,
        platforms: Optional[List[str]],
        days: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        latest_date = self._latest_date("news")
        start_date = latest_date - timedelta(days=days - 1)
        matches: List[Dict[str, Any]] = []

        current_date = start_date
        while current_date <= latest_date:
            try:
                all_titles, id_to_name, timestamps = self.data_service.parser.read_all_titles_for_date(
                    date=current_date,
                    platform_ids=platforms,
                    db_type="news",
                )
            except DataNotFoundError:
                current_date += timedelta(days=1)
                continue

            collected_at = self._latest_timestamp_iso(timestamps) or current_date.strftime("%Y-%m-%d")
            for platform_id, titles in all_titles.items():
                platform_name = id_to_name.get(platform_id, platform_id)
                for title, info in titles.items():
                    if keyword.lower() not in title.lower():
                        continue
                    item = {
                        "title": title,
                        "platform": platform_id,
                        "platform_name": platform_name,
                        "rank": info.get("ranks", [None])[0] if info.get("ranks") else None,
                        "ranks": info.get("ranks", []),
                        "count": info.get("count", len(info.get("ranks", [])) or 1),
                        "url": info.get("url", ""),
                        "date": current_date.strftime("%Y-%m-%d"),
                    }
                    matches.append(self._format_news_item(item, include_url=True, collected_at=collected_at))

            current_date += timedelta(days=1)

        matches.sort(key=lambda item: (item.get("collectedAt") or "", -(item.get("rank") or 9999)), reverse=True)
        return matches[:limit]

    def _search_rss_items(
        self,
        keyword: str,
        feeds: Optional[List[str]],
        days: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        latest_date = self._latest_date("rss")
        results: List[Dict[str, Any]] = []
        seen_urls = set()

        for offset in range(days):
            target_date = latest_date - timedelta(days=offset)
            try:
                all_items, id_to_name, timestamps = self.data_service.parser.read_all_titles_for_date(
                    date=target_date,
                    platform_ids=feeds,
                    db_type="rss",
                )
            except DataNotFoundError:
                continue

            collected_at = self._latest_timestamp_iso(timestamps) or target_date.strftime("%Y-%m-%d")
            for feed_id, items in all_items.items():
                feed_name = id_to_name.get(feed_id, feed_id)
                for title, info in items.items():
                    summary = info.get("summary", "")
                    if keyword.lower() not in title.lower() and keyword.lower() not in summary.lower():
                        continue
                    url = info.get("url", "")
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    results.append(self._format_rss_item(
                        {
                            "title": title,
                            "feed_id": feed_id,
                            "feed_name": feed_name,
                            "url": url,
                            "summary": summary,
                            "published_at": info.get("published_at", ""),
                            "fetch_time": collected_at,
                        },
                        include_summary=True,
                    ))

        results.sort(key=lambda item: item.get("publishedAt") or "", reverse=True)
        return results[:limit]

    def _trending_topics_from_latest(self, top_n: int, mode: str, extract_mode: str) -> List[Dict[str, Any]]:
        from collections import Counter, defaultdict

        latest_date = self._latest_date("news")
        try:
            all_titles, _, _ = self.data_service.parser.read_all_titles_for_date(date=latest_date)
        except DataNotFoundError as exc:
            raise ApiError(exc.code, exc.message, 503)

        word_frequency = Counter()
        platforms_by_keyword: Dict[str, set] = defaultdict(set)

        if extract_mode == "keywords":
            from trendradar.core.frequency import _word_matches

            word_groups = self.data_service.parser.parse_frequency_words()
            for platform_id, titles in all_titles.items():
                for title in titles.keys():
                    title_lower = title.lower()
                    for group in word_groups:
                        all_words = group.get("required", []) + group.get("normal", [])
                        if any(_word_matches(word_config, title_lower) for word_config in all_words):
                            display_key = group.get("display_name") or group.get("group_key", "")
                            word_frequency[display_key] += 1
                            platforms_by_keyword[display_key].add(platform_id)
                            break
        else:
            for platform_id, titles in all_titles.items():
                for title in titles.keys():
                    for word in self.data_service._extract_words_from_title(title):
                        word_frequency[word] += 1
                        platforms_by_keyword[word].add(platform_id)

        topics = []
        max_count = max(word_frequency.values(), default=1)
        for keyword, count in word_frequency.most_common(top_n):
            topics.append({
                "keyword": keyword,
                "count": count,
                "weight": round(count / max_count * 100, 2),
                "platforms": sorted(platforms_by_keyword.get(keyword, [])),
            })
        return topics

    def _format_news_item(self, item: Dict[str, Any], include_url: bool, collected_at: Any = None) -> Dict[str, Any]:
        platform_id = item.get("platform") or item.get("platform_id") or ""
        rank = item.get("rank")
        title = item.get("title") or ""
        url = item.get("url") or ""
        formatted = {
            "id": f"{platform_id}:{stable_id(item.get('date'), title, url)}",
            "title": title,
            "platformId": platform_id,
            "platformName": item.get("platform_name") or item.get("platformName") or platform_id,
            "rank": rank,
            "summary": item.get("summary"),
            "publishedAt": normalize_iso(item.get("published_at") or item.get("publishedAt")),
            "collectedAt": normalize_iso(collected_at or item.get("timestamp") or item.get("date")),
            "weight": self._news_weight(item),
        }
        if include_url:
            formatted["url"] = url
        return formatted

    def _format_rss_item(self, item: Dict[str, Any], include_summary: bool) -> Dict[str, Any]:
        feed_id = item.get("feed_id") or item.get("feedId") or ""
        title = item.get("title") or ""
        url = item.get("url") or ""
        formatted = {
            "id": f"{feed_id}:{stable_id(title, url, item.get('published_at'))}",
            "title": title,
            "feedId": feed_id,
            "feedName": item.get("feed_name") or item.get("feedName") or feed_id,
            "url": url,
            "summary": item.get("summary", "") if include_summary else None,
            "publishedAt": normalize_iso(item.get("published_at") or item.get("publishedAt")),
            "collectedAt": normalize_iso(item.get("fetch_time") or item.get("collectedAt") or item.get("date")),
        }
        return formatted

    def _news_weight(self, item: Dict[str, Any]) -> float:
        ranks = item.get("ranks")
        if ranks:
            return round(float(calculate_news_weight({"ranks": ranks, "count": item.get("count", len(ranks))})), 2)
        rank = item.get("rank")
        if isinstance(rank, (int, float)) and rank > 0:
            return round(max(1.0, 101.0 - float(rank)), 2)
        return 0.0

    def _latest_timestamp_iso(self, timestamps: Dict[str, float]) -> Optional[str]:
        if not timestamps:
            return None
        return normalize_iso(datetime.fromtimestamp(max(timestamps.values())).strftime("%Y-%m-%d %H:%M:%S"))

    def _api_error_from_tool(self, result: Dict[str, Any]) -> ApiError:
        error = result.get("error") or {}
        code = error.get("code", "INTERNAL_ERROR")
        message = error.get("message", "Internal server error")
        status = 400 if code in {"INVALID_PARAMETER", "BAD_REQUEST"} else 500
        return ApiError(code, message, status)

    def _ok(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "data": data}


def make_handler(api: TrendRadarApi):
    class TrendRadarRequestHandler(BaseHTTPRequestHandler):
        server_version = "TrendRadarApi/1.0"

        def do_OPTIONS(self) -> None:
            self._send_json(200, {"ok": True})

        def do_GET(self) -> None:
            self._handle_request("GET")

        def do_POST(self) -> None:
            self._handle_request("POST")

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[TrendRadar API] {self.address_string()} - {fmt % args}")

        def _handle_request(self, method: str) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/health" and method == "GET":
                    self._send_json(200, api.health())
                    return

                if parsed.path.startswith("/api/"):
                    api.check_auth(self.headers)

                if method == "GET" and parsed.path == "/api/news/latest":
                    self._send_json(200, api.latest_news(query))
                elif method == "GET" and parsed.path == "/api/rss/latest":
                    self._send_json(200, api.latest_rss(query))
                elif method == "GET" and parsed.path == "/api/news/search":
                    self._send_json(200, api.search_news(query))
                elif method == "GET" and parsed.path == "/api/topics/trending":
                    self._send_json(200, api.trending_topics(query))
                elif method == "GET" and parsed.path == "/api/reports/summary":
                    self._send_json(200, api.summary_report(query))
                elif method == "POST" and parsed.path == "/api/crawl/trigger":
                    self._send_json(200, api.trigger_crawl(self._read_json_body()))
                else:
                    self._send_error(404, "NOT_FOUND", "API not found")
            except ApiError as exc:
                self._send_error(exc.status, exc.code, exc.message)
            except MCPError as exc:
                self._send_error(400, exc.code, exc.message)
            except Exception as exc:
                self._send_error(500, "INTERNAL_ERROR", str(exc))

        def _read_json_body(self) -> Dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0") or 0)
            if content_length <= 0:
                return {}
            raw = self.rfile.read(content_length).decode("utf-8")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                raise ApiError("BAD_REQUEST", "Request body must be valid JSON", 400)
            if not isinstance(body, dict):
                raise ApiError("BAD_REQUEST", "Request body must be a JSON object", 400)
            return body

        def _send_error(self, status: int, code: str, message: str) -> None:
            self._send_json(status, {"ok": False, "error": {"code": code, "message": message}})

        def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if api.cors_origin:
                self.send_header("Access-Control-Allow-Origin", api.cors_origin)
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

    return TrendRadarRequestHandler


def run_api(host: str, port: int, project_root: Optional[str], token: str, cors_origin: str) -> None:
    api = TrendRadarApi(project_root=project_root, token=token, cors_origin=cors_origin)
    if not token:
        print("[TrendRadar API] WARNING: TRENDRADAR_API_TOKEN is empty. Use this only for local development.")
    print(f"[TrendRadar API] Project root: {api.project_root}")
    print(f"[TrendRadar API] Listening on http://{host}:{port}")
    server = ThreadingHTTPServer((host, port), make_handler(api))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[TrendRadar API] Shutting down")
    finally:
        server.server_close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start TrendRadar REST API")
    parser.add_argument("--host", default=os.environ.get("TRENDRADAR_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TRENDRADAR_API_PORT", "3334")))
    parser.add_argument("--project-root", default=os.environ.get("TRENDRADAR_PROJECT_ROOT", os.getcwd()))
    parser.add_argument("--token", default=os.environ.get("TRENDRADAR_API_TOKEN", ""))
    parser.add_argument("--cors-origin", default=os.environ.get("TRENDRADAR_API_CORS_ORIGIN", ""))
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    run_api(
        host=args.host,
        port=args.port,
        project_root=args.project_root,
        token=args.token,
        cors_origin=args.cors_origin,
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser

from ..types import Fragment, SourceType, iso_now
from ..utils import isoformat_utc, strip_html
from .base import Collector


@dataclass(frozen=True)
class RssFeed:
    url: str
    source_name: str
    source_type: SourceType
    lang: str | None = None  # "ja"/"en" など（任意）


class RssCollector(Collector):
    def __init__(self, feed: RssFeed):
        self._feed = feed

    @property
    def source_type(self) -> SourceType:
        return self._feed.source_type

    @property
    def source_name(self) -> str:
        return self._feed.source_name

    def collect(
        self,
        *,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[Fragment]:
        parsed = feedparser.parse(self._feed.url)
        fetched_at = iso_now()

        out: list[Fragment] = []
        for entry in parsed.entries[: max(limit, 0)]:
            url = _entry_url(entry)
            if not url:
                continue

            published_dt = _entry_published_dt(entry)
            if published_dt is not None:
                if since is not None and published_dt < since:
                    continue
                if until is not None and published_dt > until:
                    continue

            title = strip_html(str(entry.get("title", "")).strip()) or None
            summary = _entry_text(entry)
            text = _compose_text(title=title, summary=summary)

            out.append(
                Fragment(
                    source_type=self._feed.source_type,
                    source_name=self._feed.source_name,
                    published_at=isoformat_utc(published_dt) if published_dt else None,
                    url=url,
                    text=text,
                    title=title,
                    lang=self._feed.lang,
                    fetched_at=fetched_at,
                )
            )
        return out


def _entry_url(entry: dict[str, Any]) -> str | None:
    for k in ("link", "id", "guid"):
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _entry_published_dt(entry: dict[str, Any]) -> datetime | None:
    # feedparserが struct_time を持っていればそれを優先
    for k in ("published_parsed", "updated_parsed"):
        st = entry.get(k)
        if st:
            try:
                dt = datetime(*st[:6], tzinfo=timezone.utc)
                return dt
            except Exception:
                pass

    # published/updated文字列も試す（RFC 2822/3339 など）
    for k in ("published", "updated"):
        s = entry.get(k)
        if isinstance(s, str) and s.strip():
            try:
                dt = parsedate_to_datetime(s.strip())
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                continue
    return None


def _entry_text(entry: dict[str, Any]) -> str:
    # Atom/RSSでよくあるフィールドを順に試す（HTML除去）
    content = entry.get("content")
    if isinstance(content, list) and content:
        v = content[0].get("value")
        if isinstance(v, str) and v.strip():
            return strip_html(v)

    summary = entry.get("summary")
    if isinstance(summary, str) and summary.strip():
        return strip_html(summary)

    description = entry.get("description")
    if isinstance(description, str) and description.strip():
        return strip_html(description)

    return ""


def _compose_text(*, title: str | None, summary: str) -> str:
    summary = summary.strip()
    if title and summary and summary.lower() != title.lower():
        return f"{title}\n\n{summary}"
    if title:
        return title
    if summary:
        return summary
    return ""


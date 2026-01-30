from __future__ import annotations

import html
import re
from datetime import datetime, timezone


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    # 雑にタグを落とし、HTMLエンティティを解決する（MVP）。
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def isoformat_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso_or_none(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # 例: 2026-01-23T00:00:00Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


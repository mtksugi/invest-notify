"""銘柄別ニュース取得（定性レイヤーの入力）.

FMP ``/stable/news/stock`` を使う（Starter プラン可）。
直近 ``max_age_days`` のニュースだけを、LLM に渡しやすい最小形へ整形する。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .fmp import FmpConfig, fmp_stock_news


def _parse_dt(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt) + 2] if "%H" in fmt else s[:10], fmt).replace(
                tzinfo=timezone.utc
            )
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def recent_news(
    cfg: FmpConfig,
    *,
    ticker: str,
    limit: int = 15,
    max_items: int = 8,
    max_age_days: int = 21,
    snippet_chars: int = 280,
) -> list[dict[str, Any]]:
    """直近のニュースを新しい順に最大 ``max_items`` 件、最小形で返す.

    returns: ``[{date, title, site, url, text}]``
    """
    raw = fmp_stock_news(cfg, ticker=ticker, limit=limit)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out: list[tuple[datetime, dict[str, Any]]] = []
    for r in raw:
        dt = _parse_dt(r.get("publishedDate"))
        if dt is None or dt < cutoff:
            continue
        text = str(r.get("text") or "").strip()
        out.append(
            (
                dt,
                {
                    "date": (r.get("publishedDate") or "")[:10],
                    "title": str(r.get("title") or "").strip(),
                    "site": str(r.get("site") or r.get("publisher") or "").strip(),
                    "url": str(r.get("url") or "").strip(),
                    "text": text[:snippet_chars],
                },
            )
        )
    out.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in out[:max_items]]

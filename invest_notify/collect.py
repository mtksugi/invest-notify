from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .collectors.base import chain_collectors
from .collectors.rss import RssCollector
from .config import load_config
from .preprocess import apply_limits


def collect_fragments(
    *,
    config_path: str | Path,
    lookback_hours: int = 24,
    per_collector_limit: int = 500,
) -> list[dict]:
    """
    仕様書「7.1 入力：情報断片」のJSON配列を生成する。
    - 収集期間は (now - lookback_hours) 〜 now
    - 200件上限/重複除去/配分は apply_limits に委譲
    """

    cfg = load_config(config_path)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    since = now - timedelta(hours=int(lookback_hours))

    collectors = [RssCollector(feed) for feed in cfg.rss_feeds]
    fragments = chain_collectors(
        collectors,
        since=since,
        until=now,
        per_collector_limit=int(per_collector_limit),
    )

    fragments = apply_limits(fragments, limits=cfg.limits)
    return [f.to_dict() for f in fragments]


def write_fragments_json(
    *,
    fragments: list[dict],
    out_path: str | Path,
) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fragments, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


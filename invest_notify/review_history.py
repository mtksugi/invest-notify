from __future__ import annotations

"""
過去の notifications.json を一括レビューするツール。

- テキスト proxy（後追い表現ヒット率）
- 株価バックテスト（Yahoo Finance v8/finance/chart）で「本当に初動を捉えていたか」
- 旧ランク（confidence 順） vs 新ランク（_priority_score 順）の KPI 比較

を出力する。
"""

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ai.stage2 import _priority_score
from .price_backtest import (
    PriceSeries,
    classify_capture,
    compute_returns_for_notification,
    fetch_price_series,
)
from .signal_lexicon import has_late_reaction, has_structure_marker


def _parse_dt(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _text_for(n: dict[str, Any]) -> str:
    parts: list[str] = []
    s = n.get("summary")
    if isinstance(s, str):
        parts.append(s)
    for e in (n.get("evidence") or [])[:5]:
        if isinstance(e, dict):
            title = e.get("title")
            if isinstance(title, str) and title:
                parts.append(title)
    return "\n".join(parts)


def _evidence_freshness_days(n: dict[str, Any]) -> float | None:
    base = _parse_dt(n.get("event_time")) or _parse_dt(n.get("generated_at"))
    if base is None:
        return None
    best: float | None = None
    for e in (n.get("evidence") or [])[:10]:
        if not isinstance(e, dict):
            continue
        pub = _parse_dt(e.get("published_at"))
        if pub is None:
            continue
        diff = (base - pub).total_seconds() / 86400.0
        if best is None or diff < best:
            best = diff
    return best


@dataclass
class NotifRecord:
    day: str
    notif: dict[str, Any]
    pre_return: float | None = None
    post_return: float | None = None
    pre_signed: float | None = None
    post_signed: float | None = None
    capture: str = "unknown"  # early_capture / late_chase / missed / flat / unknown
    hit: bool | None = None


def _load_history(
    history_dir: Path, *, prefer_raw_pool: bool = False
) -> tuple[list[NotifRecord], list[list[NotifRecord]]]:
    """
    履歴ディレクトリを読む。戻り値:
    - 全通知フラットリスト
    - 日別リスト（rank_compare 用、各要素は「その日のプール」）

    prefer_raw_pool=True のときは、同日に notifications_pool.json があれば raw_notifications を、
    なければ notifications.json を使う。
    """
    flat: list[NotifRecord] = []
    daily: list[list[NotifRecord]] = []

    # history_dir 直下に日付ディレクトリがある構造、または単一ディレクトリ直下に notifications.json の構造を許容
    day_dirs = [p for p in history_dir.iterdir() if p.is_dir()]
    if not day_dirs:
        # 単一ディレクトリ fallback
        day_dirs = [history_dir]

    for day_dir in sorted(day_dirs):
        day_name = day_dir.name
        notifs: list[dict[str, Any]] | None = None
        if prefer_raw_pool:
            pool_p = day_dir / "notifications_pool.json"
            if pool_p.exists():
                try:
                    obj = json.loads(pool_p.read_text(encoding="utf-8"))
                    notifs = obj.get("raw_notifications") if isinstance(obj, dict) else None
                except Exception:
                    notifs = None
        if notifs is None:
            n_p = day_dir / "notifications.json"
            if not n_p.exists():
                continue
            try:
                obj = json.loads(n_p.read_text(encoding="utf-8"))
            except Exception:
                continue
            notifs = obj.get("notifications") if isinstance(obj, dict) else None
        if not isinstance(notifs, list):
            continue
        day_records: list[NotifRecord] = []
        for n in notifs:
            if not isinstance(n, dict):
                continue
            rec = NotifRecord(day=day_name, notif=n)
            flat.append(rec)
            day_records.append(rec)
        if day_records:
            daily.append(day_records)
    return flat, daily


def _signed_returns(n: dict[str, Any], pre: float | None, post: float | None) -> tuple[float | None, float | None]:
    impact = (n.get("impact_direction") or "").strip().lower()
    if pre is None or post is None:
        return pre, post
    if impact == "negative":
        return -pre, -post
    return pre, post


def _run_backtest(
    records: list[NotifRecord],
    *,
    cache_dir: Path | None,
    pre_window_days: int,
    post_window_days: int,
    rise_threshold: float,
    early_pre_band: float,
    fetch_sleep: float,
    price_start: datetime,
    price_end: datetime,
) -> None:
    tickers = sorted({str(r.notif.get("ticker") or "").strip() for r in records if r.notif.get("ticker")})
    series_by_ticker: dict[str, PriceSeries | None] = {}
    for i, t in enumerate(tickers):
        series_by_ticker[t] = fetch_price_series(
            t, start=price_start, end=price_end, cache_dir=cache_dir, sleep_seconds=fetch_sleep
        )
        if (i + 1) % 25 == 0:
            print(f"  [backtest] fetched {i+1}/{len(tickers)} tickers", flush=True)

    for r in records:
        t = str(r.notif.get("ticker") or "").strip()
        if not t:
            continue
        ser = series_by_ticker.get(t)
        if ser is None or not ser.closes:
            continue
        ev_s = r.notif.get("event_time") or r.notif.get("generated_at")
        ev = _parse_dt(ev_s)
        if not ev:
            continue
        rr = compute_returns_for_notification(
            series=ser,
            event_dt=ev,
            pre_window_days=pre_window_days,
            post_window_days=post_window_days,
        )
        r.pre_return = rr.pre_return
        r.post_return = rr.post_return
        pre_s, post_s = _signed_returns(r.notif, rr.pre_return, rr.post_return)
        r.pre_signed = pre_s
        r.post_signed = post_s
        r.capture = classify_capture(
            pre_return=pre_s,
            post_return=post_s,
            rise_threshold=rise_threshold,
            early_pre_band=early_pre_band,
        )
        if post_s is not None:
            r.hit = post_s > 0


def _summarize(records: Iterable[NotifRecord]) -> dict[str, Any]:
    rs = [r for r in records if r.capture != "unknown"]
    n = len(rs)
    if n == 0:
        return {"count": 0}
    cls_count: dict[str, int] = {}
    for r in rs:
        cls_count[r.capture] = cls_count.get(r.capture, 0) + 1
    pre_s = [r.pre_signed for r in rs if r.pre_signed is not None]
    post_s = [r.post_signed for r in rs if r.post_signed is not None]
    hits = [r.hit for r in rs if r.hit is not None]
    return {
        "count": n,
        "early_capture_rate": cls_count.get("early_capture", 0) / n,
        "late_chase_rate": cls_count.get("late_chase", 0) / n,
        "missed_rate": cls_count.get("missed", 0) / n,
        "flat_rate": cls_count.get("flat", 0) / n,
        "hit_rate": (sum(1 for x in hits if x) / len(hits)) if hits else None,
        "mean_pre_return_signed": statistics.mean(pre_s) if pre_s else None,
        "mean_post_return_signed": statistics.mean(post_s) if post_s else None,
        "median_post_return_signed": statistics.median(post_s) if post_s else None,
    }


def _group_summary(records: list[NotifRecord], key_fn) -> dict[str, Any]:
    groups: dict[str, list[NotifRecord]] = {}
    for r in records:
        k = str(key_fn(r.notif) or "(none)")
        groups.setdefault(k, []).append(r)
    return {k: _summarize(rs) for k, rs in groups.items()}


def _rank_compare(
    daily: list[list[NotifRecord]],
    *,
    max_confirmed: int,
    max_early_warning: int,
) -> dict[str, Any]:
    """
    同日プールに対して (旧: confidence順, 新: _priority_score順) で
    それぞれ lane 別に上位 N 件を採用し、KPI を比較する。
    """

    def pick_by(records: list[NotifRecord], key_fn) -> list[NotifRecord]:
        confirmed = [r for r in records if r.notif.get("lane") == "confirmed"]
        early = [r for r in records if r.notif.get("lane") == "early_warning"]
        confirmed.sort(key=lambda r: key_fn(r.notif), reverse=True)
        early.sort(key=lambda r: key_fn(r.notif), reverse=True)
        return confirmed[:max_confirmed] + early[:max_early_warning]

    def conf_key(n: dict[str, Any]) -> float:
        try:
            return float(n.get("confidence") or 0.0)
        except Exception:
            return 0.0

    old_picked: list[NotifRecord] = []
    new_picked: list[NotifRecord] = []
    for day in daily:
        old_picked.extend(pick_by(day, conf_key))
        new_picked.extend(pick_by(day, _priority_score))

    return {
        "old_confidence_rank": _summarize(old_picked),
        "new_priority_rank": _summarize(new_picked),
    }


def review_history(
    *,
    history_dir: Path,
    out_path: Path,
    max_confirmed: int = 3,
    max_early_warning: int = 3,
    backtest: bool = False,
    cache_dir: Path | None = None,
    pre_window_days: int = 5,
    post_window_days: int = 10,
    rise_threshold: float = 0.05,
    early_pre_band: float = 0.03,
    fetch_sleep: float = 0.2,
    prefer_raw_pool: bool = False,
    price_lookback_days: int = 40,
    price_lookahead_days: int = 20,
) -> dict[str, Any]:
    flat, daily = _load_history(history_dir, prefer_raw_pool=prefer_raw_pool)
    print(f"[review-history] loaded notifications={len(flat)} days={len(daily)}")

    # 共通: テキスト proxy 指標
    late_hit = sum(1 for r in flat if has_late_reaction(_text_for(r.notif)))
    struct_hit = sum(1 for r in flat if has_structure_marker(_text_for(r.notif)))
    fresh_vals = [_evidence_freshness_days(r.notif) for r in flat]
    fresh_vals_f = [v for v in fresh_vals if v is not None]

    summary: dict[str, Any] = {
        "history_dir": str(history_dir),
        "total_notifications": len(flat),
        "days": len(daily),
        "text_proxy": {
            "late_reaction_ratio": (late_hit / len(flat)) if flat else 0.0,
            "structure_marker_ratio": (struct_hit / len(flat)) if flat else 0.0,
            "evidence_freshness_median_days": (
                statistics.median(fresh_vals_f) if fresh_vals_f else None
            ),
        },
        "distribution": {
            "by_category": _count_key(flat, lambda n: n.get("category")),
            "by_lane": _count_key(flat, lambda n: n.get("lane")),
            "by_impact_direction": _count_key(flat, lambda n: n.get("impact_direction")),
            "by_ticker_top20": _count_key_top(flat, lambda n: n.get("ticker"), top=20),
        },
    }

    if backtest:
        # event_time の min/max を見て、適切な株価取得期間を決める
        ev_times: list[datetime] = []
        for r in flat:
            dt = _parse_dt(r.notif.get("event_time") or r.notif.get("generated_at"))
            if dt:
                ev_times.append(dt)
        if ev_times:
            price_start = min(ev_times).astimezone(timezone.utc) - _days(price_lookback_days)
            price_end = max(ev_times).astimezone(timezone.utc) + _days(price_lookahead_days)
        else:
            now = datetime.now(timezone.utc)
            price_start = now - _days(90)
            price_end = now
        print(
            f"[review-history] backtest range {price_start.date()} - {price_end.date()} "
            f"(pre={pre_window_days}d, post={post_window_days}d, rise={rise_threshold:.2%})"
        )
        _run_backtest(
            flat,
            cache_dir=cache_dir,
            pre_window_days=pre_window_days,
            post_window_days=post_window_days,
            rise_threshold=rise_threshold,
            early_pre_band=early_pre_band,
            fetch_sleep=fetch_sleep,
            price_start=price_start,
            price_end=price_end,
        )
        summary["backtest"] = {
            "params": {
                "pre_window_days": pre_window_days,
                "post_window_days": post_window_days,
                "rise_threshold": rise_threshold,
                "early_pre_band": early_pre_band,
            },
            "overall": _summarize(flat),
            "by_category": _group_summary(flat, lambda n: n.get("category")),
            "by_lane": _group_summary(flat, lambda n: n.get("lane")),
            "by_impact_direction": _group_summary(flat, lambda n: n.get("impact_direction")),
            "by_source_types": _group_summary(
                flat,
                lambda n: ",".join(sorted(set(str(x) for x in (n.get("source_types") or [])))) or "(none)",
            ),
            "rank_compare": _rank_compare(
                daily, max_confirmed=max_confirmed, max_early_warning=max_early_warning
            ),
            "late_chase_examples": [
                {
                    "day": r.day,
                    "ticker": r.notif.get("ticker"),
                    "category": r.notif.get("category"),
                    "impact_direction": r.notif.get("impact_direction"),
                    "lane": r.notif.get("lane"),
                    "pre_signed": r.pre_signed,
                    "post_signed": r.post_signed,
                    "summary": (r.notif.get("summary") or "")[:160],
                }
                for r in flat
                if r.capture == "late_chase"
            ][:30],
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[review-history] wrote -> {out_path}")
    return summary


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def _count_key(records: list[NotifRecord], key_fn) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        k = str(key_fn(r.notif) or "(none)")
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


def _count_key_top(records: list[NotifRecord], key_fn, *, top: int) -> dict[str, int]:
    full = _count_key(records, key_fn)
    return dict(list(full.items())[:top])

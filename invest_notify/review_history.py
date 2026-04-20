from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ai.stage2 import _cap_notifications
from .signal_lexicon import LATE_REACTION_PATTERNS, STRUCTURE_MARKERS


def _load_daily_notifications(history_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for day_dir in sorted(history_dir.glob("*/")):
        p = day_dir / "notifications.json"
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        notifs = obj.get("notifications", [])
        if not isinstance(notifs, list):
            continue
        day = day_dir.name
        for n in notifs:
            if isinstance(n, dict):
                rows.append((day, n))
    return rows


def _notif_text(n: dict[str, Any]) -> str:
    parts: list[str] = [str(n.get("summary") or "")]
    ev = n.get("evidence")
    if isinstance(ev, list):
        for e in ev[:5]:
            if isinstance(e, dict):
                parts.append(str(e.get("title") or ""))
    return "\n".join(parts).lower()


def _parse_iso_to_utc(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    ss = s.strip()
    if ss.endswith("Z"):
        ss = ss[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ss)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _freshness_days_from_evidence(n: dict[str, Any]) -> float | None:
    g = _parse_iso_to_utc(n.get("generated_at")) or _parse_iso_to_utc(n.get("event_time"))
    ev = n.get("evidence")
    if g is None or not isinstance(ev, list) or not ev:
        return None
    ages: list[float] = []
    for e in ev:
        if not isinstance(e, dict):
            continue
        p = _parse_iso_to_utc(e.get("published_at"))
        if p is None:
            continue
        ages.append((g - p).total_seconds() / 86400.0)
    if not ages:
        return None
    return min(ages)


def review_history(history_dir: str | Path) -> dict[str, Any]:
    p = Path(history_dir)
    rows = _load_daily_notifications(p)
    by_day: defaultdict[str, int] = defaultdict(int)
    by_category: Counter[str] = Counter()
    by_lane: Counter[str] = Counter()
    by_impact: Counter[str] = Counter()
    by_ticker: Counter[str] = Counter()

    late_like = 0
    structure_like = 0
    early_positive_like = 0
    examples_late: list[dict[str, Any]] = []
    age_days_all: list[float] = []
    age_days_late: list[float] = []

    by_day_notifs: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    compiled = [re.compile(x, flags=re.IGNORECASE) for x in LATE_REACTION_PATTERNS]

    for day, n in rows:
        by_day[day] += 1
        cat = str(n.get("category") or "")
        lane = str(n.get("lane") or "")
        impact = str(n.get("impact_direction") or "")
        ticker = str(n.get("ticker") or "")
        by_category[cat] += 1
        by_lane[lane] += 1
        by_impact[impact] += 1
        by_day_notifs[day].append(n)
        if ticker:
            by_ticker[ticker] += 1

        text = _notif_text(n)
        is_late = any(r.search(text) for r in compiled)
        age = _freshness_days_from_evidence(n)
        if age is not None:
            age_days_all.append(age)
        if is_late:
            late_like += 1
            if age is not None:
                age_days_late.append(age)
            if len(examples_late) < 10:
                examples_late.append(
                    {
                        "day": day,
                        "ticker": ticker,
                        "lane": lane,
                        "category": cat,
                        "summary_head": str(n.get("summary") or "")[:120],
                    }
                )
        if any(m.lower() in text for m in STRUCTURE_MARKERS):
            structure_like += 1
        if lane == "early_warning" and impact in ("positive", "mixed"):
            early_positive_like += 1

    baseline_total = 0
    baseline_late = 0
    reranked_total = 0
    reranked_late = 0
    old_cat: Counter[str] = Counter()
    new_cat: Counter[str] = Counter()
    old_cat_late: Counter[str] = Counter()
    new_cat_late: Counter[str] = Counter()
    old_ticker: Counter[str] = Counter()
    new_ticker: Counter[str] = Counter()
    for _, day_notifs in by_day_notifs.items():
        # 旧相当: laneごとにconfidence順（dedupeあり）
        def _conf(n: dict[str, Any]) -> float:
            try:
                return float(n.get("confidence") or 0.0)
            except Exception:
                return 0.0

        confirmed = sorted([n for n in day_notifs if n.get("lane") == "confirmed"], key=_conf, reverse=True)[:3]
        early = sorted([n for n in day_notifs if n.get("lane") == "early_warning"], key=_conf, reverse=True)[:3]
        old_pick: list[dict[str, Any]] = []
        seen_old: set[str] = set()
        for n in confirmed + early:
            k = f"{str(n.get('ticker') or '').strip()}:{str(n.get('category') or '').strip()}"
            if not k or k in seen_old:
                continue
            seen_old.add(k)
            old_pick.append(n)
        new_pick = _cap_notifications(
            day_notifs,
            max_confirmed=3,
            max_early_warning=3,
            watch_tickers=None,
            max_watch=0,
        )
        baseline_total += len(old_pick)
        reranked_total += len(new_pick)
        for n in old_pick:
            cat = str(n.get("category") or "")
            tk = str(n.get("ticker") or "")
            old_cat[cat] += 1
            if tk:
                old_ticker[tk] += 1
            is_late_old = any(r.search(_notif_text(n)) for r in compiled)
            if is_late_old:
                baseline_late += 1
                old_cat_late[cat] += 1
        for n in new_pick:
            cat = str(n.get("category") or "")
            tk = str(n.get("ticker") or "")
            new_cat[cat] += 1
            if tk:
                new_ticker[tk] += 1
            is_late_new = any(r.search(_notif_text(n)) for r in compiled)
            if is_late_new:
                reranked_late += 1
                new_cat_late[cat] += 1

    total = len(rows)
    days = len(by_day)
    result: dict[str, Any] = {
        "days": days,
        "notifications_total": total,
        "avg_notifications_per_day": (total / days if days else 0.0),
        "zero_notification_days": sum(1 for _, c in by_day.items() if c == 0),
        "category_counts": dict(by_category),
        "lane_counts": dict(by_lane),
        "impact_counts": dict(by_impact),
        "top_tickers": [{"ticker": t, "count": c} for t, c in by_ticker.most_common(20)],
        "initial_move_capture_proxy": {
            "late_reaction_ratio": (late_like / total if total else 0.0),
            "structure_signal_ratio": (structure_like / total if total else 0.0),
            "early_positive_or_mixed_ratio": (early_positive_like / total if total else 0.0),
            "evidence_freshness_days_median": (
                sorted(age_days_all)[len(age_days_all) // 2] if age_days_all else None
            ),
            "evidence_freshness_days_median_late_only": (
                sorted(age_days_late)[len(age_days_late) // 2] if age_days_late else None
            ),
            "late_ratio_old_rank_proxy": (baseline_late / baseline_total if baseline_total else 0.0),
            "late_ratio_new_rank_proxy": (reranked_late / reranked_total if reranked_total else 0.0),
        },
        "category_mix_compare": {
            "old_rank_category_counts": dict(old_cat),
            "new_rank_category_counts": dict(new_cat),
            "old_rank_late_by_category": dict(old_cat_late),
            "new_rank_late_by_category": dict(new_cat_late),
        },
        "ticker_diversity_compare": {
            "old_unique_tickers": len(old_ticker),
            "new_unique_tickers": len(new_ticker),
            "old_top_ticker_share": (
                max(old_ticker.values()) / baseline_total if (old_ticker and baseline_total) else 0.0
            ),
            "new_top_ticker_share": (
                max(new_ticker.values()) / reranked_total if (new_ticker and reranked_total) else 0.0
            ),
            "old_top_tickers": [{"ticker": t, "count": c} for t, c in old_ticker.most_common(10)],
            "new_top_tickers": [{"ticker": t, "count": c} for t, c in new_ticker.most_common(10)],
        },
        "late_breakdown": {
            "by_category": dict(Counter(str(r.get("category") or "") for _, r in rows if any(c.search(_notif_text(r)) for c in compiled))),
            "by_ticker_top10": [
                {"ticker": t, "count": c}
                for t, c in Counter(
                    str(r.get("ticker") or "")
                    for _, r in rows
                    if any(c.search(_notif_text(r)) for c in compiled)
                ).most_common(10)
            ],
        },
        "examples_late_reaction": examples_late,
    }
    return result


from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


LATE_PATTERNS = [
    r"すでに",
    r"既に",
    r"急騰",
    r"上昇している",
    r"上昇を受け",
    r"株価.*上昇",
    r"already",
    r"rall(y|ied)",
    r"surged?",
    r"priced in",
]

STRUCTURE_MARKERS = [
    "guidance",
    "ガイダンス",
    "修正",
    "revise",
    "contract",
    "契約",
    "agreement",
    "規制",
    "regulation",
    "supply",
    "供給",
    "dilution",
    "希薄化",
    "buyback",
    "自社株買い",
]


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

    compiled = [re.compile(x, flags=re.IGNORECASE) for x in LATE_PATTERNS]

    for day, n in rows:
        by_day[day] += 1
        cat = str(n.get("category") or "")
        lane = str(n.get("lane") or "")
        impact = str(n.get("impact_direction") or "")
        ticker = str(n.get("ticker") or "")
        by_category[cat] += 1
        by_lane[lane] += 1
        by_impact[impact] += 1
        if ticker:
            by_ticker[ticker] += 1

        text = _notif_text(n)
        is_late = any(r.search(text) for r in compiled)
        if is_late:
            late_like += 1
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
        },
        "examples_late_reaction": examples_late,
    }
    return result


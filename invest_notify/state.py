from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SentEvent:
    event_id: str  # f"{ticker}:{category}"
    sent_at: str  # ISO8601


def load_state(path: str | Path) -> list[SentEvent]:
    p = Path(path)
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8") or "[]")
    out: list[SentEvent] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("event_id"), str) and isinstance(item.get("sent_at"), str):
                out.append(SentEvent(event_id=item["event_id"], sent_at=item["sent_at"]))
    return out


def save_state(path: str | Path, events: list[SentEvent]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps([{"event_id": e.event_id, "sent_at": e.sent_at} for e in events], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def filter_recently_sent(
    notifications: list[dict[str, Any]],
    *,
    state: list[SentEvent],
    window_days: int = 3,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    returns: (allowed, suppressed)
    """
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    window = timedelta(days=int(window_days))

    cutoff = now - window
    recent_ids: set[str] = set()
    for e in state:
        dt = _parse_iso(e.sent_at)
        if dt and dt >= cutoff:
            recent_ids.add(e.event_id)

    allowed: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for n in notifications:
        ticker = str(n.get("ticker") or "").strip()
        cat = str(n.get("category") or "").strip()
        eid = f"{ticker}:{cat}"
        if eid in recent_ids:
            suppressed.append(n)
        else:
            allowed.append(n)
    return allowed, suppressed


def update_state_with_sent(
    state: list[SentEvent],
    notifications_sent: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[SentEvent]:
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now.isoformat()
    out = list(state)
    for n in notifications_sent:
        ticker = str(n.get("ticker") or "").strip()
        cat = str(n.get("category") or "").strip()
        if ticker and cat:
            out.append(SentEvent(event_id=f"{ticker}:{cat}", sent_at=now_iso))
    return out


def _parse_iso(s: str) -> datetime | None:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


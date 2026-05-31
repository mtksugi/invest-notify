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
    ticker_window_days: int = 0,
    exempt_tickers: set[str] | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """通知の重複抑制。

    2 段階で抑制する:

    1. **(ticker, category) 単位**（既存）: 同一イベントキーを ``window_days`` 日は再通知しない。
    2. **ticker 単位の横断クールダウン**（``ticker_window_days > 0`` のとき）:
       同じ銘柄を**カテゴリをまたいで** ``ticker_window_days`` 日は再通知しない。
       「織り込み済みの巨大企業が何度も鳴る」状態を抑える発見レーン向けの仕組み。
       - ``exempt_tickers`` に含まれる銘柄（ユーザーの注視ティッカー）と
         ``bucket == "watch"`` の通知は、横断クールダウンの対象外（明示追跡のため頻度を残す）。

    returns: (allowed, suppressed)
    """
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    exempt = {t.strip().upper() for t in (exempt_tickers or set()) if isinstance(t, str) and t.strip()}

    cutoff = now - timedelta(days=int(window_days))
    recent_ids: set[str] = set()
    # ticker 横断: 銘柄ごとの直近送信時刻（event_id の "ticker:cat" 前半から復元）
    ticker_cutoff = now - timedelta(days=int(ticker_window_days))
    recent_tickers: set[str] = set()
    for e in state:
        dt = _parse_iso(e.sent_at)
        if not dt:
            continue
        if dt >= cutoff:
            recent_ids.add(e.event_id)
        if int(ticker_window_days) > 0 and dt >= ticker_cutoff:
            tk = e.event_id.split(":", 1)[0].strip().upper()
            if tk:
                recent_tickers.add(tk)

    allowed: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for n in notifications:
        ticker = str(n.get("ticker") or "").strip()
        cat = str(n.get("category") or "").strip()
        eid = f"{ticker}:{cat}"
        if eid in recent_ids:
            suppressed.append(n)
            continue
        is_exempt = (n.get("bucket") == "watch") or (ticker.upper() in exempt)
        if (
            int(ticker_window_days) > 0
            and not is_exempt
            and ticker.upper() in recent_tickers
        ):
            suppressed.append(n)
            continue
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


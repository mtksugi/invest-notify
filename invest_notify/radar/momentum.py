"""価格モメンタム算出.

C8（株価モメンタム: 底から 2〜5 倍 / 200日線越え）を算出するためのユーティリティ。
``fmp_historical_price`` の日足を使う。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fmp import FmpConfig, fmp_historical_price


@dataclass
class Momentum:
    ticker: str
    as_of: str
    last_close: float | None
    sma_200: float | None
    over_sma_200: bool
    over_sma_200_pct: float | None  # last_close / sma_200 - 1
    low_252d: float | None
    high_252d: float | None
    return_from_low_x: float | None  # last / low_252d
    return_from_high_pct: float | None  # last/high_252d - 1
    vol20: float | None
    vol60: float | None
    vol_ratio_20_60: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _avg(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def fetch_momentum(cfg: FmpConfig, *, ticker: str) -> Momentum | None:
    hist = fmp_historical_price(cfg, ticker=ticker, days=300)
    if not hist:
        return None
    # FMP は新しい日付が先頭
    closes_full: list[float] = []
    vols_full: list[float] = []
    for r in hist:
        if not isinstance(r, dict):
            continue
        c = r.get("close")
        v = r.get("volume")
        if isinstance(c, (int, float)):
            closes_full.append(float(c))
        if isinstance(v, (int, float)):
            vols_full.append(float(v))

    if not closes_full:
        return None
    last_close = closes_full[0]
    closes_252 = closes_full[:252]
    closes_200 = closes_full[:200]
    sma_200 = _avg(closes_200) if len(closes_200) >= 100 else None
    low_252 = min(closes_252) if closes_252 else None
    high_252 = max(closes_252) if closes_252 else None
    over_sma = bool(sma_200 is not None and last_close > sma_200)
    over_sma_pct = (last_close / sma_200 - 1.0) if (sma_200 is not None and sma_200 > 0) else None
    ret_from_low = (last_close / low_252) if (low_252 is not None and low_252 > 0) else None
    ret_from_high_pct = (last_close / high_252 - 1.0) if (high_252 is not None and high_252 > 0) else None

    vol20 = _avg(vols_full[:20]) if len(vols_full) >= 5 else None
    vol60 = _avg(vols_full[:60]) if len(vols_full) >= 30 else None
    vol_ratio = (vol20 / vol60) if (vol20 is not None and vol60 is not None and vol60 > 0) else None

    return Momentum(
        ticker=ticker,
        as_of=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        last_close=last_close,
        sma_200=sma_200,
        over_sma_200=over_sma,
        over_sma_200_pct=over_sma_pct,
        low_252d=low_252,
        high_252d=high_252,
        return_from_low_x=ret_from_low,
        return_from_high_pct=ret_from_high_pct,
        vol20=vol20,
        vol60=vol60,
        vol_ratio_20_60=vol_ratio,
    )


def write_momentum(out_dir: Path, m: Momentum) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{m.ticker}.json"
    p.write_text(json.dumps(m.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p

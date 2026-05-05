"""スコアリング・状態分類.

設計は ``docs/REDESIGN_v0.3.md`` §5 に準拠（単純合算）。

各シグナルを 0〜1 に正規化し、加重なしの平均を取る（MVP は均等重み）。
取れない指標は 0.5（中立）として総合スコアを歪めない。

state:
- ``trigger``      total >= 0.75 AND momentum >= 0.5
- ``candidate``    total >= 0.60
- ``watch``        total >= 0.40
- ``overheated``   PSR > 16 OR 株価が底から >= 10 倍
- ``out``          上記いずれにも該当しない
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

from .fundamentals import Fundamentals
from .momentum import Momentum


THEME_FRIENDLY_SECTORS = {
    "Technology",
    "Industrials",
    "Energy",
    "Communication Services",
    "Consumer Cyclical",
    "Healthcare",
}


@dataclass
class CandidateScore:
    ticker: str
    name: str | None
    sector: str | None
    market_cap_usd: float | None
    state: str
    total: float
    scores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    trigger_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _score_size_band(market_cap: float | None) -> float:
    if market_cap is None:
        return 0.5
    if 500_000_000 <= market_cap <= 30_000_000_000:
        return 1.0
    return 0.0


def _score_theme_link(sector: str | None) -> float:
    if not sector:
        return 0.5
    if sector in THEME_FRIENDLY_SECTORS:
        return 1.0
    return 0.5


def _score_growth(rev_yoy_4q: list[float | None]) -> float:
    """直近Q の YoY 売上を 30% を満点として評価（1.0 = +30% 以上）."""
    if not rev_yoy_4q or rev_yoy_4q[0] is None:
        return 0.5
    yoy = rev_yoy_4q[0]
    if yoy <= 0:
        return _clamp(0.5 + yoy, 0, 0.5)  # マイナス成長は 0〜0.5
    return _clamp(yoy / 0.30, 0, 1)


def _score_margin_improve(op_margin_4q: list[float | None]) -> float:
    """直近 Q の営業利益率と 4Q前の差。+10pp で満点."""
    if (
        not op_margin_4q
        or len(op_margin_4q) < 2
        or op_margin_4q[0] is None
        or op_margin_4q[-1] is None
    ):
        return 0.5
    diff = op_margin_4q[0] - op_margin_4q[-1]
    return _clamp(0.5 + diff / 0.20, 0, 1)


def _score_valuation_room(psr: float | None) -> float:
    """PSR<8 を満点、PSR>=16 を 0 として線形に減点."""
    if psr is None:
        return 0.5
    if psr <= 0:
        return 0.5
    if psr < 8:
        return 1.0
    if psr >= 16:
        return 0.0
    return _clamp(1.0 - (psr - 8) / 8, 0, 1)


def _score_non_dilutive(shares_yoy: float | None) -> float:
    """発行済株式 YoY 増加率。+3% 以下は満点、+13% 以上は 0."""
    if shares_yoy is None:
        return 0.5
    if shares_yoy <= 0.03:
        return 1.0
    if shares_yoy >= 0.13:
        return 0.0
    return _clamp(1.0 - (shares_yoy - 0.03) / 0.10, 0, 1)


def _score_consistency_4q(consistency: float | None) -> float:
    if consistency is None:
        return 0.5
    return _clamp(consistency, 0, 1)


def _score_momentum(m: Momentum | None) -> tuple[float, list[str]]:
    if m is None:
        return 0.5, []
    reasons: list[str] = []
    over = m.over_sma_200
    rfl = m.return_from_low_x
    if over and (rfl is not None) and (2.0 <= rfl <= 5.0):
        reasons.append("200日線奪還 + 底から 2〜5 倍ゾーン")
        return 1.0, reasons
    if over and (rfl is not None) and (1.5 <= rfl < 2.0):
        reasons.append("200日線奪還 + 底から 1.5〜2 倍")
        return 0.7, reasons
    if over:
        reasons.append("200日線奪還")
        return 0.5, reasons
    return 0.2, reasons


def _score_skepticism(m: Momentum | None, fundamentals: Fundamentals | None) -> float:
    """半信半疑度: 単純合算（出来高比 + アナリスト数）."""
    parts: list[float] = []
    if m is not None and m.vol_ratio_20_60 is not None:
        # 出来高比 1.2 以下 = まだ騒がれていない（高評価）
        if m.vol_ratio_20_60 <= 1.2:
            parts.append(1.0)
        elif m.vol_ratio_20_60 >= 3.0:
            parts.append(0.0)
        else:
            parts.append(_clamp(1.0 - (m.vol_ratio_20_60 - 1.2) / 1.8, 0, 1))
    if fundamentals is not None and fundamentals.analyst_count is not None:
        # アナリストカバレッジが 8 名以下 = 地味（高評価）
        if fundamentals.analyst_count <= 8:
            parts.append(1.0)
        elif fundamentals.analyst_count >= 25:
            parts.append(0.0)
        else:
            parts.append(_clamp(1.0 - (fundamentals.analyst_count - 8) / 17, 0, 1))
    if not parts:
        return 0.5
    return sum(parts) / len(parts)


def _is_overheated(m: Momentum | None, f: Fundamentals | None) -> bool:
    if m is not None and m.return_from_low_x is not None and m.return_from_low_x >= 10.0:
        return True
    if f is not None and f.latest_psr is not None and f.latest_psr > 16.0:
        return True
    return False


def score_candidate(
    *,
    ticker: str,
    name: str | None,
    sector: str | None,
    market_cap_usd: float | None,
    fundamentals: Fundamentals | None,
    momentum: Momentum | None,
) -> CandidateScore:
    s_size = _score_size_band(market_cap_usd)
    s_theme = _score_theme_link(sector)
    s_growth = _score_growth(fundamentals.revenue_yoy_4q) if fundamentals else 0.5
    s_margin = _score_margin_improve(fundamentals.operating_margin_4q) if fundamentals else 0.5
    s_valuation = _score_valuation_room(fundamentals.latest_psr) if fundamentals else 0.5
    s_dilute = _score_non_dilutive(fundamentals.shares_diluted_yoy) if fundamentals else 0.5
    s_consist = _score_consistency_4q(fundamentals.consistency_4q_growth) if fundamentals else 0.5
    s_mom, mom_reasons = _score_momentum(momentum)
    s_skep = _score_skepticism(momentum, fundamentals)

    scores = {
        "size_band": s_size,
        "theme_link": s_theme,
        "growth": s_growth,
        "margin_improvement": s_margin,
        "valuation_room": s_valuation,
        "non_dilutive": s_dilute,
        "consistency_4q": s_consist,
        "momentum": s_mom,
        "skepticism": s_skep,
    }
    total = sum(scores.values()) / len(scores)

    overheated = _is_overheated(momentum, fundamentals)
    if overheated:
        state = "overheated"
    elif total >= 0.75 and s_mom >= 0.5:
        state = "trigger"
    elif total >= 0.60:
        state = "candidate"
    elif total >= 0.40:
        state = "watch"
    else:
        state = "out"

    metrics: dict[str, Any] = {}
    if fundamentals is not None:
        metrics["revenue_yoy_4q"] = fundamentals.revenue_yoy_4q
        metrics["operating_margin_4q"] = fundamentals.operating_margin_4q
        metrics["shares_diluted_yoy"] = fundamentals.shares_diluted_yoy
        metrics["latest_psr"] = fundamentals.latest_psr
        metrics["latest_pe"] = fundamentals.latest_pe
        metrics["consistency_4q_growth"] = fundamentals.consistency_4q_growth
        metrics["analyst_count"] = fundamentals.analyst_count
    if momentum is not None:
        metrics["last_close"] = momentum.last_close
        metrics["over_sma_200"] = momentum.over_sma_200
        metrics["over_sma_200_pct"] = momentum.over_sma_200_pct
        metrics["return_from_low_x"] = momentum.return_from_low_x
        metrics["return_from_high_pct"] = momentum.return_from_high_pct
        metrics["vol_ratio_20_60"] = momentum.vol_ratio_20_60

    trigger_reasons: list[str] = []
    if state == "trigger":
        if s_growth >= 0.7:
            trigger_reasons.append("売上 YoY 加速")
        if s_margin >= 0.7:
            trigger_reasons.append("営業利益率改善")
        if s_consist >= 0.6:
            trigger_reasons.append("売上加速の連続性")
        trigger_reasons.extend(mom_reasons)
        if s_skep >= 0.7:
            trigger_reasons.append("市場がまだ騒いでいない（出来高/カバレッジ）")

    return CandidateScore(
        ticker=ticker,
        name=name,
        sector=sector,
        market_cap_usd=market_cap_usd,
        state=state,
        total=round(total, 4),
        scores={k: round(v, 4) for k, v in scores.items()},
        metrics=metrics,
        trigger_reasons=trigger_reasons,
    )

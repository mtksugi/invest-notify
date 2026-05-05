"""ファンダ取得 + 派生指標算出.

FMP の ``/income-statement``, ``/key-metrics``, ``/ratios`` を四半期で取得し、
銘柄ごとに派生指標（YoY 売上、営業利益率の前年同期差、希薄化率など）を計算。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fmp import (
    FmpConfig,
    fmp_income_statement,
    fmp_key_metrics_ttm,
    fmp_ratios_ttm,
    fmp_analyst_estimates_count,
)


@dataclass
class QuarterRecord:
    fiscal_date: str
    revenue: float | None
    gross_profit: float | None
    operating_income: float | None
    revenue_yoy: float | None
    gross_margin: float | None
    operating_margin: float | None
    shares_diluted: float | None
    psr: float | None
    pe_ratio: float | None


@dataclass
class Fundamentals:
    ticker: str
    as_of: str
    period_type: str  # "quarter" | "annual"
    quarters: list[QuarterRecord]
    revenue_yoy_4q: list[float | None]  # 直近4期分の YoY（period_type に応じる）
    operating_margin_4q: list[float | None]
    shares_diluted_yoy: float | None
    latest_psr: float | None
    latest_pe: float | None
    consistency_4q_growth: float
    analyst_count: int | None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ticker": self.ticker,
            "as_of": self.as_of,
            "period_type": self.period_type,
            "quarters": [asdict(q) for q in self.quarters],
            "revenue_yoy_4q": self.revenue_yoy_4q,
            "operating_margin_4q": self.operating_margin_4q,
            "shares_diluted_yoy": self.shares_diluted_yoy,
            "latest_psr": self.latest_psr,
            "latest_pe": self.latest_pe,
            "consistency_4q_growth": self.consistency_4q_growth,
            "analyst_count": self.analyst_count,
        }
        return d


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def _yoy_ratio(curr: float | None, prior: float | None) -> float | None:
    if curr is None or prior is None or prior == 0:
        return None
    try:
        return (curr - prior) / abs(prior)
    except Exception:
        return None


def fetch_fundamentals(
    cfg: FmpConfig, *, ticker: str, quarters: int = 8
) -> Fundamentals | None:
    """FMP からファンダ時系列を取得して派生指標まで計算.

    Starter プラン制限により ``period=quarter`` が 402 になる場合は ``annual`` に
    自動フォールバックする（``Fundamentals.period_type`` で識別可能）。

    PSR / PE は TTM 値（``key-metrics-ttm`` / ``ratios-ttm``）から取る。
    取得失敗 / 空データの場合は None。
    """
    income, period_type = fmp_income_statement(cfg, ticker=ticker, limit=quarters)
    if not income:
        return None

    income_by_date: dict[str, dict[str, Any]] = {}
    for r in income:
        if isinstance(r, dict):
            d = str(r.get("date") or "")
            if d:
                income_by_date[d] = r

    sorted_dates = sorted(income_by_date.keys(), reverse=True)[:quarters]

    # YoY 比較のスパン: quarter なら 4 期前、annual なら 1 期前
    yoy_lag = 4 if period_type == "quarter" else 1

    # PSR / PE は TTM のみ使う（quarter 版は Premium）
    metrics_ttm = None
    ratios_ttm = None
    try:
        metrics_ttm = fmp_key_metrics_ttm(cfg, ticker=ticker)
    except Exception:
        metrics_ttm = None
    try:
        ratios_ttm = fmp_ratios_ttm(cfg, ticker=ticker)
    except Exception:
        ratios_ttm = None

    latest_psr: float | None = None
    latest_pe: float | None = None
    if isinstance(metrics_ttm, dict):
        latest_psr = (
            _safe_float(metrics_ttm.get("priceToSalesRatioTTM"))
            or _safe_float(metrics_ttm.get("priceToSalesRatio"))
        )
        latest_pe = (
            _safe_float(metrics_ttm.get("peRatioTTM"))
            or _safe_float(metrics_ttm.get("peRatio"))
        )
    if latest_psr is None and isinstance(ratios_ttm, dict):
        latest_psr = (
            _safe_float(ratios_ttm.get("priceToSalesRatioTTM"))
            or _safe_float(ratios_ttm.get("priceToSalesRatio"))
        )
    if latest_pe is None and isinstance(ratios_ttm, dict):
        latest_pe = (
            _safe_float(ratios_ttm.get("priceToEarningsRatioTTM"))
            or _safe_float(ratios_ttm.get("peRatioTTM"))
            or _safe_float(ratios_ttm.get("priceEarningsRatioTTM"))
            or _safe_float(ratios_ttm.get("priceEarningsRatio"))
        )

    quarters_list: list[QuarterRecord] = []
    for d in sorted_dates:
        inc = income_by_date.get(d, {})
        rev = _safe_float(inc.get("revenue"))
        gross = _safe_float(inc.get("grossProfit"))
        op = _safe_float(inc.get("operatingIncome"))
        shares = _safe_float(inc.get("weightedAverageShsOutDil"))
        quarters_list.append(
            QuarterRecord(
                fiscal_date=d,
                revenue=rev,
                gross_profit=gross,
                operating_income=op,
                revenue_yoy=None,  # 後で埋める
                gross_margin=(gross / rev) if (gross is not None and rev not in (None, 0)) else None,
                operating_margin=(op / rev) if (op is not None and rev not in (None, 0)) else None,
                shares_diluted=shares,
                psr=None,  # quarter 単位では取れない（TTM のみ）
                pe_ratio=None,
            )
        )

    # YoY 計算: yoy_lag 期前と比較
    for i, q in enumerate(quarters_list):
        if i + yoy_lag < len(quarters_list):
            prior = quarters_list[i + yoy_lag]
            q.revenue_yoy = _yoy_ratio(q.revenue, prior.revenue)

    revenue_yoy_4q = [q.revenue_yoy for q in quarters_list[:4]]
    operating_margin_4q = [q.operating_margin for q in quarters_list[:4]]

    shares_diluted_yoy: float | None = None
    if len(quarters_list) > yoy_lag:
        shares_diluted_yoy = _yoy_ratio(
            quarters_list[0].shares_diluted, quarters_list[yoy_lag].shares_diluted
        )

    # 直近 4 期のうち、YoY が前期より加速している期の比率
    growth_increasing = 0
    growth_compared = 0
    for i in range(min(3, len(quarters_list) - 1)):
        a = quarters_list[i].revenue_yoy
        b = quarters_list[i + 1].revenue_yoy
        if a is None or b is None:
            continue
        growth_compared += 1
        if a > b:
            growth_increasing += 1
    consistency = (growth_increasing / growth_compared) if growth_compared > 0 else 0.0

    analyst_count = fmp_analyst_estimates_count(cfg, ticker=ticker)

    return Fundamentals(
        ticker=ticker,
        as_of=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        period_type=period_type,
        quarters=quarters_list,
        revenue_yoy_4q=revenue_yoy_4q,
        operating_margin_4q=operating_margin_4q,
        shares_diluted_yoy=shares_diluted_yoy,
        latest_psr=latest_psr,
        latest_pe=latest_pe,
        consistency_4q_growth=consistency,
        analyst_count=analyst_count,
    )


def write_fundamentals(out_dir: Path, f: Fundamentals) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{f.ticker}.json"
    p.write_text(json.dumps(f.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def load_fundamentals(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None

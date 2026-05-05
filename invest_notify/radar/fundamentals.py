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
    fmp_income_statement_quarter,
    fmp_key_metrics_quarter,
    fmp_ratios_quarter,
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
    quarters: list[QuarterRecord]
    revenue_yoy_4q: list[float | None]
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
    """FMP から四半期データを取得して派生指標まで計算.

    取得失敗 / 空データの場合は None。
    """
    income = fmp_income_statement_quarter(cfg, ticker=ticker, limit=quarters)
    metrics = fmp_key_metrics_quarter(cfg, ticker=ticker, limit=quarters)
    ratios = fmp_ratios_quarter(cfg, ticker=ticker, limit=quarters)

    if not income or not isinstance(income, list):
        return None

    income_by_date: dict[str, dict[str, Any]] = {}
    for r in income:
        if isinstance(r, dict):
            d = str(r.get("date") or "")
            if d:
                income_by_date[d] = r

    metrics_by_date: dict[str, dict[str, Any]] = {}
    for r in metrics:
        if isinstance(r, dict):
            d = str(r.get("date") or "")
            if d:
                metrics_by_date[d] = r

    ratios_by_date: dict[str, dict[str, Any]] = {}
    for r in ratios:
        if isinstance(r, dict):
            d = str(r.get("date") or "")
            if d:
                ratios_by_date[d] = r

    sorted_dates = sorted(income_by_date.keys(), reverse=True)[:quarters]

    quarters_list: list[QuarterRecord] = []
    for d in sorted_dates:
        inc = income_by_date.get(d, {})
        met = metrics_by_date.get(d, {})
        rat = ratios_by_date.get(d, {})
        rev = _safe_float(inc.get("revenue"))
        gross = _safe_float(inc.get("grossProfit"))
        op = _safe_float(inc.get("operatingIncome"))
        shares = _safe_float(inc.get("weightedAverageShsOutDil"))
        psr = _safe_float(met.get("priceToSalesRatio")) or _safe_float(rat.get("priceToSalesRatio"))
        pe = _safe_float(met.get("peRatio")) or _safe_float(rat.get("priceEarningsRatio"))
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
                psr=psr,
                pe_ratio=pe,
            )
        )

    # YoY 計算: 4Q 前と比較
    for i, q in enumerate(quarters_list):
        if i + 4 < len(quarters_list):
            prior = quarters_list[i + 4]
            q.revenue_yoy = _yoy_ratio(q.revenue, prior.revenue)

    revenue_yoy_4q = [q.revenue_yoy for q in quarters_list[:4]]
    operating_margin_4q = [q.operating_margin for q in quarters_list[:4]]

    shares_diluted_yoy: float | None = None
    if len(quarters_list) >= 5:
        shares_diluted_yoy = _yoy_ratio(
            quarters_list[0].shares_diluted, quarters_list[4].shares_diluted
        )

    latest_psr = quarters_list[0].psr if quarters_list else None
    latest_pe = quarters_list[0].pe_ratio if quarters_list else None

    # 直近 4Q のうち YoY 売上が +0% 以上かつ前期より加速している四半期の比率
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

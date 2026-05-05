"""ユニバース生成 / 古さ判定.

Phase 1: 半期に一度、手動で ``radar build-universe`` を実行する。
Phase 2: 月次自動化 + 差分ログを追加予定。

ユニバース定義:
- 米国（country=US, exchange=NYSE/NASDAQ）
- 時価総額バンド: $500M〜$30B
- ETF / SPAC は除外
- ``include.yaml`` で強制追加 / ``exclude.yaml`` で永久除外
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fmp import FmpConfig, fmp_stock_screener, fmp_company_profile


DEFAULT_MARKET_CAP_MIN = 500_000_000
DEFAULT_MARKET_CAP_MAX = 30_000_000_000
UNIVERSE_STALE_DAYS = 180


@dataclass(frozen=True)
class UniverseStaleness:
    age_days: int
    is_stale: bool
    generated_at: str | None  # ISO8601
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "age_days": self.age_days,
            "is_stale": self.is_stale,
            "generated_at": self.generated_at,
            "message": self.message,
        }


def _read_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        obj = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        return {}
    return {}


def _load_exclude(exclude_path: Path) -> set[str]:
    obj = _read_yaml_dict(exclude_path)
    arr = obj.get("exclude") if isinstance(obj, dict) else None
    out: set[str] = set()
    if isinstance(arr, list):
        for x in arr:
            if isinstance(x, dict) and isinstance(x.get("ticker"), str):
                out.add(x["ticker"].strip().upper())
            elif isinstance(x, str):
                out.add(x.strip().upper())
    return out


def _load_include(include_path: Path) -> list[dict[str, Any]]:
    obj = _read_yaml_dict(include_path)
    arr = obj.get("include") if isinstance(obj, dict) else None
    out: list[dict[str, Any]] = []
    if isinstance(arr, list):
        for x in arr:
            if isinstance(x, dict) and isinstance(x.get("ticker"), str):
                out.append({"ticker": x["ticker"].strip().upper(), "reason": x.get("reason")})
            elif isinstance(x, str):
                out.append({"ticker": x.strip().upper(), "reason": None})
    return out


def build_universe(
    *,
    cfg: FmpConfig,
    out_path: Path,
    market_cap_min: int = DEFAULT_MARKET_CAP_MIN,
    market_cap_max: int = DEFAULT_MARKET_CAP_MAX,
    exclude_path: Path | None = None,
    include_path: Path | None = None,
    fetch_profiles_for_includes: bool = True,
) -> dict[str, Any]:
    """FMP screener から米株ユニバースを生成して保存.

    出力スキーマ:
    ```
    {
      "generated_at": "2026-05-05T00:00:00Z",
      "params": {"market_cap_min": ..., "market_cap_max": ...},
      "tickers": [
        {"ticker": "VRT", "name": "...", "exchange": "...", "sector": "...",
         "industry": "...", "market_cap_usd": 105000000000,
         "country": "US", "is_forced_include": false, "include_reason": null}
      ],
      "stats": {"total": N, "excluded_manual": M, "forced_included": K}
    }
    ```
    """

    raw = fmp_stock_screener(
        cfg,
        market_cap_more_than=market_cap_min,
        market_cap_lower_than=market_cap_max,
    )

    exclude = _load_exclude(exclude_path) if exclude_path else set()
    includes = _load_include(include_path) if include_path else []

    tickers: list[dict[str, Any]] = []
    excluded_manual = 0
    seen: set[str] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym:
            continue
        if sym in exclude:
            excluded_manual += 1
            continue
        if sym in seen:
            continue
        # SPAC らしき名前（"Acquisition Corp", "Capital Corp"）はサクッと弾く
        name = str(row.get("companyName") or "")
        nl = name.lower()
        if " acquisition " in f" {nl} " or nl.endswith(" acquisition corp"):
            continue
        seen.add(sym)
        tickers.append(
            {
                "ticker": sym,
                "name": name,
                "exchange": row.get("exchangeShortName") or row.get("exchange"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "market_cap_usd": row.get("marketCap"),
                "country": row.get("country") or "US",
                "is_forced_include": False,
                "include_reason": None,
            }
        )

    forced_included = 0
    for inc in includes:
        sym = inc["ticker"]
        if sym in seen:
            continue
        seen.add(sym)
        forced_included += 1
        if fetch_profiles_for_includes:
            try:
                prof = fmp_company_profile(cfg, ticker=sym)
            except Exception:
                prof = None
            if prof:
                tickers.append(
                    {
                        "ticker": sym,
                        "name": prof.get("companyName") or "",
                        "exchange": prof.get("exchangeShortName") or prof.get("exchange"),
                        "sector": prof.get("sector"),
                        "industry": prof.get("industry"),
                        "market_cap_usd": prof.get("mktCap"),
                        "country": prof.get("country") or "US",
                        "is_forced_include": True,
                        "include_reason": inc.get("reason"),
                    }
                )
                continue
        # プロファイルが取れなくてもティッカーだけは載せる
        tickers.append(
            {
                "ticker": sym,
                "name": None,
                "exchange": None,
                "sector": None,
                "industry": None,
                "market_cap_usd": None,
                "country": "US",
                "is_forced_include": True,
                "include_reason": inc.get("reason"),
            }
        )

    obj = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "params": {
            "market_cap_min": market_cap_min,
            "market_cap_max": market_cap_max,
            "country": "US",
            "exchanges": ["NYSE", "NASDAQ"],
        },
        "tickers": tickers,
        "stats": {
            "total": len(tickers),
            "excluded_manual": excluded_manual,
            "forced_included": forced_included,
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return obj


def load_universe(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def check_universe_staleness(
    *, universe_path: Path, stale_days: int = UNIVERSE_STALE_DAYS
) -> UniverseStaleness:
    """ユニバースの古さを判定.

    Returns:
        ``is_stale=True`` のとき、A 系統メールに警告を載せる必要がある。
    """
    obj = load_universe(universe_path)
    if obj is None:
        return UniverseStaleness(
            age_days=99999,
            is_stale=True,
            generated_at=None,
            message=(
                f"Radar ユニバース ({universe_path}) が未生成です。"
                "次のコマンドで生成してください: "
                "`python -m invest_notify radar build-universe`"
            ),
        )
    gen_at = obj.get("generated_at")
    if not isinstance(gen_at, str):
        return UniverseStaleness(
            age_days=99999,
            is_stale=True,
            generated_at=None,
            message=(
                f"Radar ユニバース ({universe_path}) の generated_at が読めません。"
                "再生成してください: `python -m invest_notify radar build-universe`"
            ),
        )
    try:
        dt = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
    except Exception:
        return UniverseStaleness(
            age_days=99999,
            is_stale=True,
            generated_at=gen_at,
            message=(
                f"Radar ユニバース ({universe_path}) の generated_at 形式不正。"
                "再生成してください: `python -m invest_notify radar build-universe`"
            ),
        )
    age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    age_days = int(age_seconds // 86400)
    is_stale = age_days >= stale_days
    if is_stale:
        msg = (
            f"⚠ Radar ユニバースが {age_days} 日経過しています "
            f"(stale_threshold={stale_days}日)。"
            "次のコマンドで再生成してください: "
            "`python -m invest_notify radar build-universe`"
        )
    else:
        msg = f"Radar ユニバース age={age_days}日 / stale_threshold={stale_days}日（OK）"
    return UniverseStaleness(
        age_days=age_days,
        is_stale=is_stale,
        generated_at=gen_at,
        message=msg,
    )

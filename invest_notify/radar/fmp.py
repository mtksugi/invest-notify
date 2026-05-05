"""FMP (Financial Modeling Prep) API クライアント.

Starter プラン想定:
- 300 req/分
- 5年分の履歴
- 米国カバレッジ

エンドポイント仕様:
- 2026年時点では ``/stable/`` ベースのエンドポイントを使う必要がある
  （旧 ``/api/v3/`` は Starter 以上で 403 Forbidden を返す）
- ティッカーはパスではなく ``?symbol=AAPL`` クエリパラメータで指定する
- 認証は ``?apikey=YOUR_API_KEY`` または ``apikey: YOUR_API_KEY`` ヘッダ

設計方針:
- 全レスポンスは ``data/radar/_fmp_cache/<endpoint>/<ticker>.json`` にキャッシュ
- キャッシュには ``_fetched_at`` を埋め、TTL 判定で再取得制御
- ファンダは四半期更新なので TTL を長め（既定 6日）にする
- 株価系は週次なので TTL 短め（既定 2日）

※ ``requirements.txt`` の ``requests`` を使う。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


FMP_BASE_URL = "https://financialmodelingprep.com/stable"
DEFAULT_CACHE_TTL_SECONDS = 6 * 24 * 3600  # ファンダ用 6日


@dataclass(frozen=True)
class FmpConfig:
    api_key: str
    cache_dir: Path
    request_timeout_seconds: int = 30
    sleep_seconds_between_requests: float = 0.05  # 300 req/min なら最低 0.2s 必要だが、Starterは余裕がある
    max_retries: int = 3


def load_fmp_config_from_env(*, cache_dir: Path | str = "data/radar/_fmp_cache") -> FmpConfig:
    api_key = os.environ.get("FMP_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "FMP_API_KEY is required (set in .env or environment). "
            "Subscribe at https://site.financialmodelingprep.com/"
        )
    timeout = int(os.environ.get("FMP_TIMEOUT_SECONDS", "30"))
    sleep = float(os.environ.get("FMP_REQUEST_SLEEP", "0.05"))
    retries = int(os.environ.get("FMP_MAX_RETRIES", "3"))
    return FmpConfig(
        api_key=api_key,
        cache_dir=Path(cache_dir),
        request_timeout_seconds=timeout,
        sleep_seconds_between_requests=sleep,
        max_retries=retries,
    )


def _cache_path(cfg: FmpConfig, *, endpoint: str, key: str) -> Path:
    safe_endpoint = endpoint.strip("/").replace("/", "_")
    safe_key = key.replace("/", "_")
    return cfg.cache_dir / safe_endpoint / f"{safe_key}.json"


def _read_cache(path: Path, *, ttl_seconds: int) -> Any | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fetched_at = obj.get("_fetched_at") if isinstance(obj, dict) else None
    if not isinstance(fetched_at, str):
        return None
    try:
        dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except Exception:
        return None
    age = (datetime.now(timezone.utc) - dt).total_seconds()
    if age > ttl_seconds:
        return None
    return obj.get("payload")


def _write_cache(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {
        "_fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": payload,
    }
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _redact_apikey(url: str) -> str:
    import re

    return re.sub(r"(apikey=)[^&]+", r"\1***", url)


class FmpHttpError(RuntimeError):
    """FMP がエラーレスポンスを返したことを示す例外（プラン制限/認証など）."""


def _http_get_json(cfg: FmpConfig, *, url: str) -> Any:
    last_err: Exception | None = None
    for attempt in range(cfg.max_retries):
        try:
            resp = requests.get(url, timeout=cfg.request_timeout_seconds)
            if resp.status_code == 429:
                # Rate limit: バックオフ
                time.sleep(2 ** (attempt + 1))
                continue
            if 500 <= resp.status_code < 600:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 402, 403):
                # 認証 / プラン制限。リトライしても解決しないので即時失敗させる。
                body = (resp.text or "").strip()[:500]
                raise FmpHttpError(
                    f"FMP {resp.status_code} for {_redact_apikey(url)}: {body}"
                )
            resp.raise_for_status()
            return resp.json()
        except FmpHttpError:
            raise
        except (requests.RequestException, ValueError) as e:
            last_err = e
            time.sleep(2 ** attempt)
            continue
    if last_err:
        raise last_err
    raise RuntimeError("FMP request failed without exception (unexpected)")


def fmp_get(
    cfg: FmpConfig,
    *,
    endpoint: str,
    cache_key: str,
    params: dict[str, Any] | None = None,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    base_url: str = FMP_BASE_URL,
) -> Any:
    """汎用 FMP GET（キャッシュ付き）.

    Args:
        endpoint: ``/income-statement/AAPL`` のようなパス
        cache_key: キャッシュファイル名（ティッカー等）
        params: クエリパラメータ
        ttl_seconds: キャッシュ TTL（既定 6日）
    """
    path = _cache_path(cfg, endpoint=endpoint, key=cache_key)
    cached = _read_cache(path, ttl_seconds=ttl_seconds)
    if cached is not None:
        return cached

    q = dict(params or {})
    q["apikey"] = cfg.api_key
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}?{urlencode(q)}"
    payload = _http_get_json(cfg, url=url)
    time.sleep(cfg.sleep_seconds_between_requests)
    _write_cache(path, payload)
    return payload


def fmp_stock_screener(
    cfg: FmpConfig,
    *,
    market_cap_more_than: int = 500_000_000,
    market_cap_lower_than: int = 30_000_000_000,
    is_etf: bool = False,
    is_actively_trading: bool = True,
    country: str = "US",
    exchange_list: list[str] | None = None,
    ttl_seconds: int = 7 * 24 * 3600,
) -> list[dict[str, Any]]:
    """``/stable/company-screener`` でユニバースを取得.

    時価総額バンド + ETF 除外 + 上場中 + 米国 + NYSE/NASDAQ。

    Note:
        2026年時点で旧 ``/api/v3/stock-screener`` は Starter 以上で 403 を返す。
        stable では ``/company-screener`` に名前が変わっている。
        ``exchange`` は1値のみ受け付けるため、複数取引所はループで集約する。
    """
    exchanges = exchange_list or ["NYSE", "NASDAQ"]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ex in exchanges:
        params: dict[str, Any] = {
            "marketCapMoreThan": market_cap_more_than,
            "marketCapLowerThan": market_cap_lower_than,
            "isEtf": "false" if not is_etf else "true",
            "isActivelyTrading": "true" if is_actively_trading else "false",
            "country": country,
            "exchange": ex,
            "limit": 5000,
        }
        cache_key = (
            f"screener_{market_cap_more_than}_{market_cap_lower_than}_{country}_{ex}"
        )
        payload = fmp_get(
            cfg,
            endpoint="company-screener",
            cache_key=cache_key,
            params=params,
            ttl_seconds=ttl_seconds,
        )
        if isinstance(payload, list):
            for r in payload:
                if not isinstance(r, dict):
                    continue
                sym = str(r.get("symbol") or "").strip().upper()
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                rows.append(r)
    return rows


def fmp_income_statement_quarter(
    cfg: FmpConfig, *, ticker: str, limit: int = 8, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
) -> list[dict[str, Any]]:
    payload = fmp_get(
        cfg,
        endpoint="income-statement",
        cache_key=f"income_q_{ticker}",
        params={"symbol": ticker, "period": "quarter", "limit": limit},
        ttl_seconds=ttl_seconds,
    )
    if isinstance(payload, list):
        return payload
    return []


def fmp_key_metrics_quarter(
    cfg: FmpConfig, *, ticker: str, limit: int = 8, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
) -> list[dict[str, Any]]:
    payload = fmp_get(
        cfg,
        endpoint="key-metrics",
        cache_key=f"key_metrics_q_{ticker}",
        params={"symbol": ticker, "period": "quarter", "limit": limit},
        ttl_seconds=ttl_seconds,
    )
    if isinstance(payload, list):
        return payload
    return []


def fmp_ratios_quarter(
    cfg: FmpConfig, *, ticker: str, limit: int = 8, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
) -> list[dict[str, Any]]:
    payload = fmp_get(
        cfg,
        endpoint="ratios",
        cache_key=f"ratios_q_{ticker}",
        params={"symbol": ticker, "period": "quarter", "limit": limit},
        ttl_seconds=ttl_seconds,
    )
    if isinstance(payload, list):
        return payload
    return []


def fmp_historical_price(
    cfg: FmpConfig,
    *,
    ticker: str,
    days: int = 365,
    ttl_seconds: int = 2 * 24 * 3600,
) -> list[dict[str, Any]]:
    """日足の終値・出来高履歴を取得（直近 ``days`` 日）.

    stable では ``/historical-price-eod/full?symbol=XXX&from=YYYY-MM-DD&to=YYYY-MM-DD``。
    レスポンスは ``[{date, open, high, low, close, volume, ...}, ...]``（配列）。
    旧 v3 と違って ``{symbol, historical:[...]}`` ではない点に注意。
    """
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days + 14)  # 余裕を持たせる
    payload = fmp_get(
        cfg,
        endpoint="historical-price-eod/full",
        cache_key=f"hist_{ticker}_{days}",
        params={
            "symbol": ticker,
            "from": start.isoformat(),
            "to": today.isoformat(),
        },
        ttl_seconds=ttl_seconds,
    )
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        hist = payload.get("historical")
        if isinstance(hist, list):
            return hist
    return []


def fmp_company_profile(
    cfg: FmpConfig, *, ticker: str, ttl_seconds: int = 7 * 24 * 3600
) -> dict[str, Any] | None:
    payload = fmp_get(
        cfg,
        endpoint="profile",
        cache_key=f"profile_{ticker}",
        params={"symbol": ticker},
        ttl_seconds=ttl_seconds,
    )
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    if isinstance(payload, dict):
        return payload
    return None


def fmp_analyst_estimates_count(
    cfg: FmpConfig, *, ticker: str, ttl_seconds: int = 7 * 24 * 3600
) -> int | None:
    """アナリスト推定の数（半信半疑度の代理指標）.

    stable: ``/analyst-estimates?symbol=XXX&period=quarter&page=0&limit=1``
    レスポンスの ``numberAnalystEstimatedRevenue`` を読む（無ければ None）。
    """
    try:
        payload = fmp_get(
            cfg,
            endpoint="analyst-estimates",
            cache_key=f"estimates_{ticker}",
            params={"symbol": ticker, "period": "quarter", "page": 0, "limit": 1},
            ttl_seconds=ttl_seconds,
        )
    except Exception:
        return None
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        for k in ("numberAnalystEstimatedRevenue", "numberAnalystsEstimatedRevenue"):
            v = payload[0].get(k)
            if isinstance(v, (int, float)):
                return int(v)
    return None

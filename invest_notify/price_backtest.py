from __future__ import annotations

"""
通知の「初動を捉えられているか」を株価ベースで評価するためのユーティリティ。

テキストの proxy（"急騰" / "rallied" 等が summary にあるか）では、
LLM が言葉を選んだだけで「実際に動いた後に通知したか」までは測れない。

本モジュールは Yahoo Finance Chart API（v8/finance/chart）から日次終値を取り、
各通知について以下を算出する。

- pre_return  : event_time から見て -N営業日 → 直近クローズ の終値リターン
                （通知前にどれくらい既に動いていたか）
- post_return : event_time 直近クローズ → +M営業日 終値 のリターン
                （通知後にどれくらい動いたか）

これを使って以下を分類する:
- early_capture: |pre| <= early_band かつ post >= rise_threshold（上がる前に気づけた）
- late_chase  : pre >= rise_threshold（既に上がってから通知 = 後追い）
- missed      : post <= -rise_threshold（通知後に逆行）
- flat        : ほぼ動かず

impact_direction が "positive/mixed" と "negative" で意味が反転するため、
呼び出し側で「上がる方向」前提の分類を必要に応じて反転する。

外部ネット必須なので、呼び出し側（review_history）は明示的に有効化したときだけ実行する。
"""

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; invest_notify/1.0)"


@dataclass
class PriceSeries:
    symbol: str
    timestamps: list[int] = field(default_factory=list)  # UNIX秒（取引日のクローズ）
    closes: list[float] = field(default_factory=list)

    def closest_close_at_or_before(self, ts: int) -> tuple[int, float] | None:
        best: tuple[int, float] | None = None
        for t, c in zip(self.timestamps, self.closes):
            if c is None:
                continue
            if t <= ts:
                best = (t, float(c))
            else:
                break
        return best

    def close_offset_days(self, base_ts: int, offset_trading_days: int) -> tuple[int, float] | None:
        if not self.timestamps:
            return None
        idx = -1
        for i, t in enumerate(self.timestamps):
            if self.closes[i] is None:
                continue
            if t <= base_ts:
                idx = i
            else:
                break
        if idx < 0:
            return None
        target = idx + offset_trading_days
        if target < 0 or target >= len(self.timestamps):
            return None
        c = self.closes[target]
        if c is None:
            return None
        return self.timestamps[target], float(c)


def _http_get_json(url: str, *, timeout: float = 15.0, user_agent: str = DEFAULT_USER_AGENT) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
            return json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None


def fetch_price_series(
    symbol: str,
    *,
    start: datetime,
    end: datetime,
    interval: str = "1d",
    cache_dir: Path | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    sleep_seconds: float = 0.0,
) -> PriceSeries | None:
    """Yahoo Finance Chart API から日次終値を取得。失敗時は None。"""
    if not symbol:
        return None
    p1 = int(start.replace(tzinfo=timezone.utc).timestamp()) if start.tzinfo is None else int(start.timestamp())
    p2 = int(end.replace(tzinfo=timezone.utc).timestamp()) if end.tzinfo is None else int(end.timestamp())
    if p1 >= p2:
        return None

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{urllib.parse.quote_plus(symbol)}__{interval}.json"
        if cache_path.exists():
            try:
                obj = json.loads(cache_path.read_text(encoding="utf-8"))
                ts = obj.get("timestamps") or []
                cl = obj.get("closes") or []
                if ts and ts[0] <= p1 and ts[-1] >= p2 - 86400:
                    return PriceSeries(symbol=symbol, timestamps=list(ts), closes=list(cl))
            except Exception:
                pass

    qs = urllib.parse.urlencode({"interval": interval, "period1": p1, "period2": p2})
    url = f"{YAHOO_CHART_URL.format(symbol=urllib.parse.quote_plus(symbol))}?{qs}"
    obj = _http_get_json(url, user_agent=user_agent)
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    if not isinstance(obj, dict):
        return None
    chart = obj.get("chart")
    if not isinstance(chart, dict):
        return None
    result = chart.get("result")
    if not isinstance(result, list) or not result:
        return None
    head = result[0]
    if not isinstance(head, dict):
        return None
    timestamps = head.get("timestamp") or []
    indicators = head.get("indicators") or {}
    quote = indicators.get("quote") or [{}]
    closes_raw = quote[0].get("close") if quote else []
    if not isinstance(timestamps, list) or not isinstance(closes_raw, list):
        return None
    series = PriceSeries(symbol=symbol, timestamps=[int(t) for t in timestamps], closes=list(closes_raw))

    if cache_dir is not None:
        try:
            (cache_dir / f"{urllib.parse.quote_plus(symbol)}__{interval}.json").write_text(
                json.dumps({"timestamps": series.timestamps, "closes": series.closes}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    return series


@dataclass
class NotificationReturns:
    ticker: str
    event_ts: int | None
    pre_return: float | None
    post_return: float | None
    pre_window_days: int
    post_window_days: int


def compute_returns_for_notification(
    *,
    series: PriceSeries,
    event_dt: datetime,
    pre_window_days: int = 5,
    post_window_days: int = 10,
) -> NotificationReturns:
    base_ts = (
        int(event_dt.replace(tzinfo=timezone.utc).timestamp())
        if event_dt.tzinfo is None
        else int(event_dt.timestamp())
    )
    closest = series.closest_close_at_or_before(base_ts)
    if closest is None:
        return NotificationReturns(
            ticker=series.symbol,
            event_ts=base_ts,
            pre_return=None,
            post_return=None,
            pre_window_days=pre_window_days,
            post_window_days=post_window_days,
        )
    base_idx_ts, base_close = closest

    pre_pt = series.close_offset_days(base_idx_ts, -pre_window_days)
    pre_ret: float | None = None
    if pre_pt is not None and pre_pt[1] > 0:
        pre_ret = (base_close / pre_pt[1]) - 1.0

    post_pt = series.close_offset_days(base_idx_ts, post_window_days)
    post_ret: float | None = None
    if post_pt is not None and base_close > 0:
        post_ret = (post_pt[1] / base_close) - 1.0

    return NotificationReturns(
        ticker=series.symbol,
        event_ts=base_ts,
        pre_return=pre_ret,
        post_return=post_ret,
        pre_window_days=pre_window_days,
        post_window_days=post_window_days,
    )


def classify_capture(
    *,
    pre_return: float | None,
    post_return: float | None,
    rise_threshold: float = 0.05,
    early_pre_band: float = 0.03,
) -> str:
    """
    通知の「捉え方」を分類する（"上がる方向" 前提）。

    - early_capture : 通知前は静か（|pre| <= early_pre_band）かつ 通知後に上昇（post >= rise_threshold）
    - late_chase    : 通知前に既に上がっていた（pre >= rise_threshold）
    - missed        : 通知後に下落（post <= -rise_threshold）
    - flat          : 通知後ほぼ動かず
    - unknown       : データ不足

    呼び出し側で impact_direction=negative のときは pre/post を反転させて渡すこと。
    """
    if pre_return is None or post_return is None:
        return "unknown"
    if pre_return >= rise_threshold:
        return "late_chase"
    if abs(pre_return) <= early_pre_band and post_return >= rise_threshold:
        return "early_capture"
    if post_return <= -rise_threshold:
        return "missed"
    return "flat"

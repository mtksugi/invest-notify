from __future__ import annotations

"""
送信直前の株価ゲート。

60日履歴×Yahoo Financeバックテストの結果から、以下が分かっている:
- pre_return (直近5営業日) が +10% 以上 → post_signed=-2.12%（既に噴いた後）
- pre_return が -5% 未満 かつ impact=negative → post_signed=-5.65%（崩れた後の悲観追従）
- pre_return が -5%〜-2% → post_signed=+5.30%（ミーンリバーション）

そこで、email/send 直前に銘柄ごとに直近5営業日のリターンを取得し、
各通知に `pre_return_gate` を付与する。さらに高リスクな組み合わせは

- confirmed の場合は early_warning に降格
- あるいは完全除外

を行う。

ネットワーク失敗時は「ゲートを適用しない（素通り）」=「安全側=変更しない」とする。
無効化するには INVEST_NOTIFY_PRICE_GATE=off を指定。
"""

import os
import time
import urllib.parse
import urllib.request
import json as _json
from datetime import datetime, timedelta, timezone
from typing import Any


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_DEFAULT_UA = "Mozilla/5.0 (compatible; invest_notify/1.0)"


def _fetch_recent_closes(
    symbol: str,
    *,
    lookback_days: int = 15,
    timeout: float = 10.0,
    user_agent: str = _DEFAULT_UA,
) -> list[tuple[int, float]] | None:
    """直近 lookback_days のカレンダー日ぶん、取引終値（timestamp, close）を返す。"""
    if not symbol:
        return None
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    qs = urllib.parse.urlencode(
        {"interval": "1d", "period1": int(start.timestamp()), "period2": int(end.timestamp())}
    )
    url = f"{YAHOO_CHART_URL.format(symbol=urllib.parse.quote_plus(symbol))}?{qs}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": user_agent, "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = resp.read()
            obj = _json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None
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
    ts = head.get("timestamp") or []
    ind = head.get("indicators") or {}
    quote = ind.get("quote") or [{}]
    closes = quote[0].get("close") if quote else []
    if not isinstance(ts, list) or not isinstance(closes, list):
        return None
    out: list[tuple[int, float]] = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        try:
            out.append((int(t), float(c)))
        except Exception:
            continue
    return out


def compute_recent_return(symbol: str, *, window_trading_days: int = 5) -> float | None:
    """
    直近 window_trading_days 営業日の終値リターン。
    取得できない銘柄（上場廃止 / 外国株で Yahoo が返さない等）は None。
    """
    closes = _fetch_recent_closes(symbol)
    if not closes:
        return None
    # 最新の終値と、その window_trading_days 日前の終値
    if len(closes) <= window_trading_days:
        return None
    latest_ts, latest = closes[-1]
    base_ts, base = closes[-(window_trading_days + 1)]
    if base <= 0:
        return None
    return (latest / base) - 1.0


def _signed(pre: float | None, impact: str) -> float | None:
    """impact=negative のとき符号反転して「良い方向」を正に揃える。"""
    if pre is None:
        return None
    if (impact or "").strip().lower() == "negative":
        return -pre
    return pre


def _should_skip() -> bool:
    v = (os.environ.get("INVEST_NOTIFY_PRICE_GATE") or "").strip().lower()
    return v in ("off", "0", "false", "no", "disable", "disabled")


def annotate_notifications_with_price_gate(
    notifications: list[dict[str, Any]],
    *,
    up_chase_threshold: float = 0.10,
    down_chase_threshold: float = -0.05,
    window_trading_days: int = 5,
    fetch_sleep_seconds: float = 0.1,
    log: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    返り値: (allowed, suppressed)

    動作:
    - 各通知の ticker について直近N営業日リターン (pre_return_gate) を取得し、
      通知の `pre_return_gate_pct` と `pre_return_gate_signed_pct` を追記。
    - 以下のいずれかに該当する confirmed は early_warning に降格:
        (a) pre_signed >= up_chase_threshold（既に大きく良い方向に動いた ＝ 後追い）
        (b) pre_signed <= down_chase_threshold（既に大きく悪い方向に動いた ＝ 反転追従）
    - 以下のいずれかに該当する通知は完全に除外（suppressed へ）:
        (c) impact=negative かつ pre_return(原値) <= -0.10（既に -10% 以上売られてから
            更に悲観通知するパターン：60日実績で post=-5.65%）
        (d) impact=positive かつ pre_return(原値) >= +0.15（既に +15% 以上噴いてから
            ポジティブ通知するパターン）
    - ネットワーク不通 / 取得失敗の銘柄は素通り（安全側）
    - INVEST_NOTIFY_PRICE_GATE=off なら処理自体をスキップ
    """
    if _should_skip():
        if log:
            print("[price-gate] skipped (INVEST_NOTIFY_PRICE_GATE=off)", flush=True)
        return list(notifications), []

    if not notifications:
        return list(notifications), []

    # 銘柄単位で取得（重複呼び出し回避）
    tickers = sorted({str(n.get("ticker") or "").strip() for n in notifications if isinstance(n, dict) and n.get("ticker")})
    cache: dict[str, float | None] = {}
    for i, t in enumerate(tickers):
        cache[t] = compute_recent_return(t, window_trading_days=window_trading_days)
        if fetch_sleep_seconds > 0:
            time.sleep(fetch_sleep_seconds)
    if log:
        got = sum(1 for v in cache.values() if v is not None)
        print(f"[price-gate] fetched {got}/{len(tickers)} tickers", flush=True)

    allowed: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []

    for n in notifications:
        if not isinstance(n, dict):
            continue
        ticker = str(n.get("ticker") or "").strip()
        pre = cache.get(ticker)
        if pre is None:
            # 株価取得失敗 → ゲート適用せず素通り
            allowed.append(n)
            continue

        impact = (n.get("impact_direction") or "").strip().lower()
        pre_signed = _signed(pre, impact)

        n2 = dict(n)
        n2["pre_return_gate_pct"] = round(pre * 100.0, 2)
        if pre_signed is not None:
            n2["pre_return_gate_signed_pct"] = round(pre_signed * 100.0, 2)
        n2["pre_return_gate_window_days"] = window_trading_days

        drop_reason: str | None = None
        downgrade_reason: str | None = None

        # (c) ネガ × 既に大きく下落 → 除外
        if impact == "negative" and pre <= -0.10:
            drop_reason = f"negative+already_down({pre*100:+.1f}%): late-chase risk"
        # (d) ポジ × 既に大きく上昇 → 除外
        elif impact == "positive" and pre >= 0.15:
            drop_reason = f"positive+already_up({pre*100:+.1f}%): late-chase risk"

        if drop_reason is None and n2.get("lane") == "confirmed":
            # (a) 既に大きく良い方向 → 降格
            if pre_signed is not None and pre_signed >= up_chase_threshold:
                downgrade_reason = f"already moved {pre_signed*100:+.1f}% in signed direction"
            # (b) 既に大きく悪い方向（負側）→ 降格
            elif pre_signed is not None and pre_signed <= down_chase_threshold:
                downgrade_reason = f"already moved {pre_signed*100:+.1f}% in signed direction"

        if drop_reason is not None:
            n2["price_gate_action"] = "suppressed"
            n2["price_gate_reason"] = drop_reason
            suppressed.append(n2)
            if log:
                print(
                    f"[price-gate] SUPPRESS {ticker} {impact} pre={pre*100:+.1f}% reason={drop_reason}",
                    flush=True,
                )
            continue

        if downgrade_reason is not None:
            n2["lane"] = "early_warning"
            n2["price_gate_action"] = "downgraded"
            n2["price_gate_reason"] = downgrade_reason
            if log:
                print(
                    f"[price-gate] DOWNGRADE {ticker} {impact} pre={pre*100:+.1f}% reason={downgrade_reason}",
                    flush=True,
                )

        allowed.append(n2)

    return allowed, suppressed

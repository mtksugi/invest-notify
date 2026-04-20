from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ai.stage2 import _cap_notifications
from .signal_lexicon import LATE_REACTION_PATTERNS, STRUCTURE_MARKERS
from .price_backtest import (
    classify_capture,
    compute_returns_for_notification,
    fetch_price_series,
)


def _load_daily_notifications(
    history_dir: Path, *, prefer_raw_pool: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    """
    history_dir 配下の YYYY-MM-DD/notifications.json をすべて読む。
    history_dir/notifications.json のような単一ファイルにも対応する
    （試運転用：1ファイルだけある場合でも動かしたい）。

    prefer_raw_pool=True かつ同ディレクトリに notifications_pool.json があれば、
    その raw_notifications を使う（後追い再ランクで効果を見るため）。
    """
    rows: list[tuple[str, dict[str, Any]]] = []
    # YYYY-MM-DD/ ディレクトリ
    for day_dir in sorted(history_dir.glob("*/")):
        if prefer_raw_pool:
            pool_p = day_dir / "notifications_pool.json"
            if pool_p.exists():
                try:
                    pool_obj = json.loads(pool_p.read_text(encoding="utf-8"))
                    raw = pool_obj.get("postprocessed_notifications") or pool_obj.get("raw_notifications")
                    if isinstance(raw, list):
                        for n in raw:
                            if isinstance(n, dict):
                                rows.append((day_dir.name, n))
                        continue
                except Exception:
                    pass
        p = day_dir / "notifications.json"
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        notifs = obj.get("notifications", [])
        if not isinstance(notifs, list):
            continue
        day = day_dir.name
        for n in notifs:
            if isinstance(n, dict):
                rows.append((day, n))

    # 単一ファイル fallback
    if not rows:
        single = history_dir / "notifications.json"
        if single.exists():
            try:
                obj = json.loads(single.read_text(encoding="utf-8"))
            except Exception:
                obj = {}
            notifs = obj.get("notifications", []) if isinstance(obj, dict) else []
            if isinstance(notifs, list):
                # generated_at から日付を引く
                gen = obj.get("generated_at") if isinstance(obj, dict) else None
                day = "unknown"
                if isinstance(gen, str):
                    try:
                        day = gen[:10]
                    except Exception:
                        day = "unknown"
                for n in notifs:
                    if isinstance(n, dict):
                        rows.append((day, n))
    return rows


def _notif_text(n: dict[str, Any]) -> str:
    parts: list[str] = [str(n.get("summary") or "")]
    ev = n.get("evidence")
    if isinstance(ev, list):
        for e in ev[:5]:
            if isinstance(e, dict):
                parts.append(str(e.get("title") or ""))
    return "\n".join(parts).lower()


def _parse_iso_to_utc(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    ss = s.strip()
    if ss.endswith("Z"):
        ss = ss[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ss)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _freshness_days_from_evidence(n: dict[str, Any]) -> float | None:
    g = _parse_iso_to_utc(n.get("generated_at")) or _parse_iso_to_utc(n.get("event_time"))
    ev = n.get("evidence")
    if g is None or not isinstance(ev, list) or not ev:
        return None
    ages: list[float] = []
    for e in ev:
        if not isinstance(e, dict):
            continue
        p = _parse_iso_to_utc(e.get("published_at"))
        if p is None:
            continue
        ages.append((g - p).total_seconds() / 86400.0)
    if not ages:
        return None
    return min(ages)


def _expected_direction(n: dict[str, Any]) -> int:
    """
    通知が「どちらに動くと当たり」かを符号で返す。
    - positive: +1（上昇で当たり）
    - negative: -1（下落で当たり）
    - mixed/unclear: 0（どちらでもない＝後追いだけ判定する）
    """
    impact = str(n.get("impact_direction") or "").strip().lower()
    if impact == "positive":
        return 1
    if impact == "negative":
        return -1
    return 0


def _event_dt_for_backtest(n: dict[str, Any], day: str) -> datetime | None:
    """
    バックテストの基準時刻を決める。優先順:
    1. event_time
    2. generated_at
    3. day（YYYY-MM-DD）の 00:00 UTC
    """
    dt = _parse_iso_to_utc(n.get("event_time")) or _parse_iso_to_utc(n.get("generated_at"))
    if dt is not None:
        return dt
    if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return datetime.fromisoformat(day + "T00:00:00+00:00")
    return None


def _run_price_backtest(
    rows: list[tuple[str, dict[str, Any]]],
    *,
    pre_window_days: int,
    post_window_days: int,
    rise_threshold: float,
    early_pre_band: float,
    cache_dir: Path,
    sleep_seconds: float,
    by_day_notifs: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """
    各通知 (day, n) について、ticker の Yahoo Finance 終値を取って
    pre/post リターンと「捉え方」分類をつける。

    返り値:
    {
      "params": {...},
      "evaluable_count": int,            # 株価データが取れたもの
      "directional_count": int,          # impact_direction が positive/negative のもの
      "directional_results": [...],
      "summary": {                       # 方向性ありに対する集計
          "early_capture_rate": float,
          "late_chase_rate": float,
          "missed_rate": float,
          "flat_rate": float,
          "hit_rate": float,             # post の符号が想定方向と一致した割合
          "mean_pre_return_signed": float,
          "mean_post_return_signed": float,
      },
      "per_category": {cat: {early_capture_rate, late_chase_rate, hit_rate, n}},
      "per_lane":     {lane: {...}},
      "examples_late_chase": [...]       # 既に大きく上がってから通知された代表例
      "examples_early_capture": [...]    # 通知後に動いた好例
    }
    """
    # ticker ごとに必要レンジを束ねて1回だけfetch
    by_ticker: dict[str, list[tuple[str, dict[str, Any], datetime]]] = defaultdict(list)
    for day, n in rows:
        ticker = str(n.get("ticker") or "").strip()
        if not ticker:
            continue
        if ticker.startswith("^"):
            # 指数は対象外
            continue
        ev_dt = _event_dt_for_backtest(n, day)
        if ev_dt is None:
            continue
        by_ticker[ticker].append((day, n, ev_dt))

    series_cache: dict[str, Any] = {}
    for ticker, items in by_ticker.items():
        dts = [it[2] for it in items]
        start = min(dts) - timedelta(days=max(pre_window_days * 2, 14))
        end = max(dts) + timedelta(days=max(post_window_days * 2, 14))
        # 余裕を持って取り、未来側はAPI側でmax(today)に丸める想定
        ps = fetch_price_series(
            ticker,
            start=start,
            end=end,
            cache_dir=cache_dir,
            sleep_seconds=sleep_seconds,
        )
        series_cache[ticker] = ps

    detail: list[dict[str, Any]] = []
    classes_dir: Counter[str] = Counter()
    classes_raw: Counter[str] = Counter()
    pre_signed: list[float] = []
    post_signed: list[float] = []
    hits = 0
    directional = 0

    # impact_direction の方向に揃えて集計する（positive/negative のみ）
    per_category_collect: defaultdict[str, list[str]] = defaultdict(list)
    per_lane_collect: defaultdict[str, list[str]] = defaultdict(list)

    examples_late_chase: list[dict[str, Any]] = []
    examples_early_capture: list[dict[str, Any]] = []
    examples_missed: list[dict[str, Any]] = []

    for ticker, items in by_ticker.items():
        ps = series_cache.get(ticker)
        if ps is None or not getattr(ps, "timestamps", None):
            continue
        for day, n, ev_dt in items:
            r = compute_returns_for_notification(
                series=ps,
                event_dt=ev_dt,
                pre_window_days=pre_window_days,
                post_window_days=post_window_days,
            )
            if r.pre_return is None and r.post_return is None:
                continue
            cls_raw = classify_capture(
                pre_return=r.pre_return,
                post_return=r.post_return,
                rise_threshold=rise_threshold,
                early_pre_band=early_pre_band,
            )
            classes_raw[cls_raw] += 1
            cat = str(n.get("category") or "")
            lane = str(n.get("lane") or "")

            sign = _expected_direction(n)
            # 集計用クラスは「期待方向に揃えた」ものを使う。
            # mixed/unclear は方向が取れないので raw のまま参考表示する。
            cls_for_bucket: str
            if sign != 0:
                pre_s = r.pre_return * sign if r.pre_return is not None else None
                post_s = r.post_return * sign if r.post_return is not None else None
                cls_for_bucket = classify_capture(
                    pre_return=pre_s,
                    post_return=post_s,
                    rise_threshold=rise_threshold,
                    early_pre_band=early_pre_band,
                )
            else:
                cls_for_bucket = cls_raw

            per_category_collect[cat].append(cls_for_bucket)
            per_lane_collect[lane].append(cls_for_bucket)

            d_entry = {
                "day": day,
                "ticker": ticker,
                "category": cat,
                "lane": lane,
                "impact_direction": n.get("impact_direction"),
                "pre_return": r.pre_return,
                "post_return": r.post_return,
                "class_raw": cls_raw,
                "class_directional": cls_for_bucket if sign != 0 else None,
                "summary_head": str(n.get("summary") or "")[:160],
            }
            detail.append(d_entry)

            if sign != 0:
                directional += 1
                pre_s = (r.pre_return or 0.0) * sign
                post_s = (r.post_return or 0.0) * sign
                pre_signed.append(pre_s)
                post_signed.append(post_s)
                if (r.post_return or 0.0) * sign > 0:
                    hits += 1
                classes_dir[cls_for_bucket] += 1
                if cls_for_bucket == "late_chase" and len(examples_late_chase) < 10:
                    examples_late_chase.append(d_entry)
                if cls_for_bucket == "early_capture" and len(examples_early_capture) < 10:
                    examples_early_capture.append(d_entry)
                if cls_for_bucket == "missed" and len(examples_missed) < 10:
                    examples_missed.append(d_entry)

    def _rate(c: Counter[str], total: int, key: str) -> float:
        return (c.get(key, 0) / total) if total else 0.0

    def _category_summary(items: dict[str, list[str]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, lst in items.items():
            if not lst:
                continue
            cnt = Counter(lst)
            total = len(lst)
            out[k] = {
                "n": total,
                "early_capture_rate": _rate(cnt, total, "early_capture"),
                "late_chase_rate": _rate(cnt, total, "late_chase"),
                "missed_rate": _rate(cnt, total, "missed"),
                "flat_rate": _rate(cnt, total, "flat"),
            }
        return out

    summary: dict[str, Any] = {}
    if directional:
        summary = {
            "early_capture_rate": classes_dir.get("early_capture", 0) / directional,
            "late_chase_rate": classes_dir.get("late_chase", 0) / directional,
            "missed_rate": classes_dir.get("missed", 0) / directional,
            "flat_rate": classes_dir.get("flat", 0) / directional,
            "hit_rate": hits / directional,
            "mean_pre_return_signed": (sum(pre_signed) / len(pre_signed)) if pre_signed else 0.0,
            "mean_post_return_signed": (sum(post_signed) / len(post_signed)) if post_signed else 0.0,
        }

    base = {
        "params": {
            "pre_window_days": pre_window_days,
            "post_window_days": post_window_days,
            "rise_threshold": rise_threshold,
            "early_pre_band": early_pre_band,
        },
        "evaluable_count": len(detail),
        "directional_count": directional,
        "raw_class_counts": dict(classes_raw),
        "directional_class_counts": dict(classes_dir),
        "summary": summary,
        "per_category": _category_summary(per_category_collect),
        "per_lane": _category_summary(per_lane_collect),
        "examples_late_chase": examples_late_chase,
        "examples_early_capture": examples_early_capture,
        "examples_missed": examples_missed,
    }

    # --- 旧ランク(confidence順) vs 新ランク(_cap_notifications) のバックテスト比較 ---
    # 保存済み履歴の通知プールは _cap_notifications を「過去版」で並べたあとの結果。
    # ここでは現在のスコアリングを各日のプールに再適用し、KPI が改善したかを測る。
    if by_day_notifs:
        rank_compare = _backtest_rank_compare(
            by_day_notifs=by_day_notifs,
            series_cache=series_cache,
            pre_window_days=pre_window_days,
            post_window_days=post_window_days,
            rise_threshold=rise_threshold,
            early_pre_band=early_pre_band,
        )
        base["rank_compare_backtest"] = rank_compare

    return base


def _backtest_one(
    n: dict[str, Any],
    *,
    day: str,
    series_cache: dict[str, Any],
    pre_window_days: int,
    post_window_days: int,
    rise_threshold: float,
    early_pre_band: float,
) -> dict[str, Any] | None:
    """単一通知の捕捉分類 (directional版) を返す。data不足は None。"""
    ticker = str(n.get("ticker") or "").strip()
    if not ticker or ticker.startswith("^"):
        return None
    ev_dt = _event_dt_for_backtest(n, day)
    if ev_dt is None:
        return None
    ps = series_cache.get(ticker)
    if ps is None or not getattr(ps, "timestamps", None):
        return None
    r = compute_returns_for_notification(
        series=ps,
        event_dt=ev_dt,
        pre_window_days=pre_window_days,
        post_window_days=post_window_days,
    )
    if r.pre_return is None and r.post_return is None:
        return None
    sign = _expected_direction(n)
    if sign == 0:
        cls = classify_capture(
            pre_return=r.pre_return,
            post_return=r.post_return,
            rise_threshold=rise_threshold,
            early_pre_band=early_pre_band,
        )
        return {"class": cls, "directional": False, "pre": r.pre_return, "post": r.post_return}
    pre_s = r.pre_return * sign if r.pre_return is not None else None
    post_s = r.post_return * sign if r.post_return is not None else None
    cls = classify_capture(
        pre_return=pre_s,
        post_return=post_s,
        rise_threshold=rise_threshold,
        early_pre_band=early_pre_band,
    )
    return {"class": cls, "directional": True, "pre": r.pre_return, "post": r.post_return, "sign": sign}


def _backtest_rank_compare(
    *,
    by_day_notifs: dict[str, list[dict[str, Any]]],
    series_cache: dict[str, Any],
    pre_window_days: int,
    post_window_days: int,
    rise_threshold: float,
    early_pre_band: float,
) -> dict[str, Any]:
    """
    各日プールに対して
      - 旧ランク: lane別 confidence 上位3件
      - 新ランク: _cap_notifications（現在の _priority_score 含む）
    を選び、それぞれの KPI（early_capture_rate / late_chase_rate / hit_rate）を株価で測る。
    """

    def _conf(n: dict[str, Any]) -> float:
        try:
            return float(n.get("confidence") or 0.0)
        except Exception:
            return 0.0

    def _agg(picks: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        c: Counter[str] = Counter()
        directional = 0
        hits = 0
        pre_signed: list[float] = []
        post_signed: list[float] = []
        for day, n in picks:
            r = _backtest_one(
                n,
                day=day,
                series_cache=series_cache,
                pre_window_days=pre_window_days,
                post_window_days=post_window_days,
                rise_threshold=rise_threshold,
                early_pre_band=early_pre_band,
            )
            if r is None:
                continue
            if not r["directional"]:
                continue
            directional += 1
            c[r["class"]] += 1
            sign = r["sign"]
            if r["pre"] is not None:
                pre_signed.append(r["pre"] * sign)
            if r["post"] is not None:
                post_signed.append(r["post"] * sign)
                if r["post"] * sign > 0:
                    hits += 1
        n_total = directional or 1  # 0除算回避
        return {
            "selected": len(picks),
            "directional": directional,
            "early_capture_rate": c.get("early_capture", 0) / n_total if directional else 0.0,
            "late_chase_rate": c.get("late_chase", 0) / n_total if directional else 0.0,
            "missed_rate": c.get("missed", 0) / n_total if directional else 0.0,
            "flat_rate": c.get("flat", 0) / n_total if directional else 0.0,
            "hit_rate": hits / n_total if directional else 0.0,
            "mean_pre_return_signed": (sum(pre_signed) / len(pre_signed)) if pre_signed else 0.0,
            "mean_post_return_signed": (sum(post_signed) / len(post_signed)) if post_signed else 0.0,
            "class_counts": dict(c),
        }

    old_picks: list[tuple[str, dict[str, Any]]] = []
    new_picks: list[tuple[str, dict[str, Any]]] = []
    for day, day_notifs in by_day_notifs.items():
        confirmed = sorted([n for n in day_notifs if n.get("lane") == "confirmed"], key=_conf, reverse=True)[:3]
        early = sorted([n for n in day_notifs if n.get("lane") == "early_warning"], key=_conf, reverse=True)[:3]
        seen_old: set[str] = set()
        for n in confirmed + early:
            k = f"{str(n.get('ticker') or '').strip()}:{str(n.get('category') or '').strip()}"
            if not k or k in seen_old:
                continue
            seen_old.add(k)
            old_picks.append((day, n))
        for n in _cap_notifications(
            day_notifs,
            max_confirmed=3,
            max_early_warning=3,
            watch_tickers=None,
            max_watch=0,
        ):
            new_picks.append((day, n))

    return {
        "old_rank": _agg(old_picks),
        "new_rank": _agg(new_picks),
    }


def review_history(
    history_dir: str | Path,
    *,
    backtest: bool = False,
    pre_window_days: int = 5,
    post_window_days: int = 10,
    rise_threshold: float = 0.05,
    early_pre_band: float = 0.03,
    cache_dir: str | Path | None = None,
    sleep_seconds: float = 0.0,
    prefer_raw_pool: bool = False,
) -> dict[str, Any]:
    p = Path(history_dir)
    rows = _load_daily_notifications(p, prefer_raw_pool=prefer_raw_pool)
    by_day: defaultdict[str, int] = defaultdict(int)
    by_category: Counter[str] = Counter()
    by_lane: Counter[str] = Counter()
    by_impact: Counter[str] = Counter()
    by_ticker: Counter[str] = Counter()

    late_like = 0
    structure_like = 0
    early_positive_like = 0
    examples_late: list[dict[str, Any]] = []
    age_days_all: list[float] = []
    age_days_late: list[float] = []

    by_day_notifs: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    compiled = [re.compile(x, flags=re.IGNORECASE) for x in LATE_REACTION_PATTERNS]

    for day, n in rows:
        by_day[day] += 1
        cat = str(n.get("category") or "")
        lane = str(n.get("lane") or "")
        impact = str(n.get("impact_direction") or "")
        ticker = str(n.get("ticker") or "")
        by_category[cat] += 1
        by_lane[lane] += 1
        by_impact[impact] += 1
        by_day_notifs[day].append(n)
        if ticker:
            by_ticker[ticker] += 1

        text = _notif_text(n)
        is_late = any(r.search(text) for r in compiled)
        age = _freshness_days_from_evidence(n)
        if age is not None:
            age_days_all.append(age)
        if is_late:
            late_like += 1
            if age is not None:
                age_days_late.append(age)
            if len(examples_late) < 10:
                examples_late.append(
                    {
                        "day": day,
                        "ticker": ticker,
                        "lane": lane,
                        "category": cat,
                        "summary_head": str(n.get("summary") or "")[:120],
                    }
                )
        if any(m.lower() in text for m in STRUCTURE_MARKERS):
            structure_like += 1
        if lane == "early_warning" and impact in ("positive", "mixed"):
            early_positive_like += 1

    baseline_total = 0
    baseline_late = 0
    reranked_total = 0
    reranked_late = 0
    old_cat: Counter[str] = Counter()
    new_cat: Counter[str] = Counter()
    old_cat_late: Counter[str] = Counter()
    new_cat_late: Counter[str] = Counter()
    old_ticker: Counter[str] = Counter()
    new_ticker: Counter[str] = Counter()
    for _, day_notifs in by_day_notifs.items():
        # 旧相当: laneごとにconfidence順（dedupeあり）
        def _conf(n: dict[str, Any]) -> float:
            try:
                return float(n.get("confidence") or 0.0)
            except Exception:
                return 0.0

        confirmed = sorted([n for n in day_notifs if n.get("lane") == "confirmed"], key=_conf, reverse=True)[:3]
        early = sorted([n for n in day_notifs if n.get("lane") == "early_warning"], key=_conf, reverse=True)[:3]
        old_pick: list[dict[str, Any]] = []
        seen_old: set[str] = set()
        for n in confirmed + early:
            k = f"{str(n.get('ticker') or '').strip()}:{str(n.get('category') or '').strip()}"
            if not k or k in seen_old:
                continue
            seen_old.add(k)
            old_pick.append(n)
        new_pick = _cap_notifications(
            day_notifs,
            max_confirmed=3,
            max_early_warning=3,
            watch_tickers=None,
            max_watch=0,
        )
        baseline_total += len(old_pick)
        reranked_total += len(new_pick)
        for n in old_pick:
            cat = str(n.get("category") or "")
            tk = str(n.get("ticker") or "")
            old_cat[cat] += 1
            if tk:
                old_ticker[tk] += 1
            is_late_old = any(r.search(_notif_text(n)) for r in compiled)
            if is_late_old:
                baseline_late += 1
                old_cat_late[cat] += 1
        for n in new_pick:
            cat = str(n.get("category") or "")
            tk = str(n.get("ticker") or "")
            new_cat[cat] += 1
            if tk:
                new_ticker[tk] += 1
            is_late_new = any(r.search(_notif_text(n)) for r in compiled)
            if is_late_new:
                reranked_late += 1
                new_cat_late[cat] += 1

    total = len(rows)
    days = len(by_day)
    result: dict[str, Any] = {
        "days": days,
        "notifications_total": total,
        "avg_notifications_per_day": (total / days if days else 0.0),
        "zero_notification_days": sum(1 for _, c in by_day.items() if c == 0),
        "category_counts": dict(by_category),
        "lane_counts": dict(by_lane),
        "impact_counts": dict(by_impact),
        "top_tickers": [{"ticker": t, "count": c} for t, c in by_ticker.most_common(20)],
        "initial_move_capture_proxy": {
            "late_reaction_ratio": (late_like / total if total else 0.0),
            "structure_signal_ratio": (structure_like / total if total else 0.0),
            "early_positive_or_mixed_ratio": (early_positive_like / total if total else 0.0),
            "evidence_freshness_days_median": (
                sorted(age_days_all)[len(age_days_all) // 2] if age_days_all else None
            ),
            "evidence_freshness_days_median_late_only": (
                sorted(age_days_late)[len(age_days_late) // 2] if age_days_late else None
            ),
            "late_ratio_old_rank_proxy": (baseline_late / baseline_total if baseline_total else 0.0),
            "late_ratio_new_rank_proxy": (reranked_late / reranked_total if reranked_total else 0.0),
        },
        "category_mix_compare": {
            "old_rank_category_counts": dict(old_cat),
            "new_rank_category_counts": dict(new_cat),
            "old_rank_late_by_category": dict(old_cat_late),
            "new_rank_late_by_category": dict(new_cat_late),
        },
        "ticker_diversity_compare": {
            "old_unique_tickers": len(old_ticker),
            "new_unique_tickers": len(new_ticker),
            "old_top_ticker_share": (
                max(old_ticker.values()) / baseline_total if (old_ticker and baseline_total) else 0.0
            ),
            "new_top_ticker_share": (
                max(new_ticker.values()) / reranked_total if (new_ticker and reranked_total) else 0.0
            ),
            "old_top_tickers": [{"ticker": t, "count": c} for t, c in old_ticker.most_common(10)],
            "new_top_tickers": [{"ticker": t, "count": c} for t, c in new_ticker.most_common(10)],
        },
        "late_breakdown": {
            "by_category": dict(Counter(str(r.get("category") or "") for _, r in rows if any(c.search(_notif_text(r)) for c in compiled))),
            "by_ticker_top10": [
                {"ticker": t, "count": c}
                for t, c in Counter(
                    str(r.get("ticker") or "")
                    for _, r in rows
                    if any(c.search(_notif_text(r)) for c in compiled)
                ).most_common(10)
            ],
        },
        "examples_late_reaction": examples_late,
    }

    if backtest:
        cache = Path(cache_dir) if cache_dir else (p.parent / "_yf_cache")
        result["price_backtest"] = _run_price_backtest(
            rows,
            pre_window_days=pre_window_days,
            post_window_days=post_window_days,
            rise_threshold=rise_threshold,
            early_pre_band=early_pre_band,
            cache_dir=cache,
            sleep_seconds=sleep_seconds,
            by_day_notifs=dict(by_day_notifs),
        )

    return result

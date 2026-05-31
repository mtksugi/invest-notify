"""週次ワンショット実行: ユニバース → ファンダ → モメンタム → スコアリング → メール.

CLI: ``python -m invest_notify radar weekly --config config.yaml``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fmp import FmpConfig
from .universe import (
    UniverseStaleness,
    check_universe_staleness,
    load_universe,
)
from .fundamentals import fetch_fundamentals, write_fundamentals
from .momentum import fetch_momentum, write_momentum
from .score import score_candidate, CandidateScore
from .email import render_radar_weekly_email


def run_weekly(
    *,
    cfg: FmpConfig,
    universe_path: Path,
    out_dir: Path,
    fundamentals_dir: Path,
    momentum_dir: Path,
    state_path: Path,
    max_tickers: int | None = None,
    skip_when_stale: bool = False,
    qualitative: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """週次パイプラインを実行して、メール本文・HTML・candidates.json を生成.

    Returns: ``{"subject": str, "text_body": str, "html_body": str,
              "candidates": [...], "universe_status": {...}, "transitions": {...}}``
    """

    universe = load_universe(universe_path)
    staleness = check_universe_staleness(universe_path=universe_path)

    if universe is None or not isinstance(universe.get("tickers"), list):
        # ユニバース未生成。空メールを出してユーザーに build-universe を促す。
        empty_status = {
            "subject": f"[Radar Weekly] {datetime.now(timezone.utc).date().isoformat()} ユニバース未生成",
            "text_body": (
                "Radar ユニバースが未生成です。\n"
                "次のコマンドを実行してください:\n"
                "  python -m invest_notify radar build-universe --config config.yaml\n"
            ),
            "html_body": (
                "<html><body><h2>ユニバース未生成</h2>"
                "<p>次のコマンドを実行してください:<br><code>python -m invest_notify radar build-universe --config config.yaml</code></p>"
                "</body></html>"
            ),
            "candidates": [],
            "universe_status": staleness.to_dict(),
            "transitions": {},
        }
        return empty_status

    if skip_when_stale and staleness.is_stale:
        if verbose:
            print(f"[radar] universe is stale ({staleness.age_days} days). skip_when_stale=True → skip.")
        return {
            "subject": f"[Radar Weekly] skipped (universe stale {staleness.age_days}d)",
            "text_body": staleness.message,
            "html_body": f"<html><body><p>{staleness.message}</p></body></html>",
            "candidates": [],
            "universe_status": staleness.to_dict(),
            "transitions": {},
        }

    tickers = universe["tickers"]
    if isinstance(max_tickers, int) and max_tickers > 0:
        tickers = tickers[:max_tickers]

    # 銘柄ごとにファンダ / モメンタムを取って採点
    candidates: list[CandidateScore] = []
    fundamentals_dir.mkdir(parents=True, exist_ok=True)
    momentum_dir.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(tickers):
        sym = str(t.get("ticker") or "").strip().upper()
        if not sym:
            continue
        if verbose and i % 50 == 0:
            print(f"[radar] {i}/{len(tickers)} {sym}", flush=True)

        try:
            f = fetch_fundamentals(cfg, ticker=sym)
        except Exception as e:
            if verbose:
                print(f"[radar] fundamentals fail {sym}: {e}")
            f = None
        if f is not None:
            try:
                write_fundamentals(fundamentals_dir, f)
            except Exception:
                pass

        try:
            m = fetch_momentum(cfg, ticker=sym)
        except Exception as e:
            if verbose:
                print(f"[radar] momentum fail {sym}: {e}")
            m = None
        if m is not None:
            try:
                write_momentum(momentum_dir, m)
            except Exception:
                pass

        sc = score_candidate(
            ticker=sym,
            name=t.get("name"),
            sector=t.get("sector"),
            market_cap_usd=t.get("market_cap_usd"),
            fundamentals=f,
            momentum=m,
        )
        candidates.append(sc)

    candidates.sort(key=lambda c: c.total, reverse=True)
    cand_dicts = [c.to_dict() for c in candidates]

    # イベント検出（前回 state と比較）
    prev_state = _load_state(state_path)
    prev_by = prev_state.get("by_ticker", {})
    now = datetime.now(timezone.utc)
    detection = detect_events(prev_by=prev_by, curr=cand_dicts, now=now)
    earnings = detection["earnings"]
    events = detection["events"]
    transitions = detection["transitions"]

    # 定性レイヤー（ショートリスト＝決算/新着イベントに出た銘柄のみ。LLM で論点付与）
    if qualitative and (earnings or events):
        _attach_qualitative(cfg, earnings=earnings, events=events, verbose=verbose)

    # メール生成
    subject, text_body, html_body = render_radar_weekly_email(
        candidates=cand_dicts,
        earnings=earnings,
        events=events,
        transitions=transitions,
        universe_status=staleness.to_dict(),
    )

    # 保存
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = out_dir / "candidates.json"
    candidates_path.write_text(
        json.dumps(
            {
                "generated_at": now.isoformat().replace("+00:00", "Z"),
                "universe_status": staleness.to_dict(),
                "candidates": cand_dicts,
                "earnings": earnings,
                "events": events,
                "transitions": transitions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "email.txt").write_text(text_body, encoding="utf-8")
    (out_dir / "email.txt.html").write_text(html_body, encoding="utf-8")

    # state 更新（今回通知した銘柄のクールダウンを記録）
    notified = {e["ticker"] for e in (earnings + events) if e.get("ticker")}
    _save_state(state_path, cand_dicts, prev_by=prev_by, notified=notified, now=now)

    return {
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "candidates": cand_dicts,
        "earnings": earnings,
        "events": events,
        "universe_status": staleness.to_dict(),
        "transitions": transitions,
    }


# --- パラメータ（docs/REDESIGN_v0.4.md §8 = バランス） ---
NOTIFY_COOLDOWN_WEEKS = 8
MAX_EVENTS = 10


def _attach_qualitative(cfg, *, earnings: list, events: list, verbose: bool) -> None:
    """ショートリストに定性評価を付与（OpenAI 鍵が無ければ静かにスキップ）."""
    try:
        from ..ai.openai_compat import load_openai_compat_config_from_env_for_stage
        from .qualitative import assess_shortlist
    except Exception:
        return
    try:
        llm_cfg = load_openai_compat_config_from_env_for_stage(stage="stage2")
    except Exception as ex:
        if verbose:
            print(f"[radar] qualitative skipped (no LLM config): {ex}")
        return
    # 重複銘柄は1回だけ評価
    by_ticker: dict[str, dict[str, Any]] = {}
    for e in earnings + events:
        c = e.get("candidate") or {}
        t = c.get("ticker")
        if t and t not in by_ticker:
            by_ticker[t] = c
    try:
        qual = assess_shortlist(llm_cfg, cfg, candidates=list(by_ticker.values()), verbose=verbose)
    except Exception as ex:
        if verbose:
            print(f"[radar] qualitative failed: {ex}")
        return
    for e in earnings + events:
        c = e.get("candidate") or {}
        q = qual.get(c.get("ticker"))
        if q:
            c["qualitative"] = q
            e["qualitative"] = q

_STATE_RANK = {"out": 0, "watch": 1, "overheated": 1, "candidate": 2, "trigger": 3}


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"by_ticker": {}}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        return {"by_ticker": {}}
    return {"by_ticker": {}}


def _in_band(metrics: dict[str, Any]) -> bool:
    rfl = metrics.get("return_from_low_x")
    return bool(metrics.get("over_sma_200")) and (rfl is not None and 1.3 <= rfl <= 5.0)


def _save_state(
    path: Path,
    candidates: list[dict[str, Any]],
    *,
    prev_by: dict[str, Any],
    notified: set[str],
    now: datetime,
) -> None:
    now_iso = now.isoformat().replace("+00:00", "Z")
    by_ticker: dict[str, dict[str, Any]] = {}
    for c in candidates:
        t = c.get("ticker")
        if not (isinstance(t, str) and t):
            continue
        m = c.get("metrics") or {}
        prevt = prev_by.get(t) or {}
        is_notified = t in notified
        by_ticker[t] = {
            "state": c.get("state"),
            "total": c.get("total"),
            "last_fiscal_date": m.get("latest_fiscal_date") or prevt.get("last_fiscal_date"),
            "mom_over_sma200": bool(m.get("over_sma_200")),
            "mom_in_band": _in_band(m),
            "last_notified_at": now_iso if is_notified else prevt.get("last_notified_at"),
            "last_notified_rank": (
                _STATE_RANK.get(c.get("state"), 0) if is_notified else prevt.get("last_notified_rank")
            ),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"as_of": now_iso, "by_ticker": by_ticker}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _weeks_since(iso: Any, now: datetime) -> float | None:
    dt = _parse_iso(iso)
    if dt is None:
        return None
    return (now - dt).total_seconds() / (86400.0 * 7)


def _earnings_notable(metrics: dict[str, Any]) -> tuple[bool, str, list[str]]:
    """新決算が「特筆」に値するか判定（docs §2）。returns (notable, direction, reasons)."""

    def _pct(x: Any) -> str:
        try:
            return f"{float(x) * 100:+.0f}%"
        except Exception:
            return "n/a"

    yoy = metrics.get("revenue_yoy_4q") or []
    om = metrics.get("operating_margin_4q") or []
    cons = metrics.get("consistency_4q_growth")
    dil = metrics.get("shares_diluted_yoy")

    pos: list[str] = []
    neg: list[str] = []

    if len(yoy) >= 2 and yoy[0] is not None and yoy[1] is not None:
        if yoy[1] <= 0 < yoy[0]:
            pos.append("売上プラス転換")
        elif yoy[0] >= yoy[1] + 0.02:
            pos.append(f"売上YoY加速 {_pct(yoy[1])}→{_pct(yoy[0])}")
        if yoy[0] < 0 or yoy[0] <= yoy[1] - 0.10:
            neg.append(f"売上減速/マイナス {_pct(yoy[1])}→{_pct(yoy[0])}")
    if len(om) >= 2 and om[0] is not None and om[1] is not None and (om[0] - om[1]) >= 0.05:
        pos.append(f"営業利益率改善 +{(om[0]-om[1])*100:.0f}pp")
    if cons is not None and cons >= 0.67:
        pos.append("加速の連続性")
    if dil is not None and dil > 0.10:
        neg.append(f"希薄化 +{dil*100:.0f}%")

    if neg:
        return True, "negative", neg + pos
    if pos:
        return True, "positive", pos
    return False, "neutral", []


def detect_events(
    *, prev_by: dict[str, Any], curr: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    """前回 state と比較して、今週の離散イベントを抽出する。

    - earnings: 新決算を検出し「特筆」条件を満たすもの（クールダウン無視で常時通知）
    - events:   TIER_UP / BREAKOUT（クールダウン適用、銘柄ごと最大1件）
    - transitions: 昇格/降格などの集計（件名・サマリ用）
    """
    earnings: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    promoted = demoted = new_in = 0

    curr_by = {c["ticker"]: c for c in curr if isinstance(c.get("ticker"), str)}
    earnings_tickers: set[str] = set()

    for t, c in curr_by.items():
        m = c.get("metrics") or {}
        prevt = prev_by.get(t) or {}
        new_state = c.get("state")
        new_rank = _STATE_RANK.get(new_state, 0)
        old_rank = _STATE_RANK.get(prevt.get("state"), 0)
        if old_rank == 0 and new_rank > 0:
            new_in += 1
        elif new_rank > old_rank:
            promoted += 1
        elif new_rank < old_rank:
            demoted += 1

        # --- 決算（特筆）: 会計期末が更新された銘柄のみ ---
        cur_fd = m.get("latest_fiscal_date")
        prev_fd = prevt.get("last_fiscal_date")
        if cur_fd and prev_fd and cur_fd != prev_fd:
            notable, direction, reasons = _earnings_notable(m)
            if notable:
                earnings.append(
                    {
                        "ticker": t,
                        "type": "EARNINGS_NOTABLE",
                        "direction": direction,
                        "reasons": reasons,
                        "candidate": c,
                    }
                )
                earnings_tickers.add(t)

    # --- TIER_UP / BREAKOUT（クールダウン適用） ---
    for t, c in curr_by.items():
        if t in earnings_tickers:
            continue  # 決算セクションに出るので新着イベントでは重複させない
        m = c.get("metrics") or {}
        prevt = prev_by.get(t) or {}
        new_state = c.get("state")
        new_rank = _STATE_RANK.get(new_state, 0)
        old_rank = _STATE_RANK.get(prevt.get("state"), 0)

        weeks = _weeks_since(prevt.get("last_notified_at"), now)
        in_cooldown = weeks is not None and weeks < NOTIFY_COOLDOWN_WEEKS
        last_rank = prevt.get("last_notified_rank") or 0

        ev: dict[str, Any] | None = None
        # TIER_UP: ランクが上がった。クールダウン中でも「より上位に進んだ」なら通知。
        if new_rank > old_rank and new_state in ("candidate", "trigger"):
            if (not in_cooldown) or (new_rank > last_rank):
                ev = {
                    "ticker": t,
                    "type": "TIER_UP",
                    "from": prevt.get("state") or "new",
                    "to": new_state,
                    "candidate": c,
                }
        # BREAKOUT: 200日線を新規奪還 / エントリー帯に新規進入。trigger/candidate のみ。
        if ev is None and new_state in ("candidate", "trigger") and not in_cooldown:
            now_over = bool(m.get("over_sma_200"))
            now_band = _in_band(m)
            was_over = bool(prevt.get("mom_over_sma200"))
            was_band = bool(prevt.get("mom_in_band"))
            reasons = []
            if now_over and not was_over:
                reasons.append("200日線を新規奪還")
            if now_band and not was_band:
                reasons.append("エントリー帯(底から1.3〜5x)に新規進入")
            if reasons:
                ev = {"ticker": t, "type": "BREAKOUT", "reasons": reasons, "candidate": c}

        if ev is not None:
            events.append(ev)

    # 重要度順（trigger > candidate、total 降順）に並べて上限件数で切る
    def _ev_key(e: dict[str, Any]) -> tuple[int, float]:
        c = e.get("candidate") or {}
        return (_STATE_RANK.get(c.get("state"), 0), float(c.get("total") or 0))

    events.sort(key=_ev_key, reverse=True)
    events = events[:MAX_EVENTS]

    transitions = {"promoted": promoted, "demoted": demoted, "new_in": new_in}
    return {"earnings": earnings, "events": events, "transitions": transitions}

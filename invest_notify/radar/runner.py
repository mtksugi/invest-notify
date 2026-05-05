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

    # 状態遷移計算
    prev_state = _load_state(state_path)
    transitions = _compute_transitions(prev=prev_state.get("by_ticker", {}), curr=cand_dicts)

    # メール生成
    subject, text_body, html_body = render_radar_weekly_email(
        candidates=cand_dicts,
        transitions=transitions,
        universe_status=staleness.to_dict(),
        last_week_triggers=None,  # Phase 3 で実装
    )

    # 保存
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = out_dir / "candidates.json"
    candidates_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "universe_status": staleness.to_dict(),
                "candidates": cand_dicts,
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

    # state 更新（次回の状態遷移計算用）
    _save_state(state_path, cand_dicts)

    return {
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "candidates": cand_dicts,
        "universe_status": staleness.to_dict(),
        "transitions": transitions,
    }


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


def _save_state(path: Path, candidates: list[dict[str, Any]]) -> None:
    by_ticker: dict[str, dict[str, Any]] = {}
    for c in candidates:
        t = c.get("ticker")
        if isinstance(t, str) and t:
            by_ticker[t] = {"state": c.get("state"), "total": c.get("total")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "by_ticker": by_ticker,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


_STATE_RANK = {"out": 0, "watch": 1, "candidate": 2, "trigger": 3, "overheated": 1}


def _compute_transitions(
    *, prev: dict[str, Any], curr: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    promoted: list[dict[str, Any]] = []
    demoted: list[dict[str, Any]] = []
    new_in: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []

    curr_by_ticker = {c["ticker"]: c for c in curr if isinstance(c.get("ticker"), str)}
    for t, info in prev.items():
        if t not in curr_by_ticker:
            if (info or {}).get("state") not in (None, "out"):
                dropped.append({"ticker": t, "from": info.get("state"), "to": "out"})

    for t, c in curr_by_ticker.items():
        new_state = c.get("state")
        if new_state == "out":
            continue
        old = prev.get(t) or {}
        old_state = old.get("state")
        if old_state in (None, "out"):
            new_in.append({"ticker": t, "to": new_state})
            continue
        if _STATE_RANK.get(new_state, 0) > _STATE_RANK.get(old_state, 0):
            promoted.append({"ticker": t, "from": old_state, "to": new_state})
        elif _STATE_RANK.get(new_state, 0) < _STATE_RANK.get(old_state, 0):
            demoted.append({"ticker": t, "from": old_state, "to": new_state})

    return {
        "promoted": promoted,
        "demoted": demoted,
        "new_in": new_in,
        "dropped": dropped,
    }

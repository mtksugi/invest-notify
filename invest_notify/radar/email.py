"""週次サマリメール（[Radar Weekly]）の生成.

A 系統のメールとは独立した件名・フォーマットを使う。
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any


def _yahoo_url(ticker: str) -> str:
    t = (ticker or "").strip()
    if not t:
        return ""
    return f"https://finance.yahoo.com/quote/{t}"


def _fmt_pct(x: Any) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x) * 100:+.1f}%"
    except Exception:
        return "n/a"


def _fmt_x(x: Any) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.2f}x"
    except Exception:
        return "n/a"


def _fmt_pct_no_sign(x: Any) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x) * 100:.1f}%"
    except Exception:
        return "n/a"


def _fmt_num(x: Any, *, decimals: int = 2) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.{decimals}f}"
    except Exception:
        return "n/a"


def _fmt_consistency(x: Any) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.2f}"
    except Exception:
        return "n/a"


def _fmt_scores(d: Any) -> str:
    if not isinstance(d, dict):
        return "n/a"
    parts = []
    for k, v in d.items():
        try:
            parts.append(f"{k}={float(v):.2f}")
        except Exception:
            parts.append(f"{k}=n/a")
    return ", ".join(parts)


def _fmt_money(x: Any) -> str:
    if x is None:
        return "n/a"
    try:
        v = float(x)
    except Exception:
        return "n/a"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _yoy_chain_str(yoy_4q: list[Any] | None) -> str:
    if not yoy_4q:
        return "n/a"
    parts: list[str] = []
    for v in reversed(yoy_4q):
        parts.append(_fmt_pct(v))
    return " → ".join(parts)


_EVENT_LABEL = {
    "EARNINGS_NOTABLE": "決算",
    "TIER_UP": "昇格",
    "BREAKOUT": "ブレイク",
}


def _event_headline(e: dict[str, Any]) -> str:
    typ = e.get("type")
    if typ == "EARNINGS_NOTABLE":
        d = e.get("direction")
        mark = "▼ネガ決算" if d == "negative" else "▲特筆決算"
        return f"[{mark}] " + "・".join(e.get("reasons") or [])
    if typ == "TIER_UP":
        return f"[昇格] {e.get('from')} → {e.get('to')}"
    if typ == "BREAKOUT":
        return "[ブレイク] " + "・".join(e.get("reasons") or [])
    return f"[{_EVENT_LABEL.get(typ, typ)}]"


def render_radar_weekly_email(
    *,
    candidates: list[dict[str, Any]],
    earnings: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    transitions: dict[str, Any] | None = None,
    universe_status: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """イベント駆動メールを生成。returns (subject, text_body, html_body)."""
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    earnings = earnings or []
    events = events or []
    transitions = transitions or {}

    n_earn = len(earnings)
    n_events = len(events)
    n_promoted = int(transitions.get("promoted") or 0)
    n_demoted = int(transitions.get("demoted") or 0)
    n_new = int(transitions.get("new_in") or 0)

    n_trigger = sum(1 for c in candidates if c.get("state") == "trigger")
    n_cand = sum(1 for c in candidates if c.get("state") == "candidate")

    # 殿堂入り: trigger だが今週の新着イベント/決算が無い銘柄（反復させない）
    featured = {e.get("ticker") for e in (earnings + events)}
    hall = [c for c in candidates if c.get("state") == "trigger" and c.get("ticker") not in featured]
    hall_tickers = [str(c.get("ticker")) for c in sorted(hall, key=lambda c: float(c.get("total") or 0), reverse=True)]

    subject = f"[Radar Weekly] {today} 新着{n_events} / 決算{n_earn} / 昇格{n_promoted}"

    # ----------- text -----------
    L: list[str] = [subject, ""]
    if universe_status and universe_status.get("is_stale"):
        L += ["⚠ ユニバースが古くなっています:", f"  {universe_status.get('message', '')}", ""]
    elif universe_status:
        L += [f"[ユニバース] {universe_status.get('message', '')}", ""]

    L.append(f"== 今週の決算（特筆 {n_earn} 件） ==")
    if not earnings:
        L.append("（特筆すべき新決算はありません）")
    for i, e in enumerate(earnings, start=1):
        L.append("")
        L.append(f"{i}) {e.get('ticker')}  {_event_headline(e)}")
        L.append(_text_block_for_candidate(e.get("candidate") or {}))
    L.append("")

    L.append(f"== 今週の新着イベント（{n_events} 件） ==")
    if not events:
        L.append("（前回通知以降の新しい動きはありません。本系統は鳴らない週もあります。）")
    for i, e in enumerate(events, start=1):
        L.append("")
        L.append(f"{i}) {e.get('ticker')}  {_event_headline(e)}")
        L.append(_text_block_for_candidate(e.get("candidate") or {}))
    L.append("")

    if hall_tickers:
        L.append(f"== 殿堂入り（高スコア継続・新規材料なし {len(hall_tickers)} 件） ==")
        L.append("  " + ", ".join(hall_tickers[:40]))
        L.append("  ※新決算や上位昇格が出たら再掲します。")
        L.append("")

    L.append("== 状態遷移サマリ ==")
    L.append(f"  新規昇格 {n_promoted} / 降格 {n_demoted} / 新規入り {n_new}")
    L.append("")
    L.append("--")
    L.append(f"（参考）ユニバース trigger {n_trigger} / candidate {n_cand}")
    L.append("Radar の設計: docs/REDESIGN_v0.4.md")
    text_body = "\n".join(L)

    # ----------- html -----------
    H: list[str] = ['<html><body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; color:#222">']
    H.append(f"<h2>{html.escape(subject)}</h2>")
    if universe_status and universe_status.get("is_stale"):
        H.append(
            '<div style="background:#fff3cd;border:1px solid #ffeeba;padding:12px;border-radius:6px;margin-bottom:16px">'
            f"<strong>⚠ ユニバースが古くなっています</strong><br/>{html.escape(str(universe_status.get('message', '')))}</div>"
        )
    elif universe_status:
        H.append(
            '<div style="color:#666;margin-bottom:16px;font-size:12px">'
            f"[ユニバース] {html.escape(str(universe_status.get('message', '')))}</div>"
        )

    H.append(f"<h3>今週の決算（特筆 {n_earn} 件）</h3>")
    if not earnings:
        H.append("<p>特筆すべき新決算はありません。</p>")
    for e in earnings:
        H.append(f"<div style='font-weight:bold;margin-top:8px'>{html.escape(_event_headline(e))}</div>")
        H.append(_html_block_for_candidate(e.get("candidate") or {}))

    H.append(f"<h3>今週の新着イベント（{n_events} 件）</h3>")
    if not events:
        H.append("<p>前回通知以降の新しい動きはありません。本系統は鳴らない週もあります。</p>")
    for e in events:
        H.append(f"<div style='font-weight:bold;margin-top:8px'>{html.escape(_event_headline(e))}</div>")
        H.append(_html_block_for_candidate(e.get("candidate") or {}))

    if hall_tickers:
        H.append(f"<h3>殿堂入り（高スコア継続・新規材料なし {len(hall_tickers)} 件）</h3>")
        H.append(
            "<p style='color:#555;font-size:13px'>"
            + ", ".join(html.escape(t) for t in hall_tickers[:40])
            + "<br/><span style='color:#888'>※新決算や上位昇格が出たら再掲します。</span></p>"
        )

    H.append("<h3>状態遷移サマリ</h3>")
    H.append(f"<p>新規昇格 {n_promoted} / 降格 {n_demoted} / 新規入り {n_new}</p>")
    H.append(
        '<p style="color:#888;font-size:12px;margin-top:24px">'
        f"（参考）ユニバース trigger {n_trigger} / candidate {n_cand}<br/>設計: docs/REDESIGN_v0.4.md</p>"
    )
    H.append("</body></html>")
    html_body = "".join(H)

    return subject, text_body, html_body


def _text_block_for_candidate(c: dict[str, Any], *, idx: int | None = None) -> str:
    metrics = c.get("metrics") or {}
    period_label = "4Q" if (metrics.get("period_type") == "quarter") else "4Y"
    prefix = f"{idx}) " if idx is not None else "   "
    rows: list[str] = []
    rows.append(
        f"{prefix}{c.get('ticker')}  {c.get('name') or ''}  "
        f"市場規模 {_fmt_money(c.get('market_cap_usd'))} / セクター: {c.get('sector') or 'n/a'}"
    )
    rows.append(f"   - 売上 YoY ({period_label}): {_yoy_chain_str(metrics.get('revenue_yoy_4q'))}")
    rows.append(
        f"   - 営業利益率 ({period_label}): {_yoy_chain_str(metrics.get('operating_margin_4q'))}"
        f"  / consistency={_fmt_consistency(metrics.get('consistency_4q_growth'))}"
    )
    rfl = metrics.get("return_from_low_x")
    over_pct = metrics.get("over_sma_200_pct")
    rows.append(
        f"   - 株価: 底から {_fmt_x(rfl)} / 200日線 {_fmt_pct(over_pct)} / "
        f"出来高比 20:60 = {_fmt_x(metrics.get('vol_ratio_20_60'))}"
    )
    psr = metrics.get("latest_psr")
    pe = metrics.get("latest_pe")
    rows.append(
        f"   - PSR: {_fmt_num(psr, decimals=2)} / PER: {_fmt_num(pe, decimals=2)} / "
        f"希薄化YoY: {_fmt_pct(metrics.get('shares_diluted_yoy'))} / アナリスト: {metrics.get('analyst_count')}"
    )
    rows.append(
        f"   - total: {_fmt_num(c.get('total'), decimals=3)} / 内訳: {_fmt_scores(c.get('scores'))}"
    )
    if c.get("trigger_reasons"):
        rows.append(f"   - 理由: {', '.join(c.get('trigger_reasons') or [])}")
    rows.append(f"   - Yahoo: {_yahoo_url(c.get('ticker') or '')}")
    return "\n".join(rows)


def _html_block_for_candidate(c: dict[str, Any]) -> str:
    metrics = c.get("metrics") or {}
    period_label = "4Q" if (metrics.get("period_type") == "quarter") else "4Y"
    ticker = c.get("ticker") or ""
    link = _yahoo_url(ticker)
    parts: list[str] = []
    parts.append(
        '<div style="border:1px solid #ddd;border-radius:6px;padding:12px;margin-bottom:12px">'
    )
    parts.append(
        f"<div><strong><a href='{html.escape(link)}'>{html.escape(str(ticker))}</a></strong>"
        f" — {html.escape(str(c.get('name') or ''))}</div>"
    )
    parts.append(
        f"<div style='color:#555;font-size:13px'>"
        f"市場規模 {_fmt_money(c.get('market_cap_usd'))} / セクター {html.escape(str(c.get('sector') or 'n/a'))}"
        f"</div>"
    )
    parts.append("<ul style='font-size:13px'>")
    parts.append(f"<li>売上 YoY ({period_label}): {_yoy_chain_str(metrics.get('revenue_yoy_4q'))}</li>")
    parts.append(
        f"<li>営業利益率 ({period_label}): {_yoy_chain_str(metrics.get('operating_margin_4q'))}"
        f" / consistency={_fmt_consistency(metrics.get('consistency_4q_growth'))}</li>"
    )
    parts.append(
        f"<li>株価: 底から {_fmt_x(metrics.get('return_from_low_x'))} / "
        f"200日線 {_fmt_pct(metrics.get('over_sma_200_pct'))} / "
        f"出来高比 20:60 = {_fmt_x(metrics.get('vol_ratio_20_60'))}</li>"
    )
    parts.append(
        f"<li>PSR: {_fmt_num(metrics.get('latest_psr'), decimals=2)} / "
        f"PER: {_fmt_num(metrics.get('latest_pe'), decimals=2)} / "
        f"希薄化 YoY: {_fmt_pct(metrics.get('shares_diluted_yoy'))} / "
        f"アナリスト: {metrics.get('analyst_count')}</li>"
    )
    parts.append(
        f"<li>total: {_fmt_num(c.get('total'), decimals=3)} "
        f"<span style='color:#888;font-size:12px'>({html.escape(_fmt_scores(c.get('scores')))})</span></li>"
    )
    if c.get("trigger_reasons"):
        parts.append(f"<li>理由: {html.escape(', '.join(c.get('trigger_reasons') or []))}</li>")
    parts.append("</ul>")
    parts.append("</div>")
    return "".join(parts)

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


def render_radar_weekly_email(
    *,
    candidates: list[dict[str, Any]],
    transitions: dict[str, list[dict[str, Any]]] | None = None,
    universe_status: dict[str, Any] | None = None,
    last_week_triggers: list[dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    """returns (subject, text_body, html_body)."""
    today = datetime.now(timezone.utc).astimezone().date().isoformat()

    triggers = [c for c in candidates if c.get("state") == "trigger"]
    cands = [c for c in candidates if c.get("state") == "candidate"]
    overheated = [c for c in candidates if c.get("state") == "overheated"]

    transitions = transitions or {}
    promoted = transitions.get("promoted") or []
    demoted = transitions.get("demoted") or []
    new_in = transitions.get("new_in") or []
    dropped = transitions.get("dropped") or []

    n_trigger = len(triggers)
    n_cand = len(cands)
    n_overheated = len(overheated)
    n_promoted = len(promoted)

    subject = f"[Radar Weekly] {today} トリガ{n_trigger} / 候補{n_cand} / 昇格{n_promoted} / 過熱{n_overheated}"

    # ----------- text -----------
    L: list[str] = []
    L.append(subject)
    L.append("")

    if universe_status and universe_status.get("is_stale"):
        L.append("⚠ ユニバースが古くなっています:")
        L.append(f"  {universe_status.get('message', '')}")
        L.append("")
    elif universe_status:
        L.append(f"[ユニバース] {universe_status.get('message', '')}")
        L.append("")

    L.append(f"== 今週のトリガ（{n_trigger} 件） ==")
    if not triggers:
        L.append("（鳴っていません。これは異常ではなく、本系統は鳴らない週もあります。）")
    for i, c in enumerate(triggers[:10], start=1):
        L.append("")
        L.append(_text_block_for_candidate(c, idx=i))
    L.append("")

    L.append(f"== 候補トップ10（state=candidate, {n_cand} 件中） ==")
    for c in cands[:10]:
        L.append(
            f"- {c.get('ticker')}  total={c.get('total')}  "
            f"市場規模 {_fmt_money(c.get('market_cap_usd'))} / セクター: {c.get('sector') or 'n/a'}"
        )
    L.append("")

    if promoted or demoted or new_in or dropped:
        L.append("== 状態遷移（先週比） ==")
        for t in promoted:
            L.append(f"- 昇格: {t.get('ticker')} ({t.get('from')} → {t.get('to')})")
        for t in demoted:
            L.append(f"- 降格: {t.get('ticker')} ({t.get('from')} → {t.get('to')})")
        for t in new_in:
            L.append(f"- 新規入り: {t.get('ticker')} (state={t.get('to')})")
        for t in dropped:
            L.append(f"- 圏外: {t.get('ticker')} (state={t.get('from')} → out)")
        L.append("")

    if last_week_triggers:
        L.append("== 先週のトリガの事後動き ==")
        for x in last_week_triggers:
            L.append(
                f"- {x.get('ticker')} ({x.get('triggered_at')}): "
                f"事後リターン {x.get('post_return_pct', 'n/a')}"
            )
        L.append("")

    if overheated:
        L.append(f"== 過熱降格（{n_overheated} 件、参考） ==")
        for c in overheated[:10]:
            psr = c.get("metrics", {}).get("latest_psr")
            rfl = c.get("metrics", {}).get("return_from_low_x")
            L.append(
                f"- {c.get('ticker')}  PSR={_fmt_num(psr, decimals=2)} / 底から {_fmt_x(rfl)}"
            )
        L.append("")

    L.append("--")
    L.append("Radar の設計: docs/REDESIGN_v0.3.md")
    text_body = "\n".join(L)

    # ----------- html -----------
    H: list[str] = ['<html><body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; color:#222">']
    H.append(f"<h2>{html.escape(subject)}</h2>")

    if universe_status and universe_status.get("is_stale"):
        H.append(
            '<div style="background:#fff3cd;border:1px solid #ffeeba;padding:12px;border-radius:6px;margin-bottom:16px">'
            f"<strong>⚠ ユニバースが古くなっています</strong><br/>"
            f"{html.escape(str(universe_status.get('message', '')))}"
            "</div>"
        )
    elif universe_status:
        H.append(
            '<div style="color:#666;margin-bottom:16px;font-size:12px">'
            f"[ユニバース] {html.escape(str(universe_status.get('message', '')))}"
            "</div>"
        )

    H.append(f"<h3>今週のトリガ（{n_trigger} 件）</h3>")
    if not triggers:
        H.append("<p>鳴っていません。本系統は鳴らない週もあります。</p>")
    for c in triggers[:10]:
        H.append(_html_block_for_candidate(c))

    H.append(f"<h3>候補トップ10（state=candidate, {n_cand} 件中）</h3>")
    H.append("<table style='border-collapse:collapse'>")
    H.append("<tr style='background:#f5f5f5'><th style='padding:6px 10px;text-align:left'>Ticker</th><th>total</th><th>市場規模</th><th>セクター</th></tr>")
    for c in cands[:10]:
        ticker = c.get("ticker") or ""
        link = _yahoo_url(ticker)
        H.append(
            f"<tr><td style='padding:4px 10px'><a href='{html.escape(link)}'>{html.escape(str(ticker))}</a></td>"
            f"<td style='padding:4px 10px'>{c.get('total')}</td>"
            f"<td style='padding:4px 10px'>{_fmt_money(c.get('market_cap_usd'))}</td>"
            f"<td style='padding:4px 10px'>{html.escape(str(c.get('sector') or 'n/a'))}</td></tr>"
        )
    H.append("</table>")

    if promoted or demoted or new_in or dropped:
        H.append("<h3>状態遷移（先週比）</h3>")
        H.append("<ul>")
        for t in promoted:
            H.append(f"<li>昇格: <strong>{html.escape(str(t.get('ticker')))}</strong> ({t.get('from')} → {t.get('to')})</li>")
        for t in demoted:
            H.append(f"<li>降格: {html.escape(str(t.get('ticker')))} ({t.get('from')} → {t.get('to')})</li>")
        for t in new_in:
            H.append(f"<li>新規入り: {html.escape(str(t.get('ticker')))} (state={t.get('to')})</li>")
        for t in dropped:
            H.append(f"<li>圏外: {html.escape(str(t.get('ticker')))} ({t.get('from')} → out)</li>")
        H.append("</ul>")

    if last_week_triggers:
        H.append("<h3>先週のトリガの事後動き</h3>")
        H.append("<ul>")
        for x in last_week_triggers:
            H.append(
                f"<li>{html.escape(str(x.get('ticker')))} ({x.get('triggered_at')}): "
                f"事後リターン {html.escape(str(x.get('post_return_pct', 'n/a')))}</li>"
            )
        H.append("</ul>")

    H.append('<p style="color:#888;font-size:12px;margin-top:24px">設計: docs/REDESIGN_v0.3.md</p>')
    H.append("</body></html>")
    html_body = "".join(H)

    return subject, text_body, html_body


def _text_block_for_candidate(c: dict[str, Any], *, idx: int) -> str:
    metrics = c.get("metrics") or {}
    period_label = "4Q" if (metrics.get("period_type") == "quarter") else "4Y"
    rows: list[str] = []
    rows.append(
        f"{idx}) {c.get('ticker')}  {c.get('name') or ''}  "
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

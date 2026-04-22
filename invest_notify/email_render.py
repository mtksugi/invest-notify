from __future__ import annotations

from datetime import datetime, timezone
import html
import os
from typing import Any


def _yahoo_finance_url(ticker: str) -> str:
    t = (ticker or "").strip()
    if not t:
        return ""
    # ^N225 など指数は通知対象外にしているが、念のためURL生成も抑制
    if t.startswith("^"):
        return ""
    return f"https://finance.yahoo.com/quote/{t}"


def _load_watch_set(*, watch_tickers: list[str] | None) -> set[str]:
    if watch_tickers is not None:
        return {x.strip().upper() for x in watch_tickers if isinstance(x, str) and x.strip()}
    raw = os.environ.get("INVEST_NOTIFY_WATCH_TICKERS", "").strip()
    if not raw:
        return set()
    return {x.strip().upper() for x in raw.split(",") if x.strip()}


def render_email(notifications: list[dict[str, Any]], *, watch_tickers: list[str] | None = None) -> tuple[str, str, str]:
    """
    returns: (subject, text_body, html_body)
    """
    watch_set = _load_watch_set(watch_tickers=watch_tickers)
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    confirmed = [n for n in notifications if n.get("lane") == "confirmed"]
    early = [n for n in notifications if n.get("lane") == "early_warning"]

    def is_watch(n: dict[str, Any]) -> bool:
        t = str(n.get("ticker") or "").strip().upper()
        return bool(t and t in watch_set)

    def is_watch_bucket(n: dict[str, Any]) -> bool:
        return n.get("bucket") == "watch"

    c_watch_extra = sum(1 for n in confirmed if is_watch_bucket(n))
    e_watch_extra = sum(1 for n in early if is_watch_bucket(n))
    c_main = len(confirmed) - c_watch_extra
    e_main = len(early) - e_watch_extra

    if c_watch_extra or e_watch_extra:
        subject = f"{today} 確度高{c_main}件(+注視{c_watch_extra}件) / 早期警戒{e_main}件(+注視{e_watch_extra}件)"
    else:
        subject = f"{today} 確度高{len(confirmed)}件 / 早期警戒{len(early)}件"

    # -------- text --------
    lines_text: list[str] = []
    lines_text.append(subject)
    lines_text.append("")

    def _pre_return_label(n: dict[str, Any]) -> str:
        pre = n.get("pre_return_gate_pct")
        pre_s = n.get("pre_return_gate_signed_pct")
        w = n.get("pre_return_gate_window_days")
        if pre is None:
            return ""
        try:
            pre_f = float(pre)
            w_i = int(w) if w is not None else 5
        except Exception:
            return ""
        signed_str = ""
        if pre_s is not None:
            try:
                signed_str = f" / 方向調整後 {float(pre_s):+.1f}%"
            except Exception:
                signed_str = ""
        action = n.get("price_gate_action")
        action_str = ""
        if action == "downgrade" or action == "downgraded":
            action_str = " ⚠後追い降格"
        return f"{pre_f:+.1f}%({w_i}d){signed_str}{action_str}"

    def _render_items_text(items: list[dict[str, Any]]):
        for idx, n in enumerate(items, start=1):
            ticker = str(n.get("ticker") or "").strip()
            category = str(n.get("category") or "").strip()
            extra = " [別枠]" if is_watch_bucket(n) else ""
            lines_text.append(f"#### {idx}. {ticker} / {category} / conf={n.get('confidence')}{extra}")
            yf = _yahoo_finance_url(ticker)
            if yf:
                lines_text.append(f"Yahoo: {yf}")
            pre_label = _pre_return_label(n)
            if pre_label:
                lines_text.append(f"直近株価変動: {pre_label}")
            lines_text.append(str(n.get("summary", "")).strip())
            lines_text.append("")
            lines_text.append("- 影響: " + str(n.get("impact_direction")))
            lines_text.append("- 織り込み前の可能性: " + " / ".join(n.get("why_not_priced_in", [])[:3]))
            lines_text.append("- 未確認点: " + " / ".join(n.get("unknowns", [])[:3]))
            lines_text.append("- 次の確認: " + " / ".join(n.get("next_checks", [])[:3]))
            ev = n.get("evidence") or []
            if isinstance(ev, list) and ev:
                lines_text.append("- 根拠:")
                for e in ev[:5]:
                    if isinstance(e, dict):
                        lines_text.append(f"  - {e.get('source_type')}: {e.get('title') or ''} {e.get('url')}")
            lines_text.append("")

    def section_text(title: str, items: list[dict[str, Any]]):
        lines_text.append(f"## {title}（{len(items)}件）")
        if not items:
            lines_text.append("（なし）")
            lines_text.append("")
            return
        watch_items = [n for n in items if is_watch(n)]
        other_items = [n for n in items if not is_watch(n)]

        watch_extra = sum(1 for n in watch_items if is_watch_bucket(n))
        lines_text.append(f"### 注視ティッカー（{len(watch_items)}件 / 別枠{watch_extra}件）")
        if watch_items:
            _render_items_text(watch_items)
        else:
            lines_text.append("（なし）")
            lines_text.append("")

        lines_text.append(f"### その他（{len(other_items)}件）")
        if other_items:
            _render_items_text(other_items)
        else:
            lines_text.append("（なし）")
            lines_text.append("")

    section_text("確度高", confirmed)
    section_text("早期警戒", early)

    text_body = "\n".join(lines_text).rstrip() + "\n"

    # -------- html --------
    def esc(s: Any) -> str:
        return html.escape(str(s or ""), quote=True)

    def p_list(items: list[str]) -> str:
        li = "\n".join([f"<li>{esc(x)}</li>" for x in items if isinstance(x, str) and x.strip()])
        return f"<ul>{li}</ul>" if li else "<ul></ul>"

    def evidence_list(ev: Any) -> str:
        if not isinstance(ev, list) or not ev:
            return "<ul></ul>"
        lis: list[str] = []
        for e in ev[:8]:
            if not isinstance(e, dict):
                continue
            url = str(e.get("url") or "").strip()
            title = str(e.get("title") or "").strip()
            st = str(e.get("source_type") or "").strip()
            if not url:
                continue
            label = f"{st}: {title}" if title else st
            lis.append(f'<li><a href="{esc(url)}">{esc(label) or esc(url)}</a></li>')
        return "<ul>" + "\n".join(lis) + "</ul>"

    def section_html(title: str, items: list[dict[str, Any]]) -> str:
        if not items:
            return f"<h2>{esc(title)}（0件）</h2><p>（なし）</p>"
        blocks: list[str] = [f"<h2>{esc(title)}（{len(items)}件）</h2>"]

        def render_group(group_title: str, group_items: list[dict[str, Any]], *, show_bucket: bool):
            if group_title:
                blocks.append(f"<h3>{esc(group_title)}（{len(group_items)}件）</h3>")
            if not group_items:
                blocks.append("<p>（なし）</p>")
                return
            for idx, n in enumerate(group_items, start=1):
                ticker = str(n.get("ticker") or "").strip()
                category = str(n.get("category") or "").strip()
                conf = n.get("confidence")
                yf = _yahoo_finance_url(ticker)
                extra = " [別枠]" if (show_bucket and is_watch_bucket(n)) else ""
                header = f"{idx}. {ticker} / {category} / conf={conf}{extra}"
                blocks.append(f"<h4>{esc(header)}</h4>")
                if yf:
                    blocks.append(f'<p><a href="{esc(yf)}">Yahoo Finance: {esc(ticker)}</a></p>')
                pre_label = _pre_return_label(n)
                if pre_label:
                    blocks.append(f"<p><b>直近株価変動</b>: {esc(pre_label)}</p>")
                blocks.append(f"<p>{esc(str(n.get('summary') or '').strip())}</p>")
                blocks.append(f"<p><b>影響</b>: {esc(n.get('impact_direction'))}</p>")
                blocks.append(f"<p><b>織り込み前の可能性</b></p>{p_list(list(n.get('why_not_priced_in', [])[:3]))}")
                blocks.append(f"<p><b>未確認点</b></p>{p_list(list(n.get('unknowns', [])[:3]))}")
                blocks.append(f"<p><b>次の確認</b></p>{p_list(list(n.get('next_checks', [])[:3]))}")
                blocks.append(f"<p><b>根拠</b></p>{evidence_list(n.get('evidence') or [])}")

        watch_items = [n for n in items if is_watch(n)]
        other_items = [n for n in items if not is_watch(n)]
        watch_extra = sum(1 for n in watch_items if is_watch_bucket(n))

        render_group(f"注視ティッカー（別枠{watch_extra}件）", watch_items, show_bucket=True)
        render_group("その他", other_items, show_bucket=False)
        return "\n".join(blocks)

    style = """
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Hiragino Sans', 'Noto Sans JP', Arial, sans-serif; line-height: 1.5; }
    h1 { font-size: 18px; margin: 0 0 12px; }
    h2 { font-size: 16px; margin-top: 20px; }
    h3 { font-size: 14px; margin-top: 16px; }
    h4 { font-size: 13px; margin-top: 14px; }
    p, li { font-size: 13px; }
    code { background: #f4f4f4; padding: 0 4px; border-radius: 3px; }
    a { color: #1a73e8; text-decoration: none; }
    a:hover { text-decoration: underline; }
    """
    html_body = (
        "<!doctype html>"
        "<html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<style>{style}</style>"
        "</head><body>"
        f"<h1>{esc(subject)}</h1>"
        f"{section_html('確度高', confirmed)}"
        f"{section_html('早期警戒', early)}"
        "</body></html>"
    )

    return subject, text_body, html_body


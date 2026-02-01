from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def render_email(notifications: list[dict[str, Any]]) -> tuple[str, str]:
    """
    returns: (subject, body)
    """
    today = datetime.now(timezone.utc).astimezone().date().isoformat()
    confirmed = [n for n in notifications if n.get("lane") == "confirmed"]
    early = [n for n in notifications if n.get("lane") == "early_warning"]

    subject = f"{today} 確度高{len(confirmed)}件 / 早期警戒{len(early)}件"

    lines: list[str] = []
    lines.append(subject)
    lines.append("")

    def section(title: str, items: list[dict[str, Any]]):
        lines.append(f"## {title}（{len(items)}件）")
        if not items:
            lines.append("（なし）")
            lines.append("")
            return
        for idx, n in enumerate(items, start=1):
            lines.append(f"### {idx}. {n.get('ticker')} / {n.get('category')} / conf={n.get('confidence')}")
            lines.append(str(n.get("summary", "")).strip())
            lines.append("")
            lines.append("- 影響: " + str(n.get("impact_direction")))
            lines.append("- 織り込み前の可能性: " + " / ".join(n.get("why_not_priced_in", [])[:3]))
            lines.append("- 未確認点: " + " / ".join(n.get("unknowns", [])[:3]))
            lines.append("- 次の確認: " + " / ".join(n.get("next_checks", [])[:3]))
            ev = n.get("evidence") or []
            if isinstance(ev, list) and ev:
                lines.append("- 根拠:")
                for e in ev[:5]:
                    if isinstance(e, dict):
                        lines.append(f"  - {e.get('source_type')}: {e.get('title') or ''} {e.get('url')}")
            lines.append("")

    section("確度高", confirmed)
    section("早期警戒", early)

    body = "\n".join(lines).rstrip() + "\n"
    return subject, body


from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import strip_html
from .openai_compat import OpenAICompatConfig, chat_json
from .prompts import STAGE1_SYSTEM, stage1_user


def run_stage1(
    *,
    cfg: OpenAICompatConfig,
    fragments_path: str | Path,
    out_path: str | Path,
    max_fragments: int = 200,
    max_text_chars_per_fragment: int = 400,
    chunk_size: int = 10,
) -> dict[str, Any]:
    fragments = json.loads(Path(fragments_path).read_text(encoding="utf-8"))
    if not isinstance(fragments, list):
        raise ValueError("fragments must be a JSON array")

    compact = _compact_fragments(
        fragments,
        max_items=max_fragments,
        max_text_chars=max_text_chars_per_fragment,
    )
    # 200件まとめて投げるとモデル/回線によってタイムアウトしやすいので、分割してイベント化してマージする（MVP）。
    events_all: list[dict[str, Any]] = []
    if chunk_size <= 0:
        chunk_size = 10
    for idx in range(0, len(compact), chunk_size):
        chunk = compact[idx : idx + chunk_size]
        print(
            f"[stage1] chunk {idx//chunk_size+1}/{(len(compact)+chunk_size-1)//chunk_size} items={len(chunk)}",
            flush=True,
        )
        payload = json.dumps(chunk, ensure_ascii=False)
        resp_part = chat_json(
            cfg=cfg,
            system=STAGE1_SYSTEM,
            user=stage1_user(payload),
            temperature=None,
            max_tokens=8000,
        )
        _basic_stage1_validate(resp_part)
        part_events = resp_part.get("events", [])
        if isinstance(part_events, list):
            events_all.extend([e for e in part_events if isinstance(e, dict)])
        print(f"[stage1] chunk done events_total={len(events_all)}", flush=True)

    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    resp = {"generated_at": now_iso, "events": _renumber_events(events_all)}

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(resp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resp


def _renumber_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, e in enumerate(events, start=1):
        e2 = dict(e)
        e2["event_key"] = f"evt_{i:03d}"
        out.append(e2)
    return out


def _compact_fragments(fragments: list[dict], *, max_items: int, max_text_chars: int) -> list[dict]:
    out: list[dict] = []
    for f in fragments[: max(0, int(max_items))]:
        if not isinstance(f, dict):
            continue
        text = str(f.get("text", "") or "")
        text = strip_html(text)
        if len(text) > max_text_chars:
            text = text[:max_text_chars] + "…"
        out.append(
            {
                "source_type": f.get("source_type"),
                "source_name": f.get("source_name"),
                "published_at": f.get("published_at"),
                "url": f.get("url"),
                "title": f.get("title"),
                "lang": f.get("lang"),
                "text": text,
            }
        )
    return out


def _basic_stage1_validate(obj: dict[str, Any]) -> None:
    if not isinstance(obj, dict):
        raise ValueError("stage1 output must be an object")
    if "events" not in obj or not isinstance(obj["events"], list):
        raise ValueError("stage1 output must contain events[]")
    if "generated_at" not in obj:
        # 緩め：無ければ後で補うこともできるが、プロンプトに従ってほしい
        raise ValueError("stage1 output must contain generated_at")


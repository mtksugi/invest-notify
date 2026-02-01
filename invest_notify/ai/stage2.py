from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .openai_compat import OpenAICompatConfig, chat_json
from .prompts import STAGE2_SYSTEM, stage2_user


def run_stage2(
    *,
    cfg: OpenAICompatConfig,
    stage1_path: str | Path,
    out_path: str | Path,
    chunk_size: int = 25,
    auto_fix_summary: bool = True,
) -> dict[str, Any]:
    stage1 = json.loads(Path(stage1_path).read_text(encoding="utf-8"))
    if not isinstance(stage1, dict):
        raise ValueError("stage1 must be a JSON object")

    events = stage1.get("events", [])
    if not isinstance(events, list):
        raise ValueError("stage1.events must be an array")

    # stage1が大きいので分割して、第2段を複数回実行し候補を集約する（MVP）
    all_notifs: list[dict[str, Any]] = []
    if chunk_size <= 0:
        chunk_size = 25

    total_chunks = (len(events) + chunk_size - 1) // chunk_size
    for i in range(total_chunks):
        chunk = events[i * chunk_size : (i + 1) * chunk_size]
        compact = _compact_events(chunk)
        payload = json.dumps({"generated_at": stage1.get("generated_at"), "events": compact}, ensure_ascii=False)
        print(f"[stage2] chunk {i+1}/{total_chunks} events={len(chunk)}", flush=True)
        resp_part = chat_json(cfg=cfg, system=STAGE2_SYSTEM, user=stage2_user(payload), temperature=None, max_tokens=8000)
        _basic_stage2_validate(resp_part)
        part = resp_part.get("notifications", [])
        if isinstance(part, list):
            all_notifs.extend([n for n in part if isinstance(n, dict)])

    # 集約後に lane ごとに confidence 上位3件に絞る
    merged = _cap_notifications(all_notifs)
    if auto_fix_summary:
        # summaryの文字数制約（300〜600）を満たすまで補正する（MVPの安定化）
        merged = _fix_summaries(cfg, merged)
    resp = {"generated_at": stage1.get("generated_at"), "notifications": merged}

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(resp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resp


def _basic_stage2_validate(obj: dict[str, Any]) -> None:
    if not isinstance(obj, dict):
        raise ValueError("stage2 output must be an object")
    notifs = obj.get("notifications")
    if not isinstance(notifs, list):
        raise ValueError("stage2 output must contain notifications[]")


def _compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    第2段に渡す情報を絞る（トークン節約）。
    evidenceはURL中心、最大3件。
    """
    out: list[dict[str, Any]] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        ev = e.get("evidence") or []
        ev2: list[dict[str, Any]] = []
        if isinstance(ev, list):
            for x in ev[:3]:
                if isinstance(x, dict):
                    ev2.append(
                        {
                            "url": x.get("url"),
                            "source_type": x.get("source_type"),
                            "title": x.get("title"),
                            "published_at": x.get("published_at"),
                        }
                    )
        out.append(
            {
                "event_key": e.get("event_key"),
                "title": e.get("title"),
                "summary": e.get("summary"),
                "timeline": e.get("timeline", [])[:3] if isinstance(e.get("timeline"), list) else [],
                "candidate_categories": e.get("candidate_categories", []),
                "candidate_tickers": e.get("candidate_tickers", []),
                "source_types": e.get("source_types", []),
                "evidence": ev2,
                "what_changed": e.get("what_changed"),
                "open_questions": e.get("open_questions", [])[:3] if isinstance(e.get("open_questions"), list) else [],
            }
        )
    return out


def _cap_notifications(notifs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def conf(n: dict[str, Any]) -> float:
        v = n.get("confidence")
        try:
            return float(v)
        except Exception:
            return 0.0

    confirmed = [n for n in notifs if n.get("lane") == "confirmed"]
    early = [n for n in notifs if n.get("lane") == "early_warning"]
    confirmed = sorted(confirmed, key=conf, reverse=True)[:3]
    early = sorted(early, key=conf, reverse=True)[:3]

    # 重複キー（ticker:category）は先勝ち
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for n in confirmed + early:
        t = str(n.get("ticker") or "").strip()
        c = str(n.get("category") or "").strip()
        k = f"{t}:{c}"
        if k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


_SUMMARY_FIX_SYSTEM = """あなたは文章編集者です。
与えられた情報をもとに、summary を日本語で300〜600文字に収まるように書き直してください。

制約:
- 出力はJSONのみ（この形だけ）: {"summary":"..."}
- 300〜600文字を厳守（短すぎ/長すぎ不可）
- 内容は元の意図を保ち、判断材料（未確認点/次の確認の導線）に繋がるように
"""


def _fix_summaries(cfg: OpenAICompatConfig, notifs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fixed: list[dict[str, Any]] = []
    for n in notifs:
        s = str(n.get("summary") or "").strip()
        if 300 <= len(s) <= 600:
            fixed.append(n)
            continue
        # LLMに渡す入力を最小化（長さ制限/空出力対策）
        user = json.dumps(
            {
                "ticker": n.get("ticker"),
                "category": n.get("category"),
                "lane": n.get("lane"),
                "impact_direction": n.get("impact_direction"),
                "summary": s,
                "why_not_priced_in": n.get("why_not_priced_in", [])[:3],
                "unknowns": n.get("unknowns", [])[:3],
                "next_checks": n.get("next_checks", [])[:3],
            },
            ensure_ascii=False,
        )
        # たまに300未満で返るので、最大2回だけ再試行して文字数を満たすまで調整する
        new_summary: str | None = None
        for _ in range(2):
            resp = chat_json(cfg=cfg, system=_SUMMARY_FIX_SYSTEM, user=user, temperature=None, max_tokens=2000)
            if isinstance(resp, dict) and isinstance(resp.get("summary"), str):
                cand = resp["summary"].strip()
                if 300 <= len(cand) <= 600:
                    new_summary = cand
                    break
                # 不足している場合は「もう少し詳しく」を追記して再依頼
                user = json.dumps({**json.loads(user), "note": "300〜600文字に収まるようにもう少し具体を追加して"}, ensure_ascii=False)
        if new_summary is not None:
            n2 = dict(n)
            n2["summary"] = new_summary
            fixed.append(n2)
        else:
            fixed.append(n)
    return fixed


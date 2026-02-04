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
    max_confirmed: int = 3,
    max_early_warning: int = 3,
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
        resp_part = chat_json(
            cfg=cfg,
            system=STAGE2_SYSTEM,
            user=stage2_user(payload, max_confirmed=max_confirmed, max_early_warning=max_early_warning),
            temperature=None,
            max_tokens=8000,
        )
        _basic_stage2_validate(resp_part)
        part = resp_part.get("notifications", [])
        if isinstance(part, list):
            all_notifs.extend([n for n in part if isinstance(n, dict)])

    # LLM出力を運用ルールに寄せる（特にIRのスコープ制限）
    frag_text_by_url = _try_load_fragment_text_by_url(stage1_path=stage1_path)
    all_notifs = _postprocess_llm_notifications(all_notifs, frag_text_by_url=frag_text_by_url)

    # 集約後に lane ごとに confidence 上位3件に絞る
    merged = _cap_notifications(all_notifs, max_confirmed=max_confirmed, max_early_warning=max_early_warning)
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


def _try_load_fragment_text_by_url(*, stage1_path: str | Path) -> dict[str, str]:
    """
    postprocessでIRのItem番号などを拾うため、収集済み断片の text をURLで引けるようにする。
    - stage2単体実行でも効くよう、stage1ファイルと同じdataディレクトリの fragments.json を探す。
    - 見つからない/壊れている場合は空を返す（後処理は通知文だけで判定）。
    """
    try:
        p1 = Path(stage1_path)
        candidates = [p1.parent / "fragments.json", Path("data/fragments.json")]
        fp = None
        for c in candidates:
            if c.exists():
                fp = c
                break
        if fp is None:
            return {}
        raw = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return {}
        out: dict[str, str] = {}
        for f in raw:
            if not isinstance(f, dict):
                continue
            url = f.get("url")
            text = f.get("text")
            if isinstance(url, str) and isinstance(text, str) and url and text:
                out[url] = text
        return out
    except Exception:
        return {}


def _postprocess_llm_notifications(
    notifs: list[dict[str, Any]], *, frag_text_by_url: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """
    LLMが「IR=8-K通知」になりがちなので、MVP仕様（IRは限定）に寄せる。

    方針:
    - category=ir は「業績修正/ガイダンス」「資本政策」「破綻/継続企業/重大インシデント」以外は原則通知しない
    - ただし、M&A/資産取得・処分などは business_B2 に寄せる（IRに閉じない）
    - 人事/報酬/Reg FDのみの8-Kは落とす
    """

    allowed_categories = {"geopolitics", "business_B2", "ir", "lawsuit"}

    def _is_index_ticker(ticker: str) -> bool:
        # MVP方針：指数/市場全体（^N225 等）は原則通知しない
        return ticker.startswith("^")

    def _evidence_hosts(n: dict[str, Any]) -> set[str]:
        """
        newsの裏取りを雑に判定するため、evidence URLのホスト名集合を返す。
        - source_type=news のみ対象
        - URLが壊れている場合は無視
        """
        from urllib.parse import urlparse

        ev = n.get("evidence")
        if not isinstance(ev, list):
            return set()
        hosts: set[str] = set()
        for e in ev[:10]:
            if not isinstance(e, dict):
                continue
            if e.get("source_type") != "news":
                continue
            u = e.get("url")
            if not isinstance(u, str) or not u:
                continue
            try:
                h = urlparse(u).hostname or ""
                h = h.lower().strip()
                if h:
                    hosts.add(h)
            except Exception:
                continue
        return hosts

    def _has_primary_ir_evidence(n: dict[str, Any]) -> bool:
        ev = n.get("evidence")
        if not isinstance(ev, list):
            return False
        for e in ev[:10]:
            if isinstance(e, dict) and e.get("source_type") == "ir":
                return True
        return False

    def _text(n: dict[str, Any]) -> str:
        parts: list[str] = []
        for k in ("summary",):
            v = n.get(k)
            if isinstance(v, str):
                parts.append(v)
        for k in ("why_not_priced_in", "unknowns", "next_checks"):
            v = n.get(k)
            if isinstance(v, list):
                parts.extend([x for x in v if isinstance(x, str)])
        # evidence URLに紐づく断片textも混ぜる（SECのItem番号などを拾うため）
        if frag_text_by_url:
            ev = n.get("evidence")
            if isinstance(ev, list):
                for e in ev[:5]:
                    if isinstance(e, dict):
                        u = e.get("url")
                        if isinstance(u, str) and u in frag_text_by_url:
                            parts.append(frag_text_by_url[u])
        return "\n".join(parts).lower()

    def _items_mentioned(t: str) -> set[str]:
        """
        8-Kの Item x.xx をざっくり抽出する（完全一致でなくてOK）。
        例: "Item 1.01" / "Items 1.01, 3.02" / "item 7.01"
        """
        import re

        out: set[str] = set()
        for m in re.finditer(r"\bitem[s]?\s+(\d\.\d{2})\b", t, flags=re.IGNORECASE):
            out.add(m.group(1))
        return out

    # 「芽（数か月後に効きやすい）」になりやすいIRシグナル（許可制）
    ir_signal_items = {
        # 資本政策/権利変更
        "3.02",  # unregistered sales
        "3.03",  # rights modifications
        # 信用不安/破綻・コベナンツ
        "1.03",  # bankruptcy/receivership
        "2.03",  # obligations
        "2.04",  # triggering events / defaults
        # 会計・監査
        "4.01",  # auditor changes
        "4.02",  # non-reliance/restatement
        # 業績/ガイダンス（※定例決算は除外したいが、8-Kの2.02自体は「修正」も混ざる）
        "2.02",  # results of operations / financial condition
    }

    # 取引・構造変化（business_B2寄せが妥当になりやすい）
    transaction_items = {
        "1.01",  # material definitive agreement
        "2.01",  # acquisition/disposition
        "5.01",  # change in control
    }

    # ノイズになりやすい（単体なら落とす）
    noise_only_items = {"7.01", "9.01"}  # Reg FD / exhibits only

    # 人事/報酬寄りのシグナル（単体なら落とす）
    personnel_keywords = [
        "取締役",
        "役員",
        "退任",
        "就任",
        "人事",
        "報酬",
        "compensation",
        "director",
        "officer",
        "resign",
        "appointment",
    ]

    # IRとして残すためのキーワード（Item表記が無い場合の保険）
    ir_signal_keywords = [
        # 資本政策
        "増資",
        "希薄化",
        "株式発行",
        "転換社債",
        "convertible",
        "warrant",
        "自社株買い",
        "自己株式",
        "配当",
        "増配",
        "減配",
        "buyback",
        "repurchase",
        # 信用不安/会計
        "破産",
        "倒産",
        "receivership",
        "default",
        "going concern",
        "継続企業",
        "restatement",
        "non-reliance",
        "material weakness",
        # 当局/規制（lawsuit寄せもあり得るが、ここではIRの芽として保持）
        "調査",
        "enforcement",
        "subpoena",
        "制裁",
    ]

    # Item 2.02（決算/業績）を「定例決算」として落とすためのヒューリスティクス
    # - guidance / revision / withdraw 等の “変化” があるときだけ残す
    # - そうでなければ超低ノイズ方針として落とす
    earnings_noise_keywords = [
        "決算",
        "四半期",
        "通期",
        "業績",
        "results of operations",
        "financial condition",
        "earnings",
        "quarter",
        "q1",
        "q2",
        "q3",
        "q4",
        "fiscal",
    ]
    earnings_keep_keywords = [
        "ガイダンス",
        "見通し",
        "修正",
        "撤回",
        "guidance",
        "outlook",
        "revise",
        "revised",
        "update",
        "updated",
        "withdraw",
        "preliminary",
        "materially",
    ]

    # 取引系をbusiness_B2に寄せるためのキーワード（Itemだけで曖昧なら落とす）
    transaction_keywords = [
        "買収",
        "譲渡",
        "取得",
        "処分",
        "合併",
        "m&a",
        "acquisition",
        "disposition",
        "transaction",
        "closing",
        "tender offer",
        "asset",
        "重要契約",
        "material definitive agreement",
        "change in control",
        "支配",
        "株主権",
        "rights of security holders",
    ]

    def _set_lane(n: dict[str, Any], lane: str) -> dict[str, Any]:
        n2 = dict(n)
        n2["lane"] = lane
        return n2

    out: list[dict[str, Any]] = []
    for n in notifs:
        if not isinstance(n, dict):
            continue
        cat = str(n.get("category") or "").strip()
        ticker = str(n.get("ticker") or "").strip()

        # 許可カテゴリ以外（例: markets）は落とす
        if cat not in allowed_categories:
            continue
        # 指数は原則通知しない（仕様・方針に合わせてノイズ削減）
        if ticker and _is_index_ticker(ticker):
            continue

        # business_B2 の confirmed は厳格化：
        # - 一次ソース(ir)が無い
        # - かつ news が単一ドメインしか無い
        # なら early_warning に落とす（「報道1本で確度高」になりがちなのを抑制）
        if cat == "business_B2" and n.get("lane") == "confirmed":
            if (not _has_primary_ir_evidence(n)) and (len(_evidence_hosts(n)) < 2):
                n = dict(n)
                n["lane"] = "early_warning"

        if cat != "ir":
            out.append(n)
            continue
        t = _text(n)
        items = _items_mentioned(t)

        # Reg FD / exhibits だけの8-Kは落とす（芽になりにくい）
        if items and items.issubset(noise_only_items):
            continue

        # 人事/報酬“だけ”の匂いが強いものは落とす（芽になりにくい）
        if any(k.lower() in t for k in personnel_keywords) and not (items & ir_signal_items):
            continue

        # Item 2.02（決算/業績）は原則落とす。ガイダンス修正等の“変化”があるときだけ残す。
        if ("2.02" in items) and not (items & (ir_signal_items - {"2.02"})):
            if any(k.lower() in t for k in earnings_noise_keywords) and not any(k.lower() in t for k in earnings_keep_keywords):
                continue

        # 許可制：IRシグナルに該当すれば残す
        if (items & ir_signal_items) or any(k.lower() in t for k in ir_signal_keywords):
            # ここが肝：IRの“芽”は原則 early_warning に寄せる（数か月後の値動きの兆候）
            # ただし、破綻/デフォルト級は confirmed に残す。
            if items & {"1.03", "2.03", "2.04"}:
                out.append(_set_lane(n, "confirmed"))
            elif items & {"3.02", "3.03", "4.01", "4.02"}:
                out.append(_set_lane(n, "early_warning"))
            elif "2.02" in items:
                out.append(_set_lane(n, "early_warning"))
            else:
                # Itemが取れないがキーワードでIRシグナル判定したケースは早期警戒寄り
                out.append(_set_lane(n, "early_warning"))
            continue

        # 取引系はbusiness_B2へ寄せる（ただし曖昧なら落とす）
        if (items & transaction_items) and any(k.lower() in t for k in transaction_keywords):
            n2 = dict(n)
            n2["category"] = "business_B2"
            # 取引系は通常 “確定” に近いが、内容が薄いことも多いので lane は維持（LLMに委ねる）
            out.append(n2)
            continue

        # それ以外のIRは落とす（超低ノイズ優先）
        continue

    return out


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


def _cap_notifications(
    notifs: list[dict[str, Any]],
    *,
    max_confirmed: int,
    max_early_warning: int,
) -> list[dict[str, Any]]:
    def conf(n: dict[str, Any]) -> float:
        v = n.get("confidence")
        try:
            return float(v)
        except Exception:
            return 0.0

    confirmed = [n for n in notifs if n.get("lane") == "confirmed"]
    early = [n for n in notifs if n.get("lane") == "early_warning"]
    confirmed = sorted(confirmed, key=conf, reverse=True)[: max(0, int(max_confirmed))]
    early = sorted(early, key=conf, reverse=True)[: max(0, int(max_early_warning))]

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


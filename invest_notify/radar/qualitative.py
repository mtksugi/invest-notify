"""定性レイヤー（第三者的意見）.

ルールで絞ったショートリスト（決算/新着イベントに出た銘柄、週に数〜十数件）にだけ
LLM を当て、定量では測れない論点を付与する。設計は ``docs/REDESIGN_v0.4.md`` §12。

原則（v0.2 §0 を踏襲）:
- LLM は「編集者・批評者」であり、売買推奨はしない。
- 出力は人間が判断するための「論点」。憶測は unknowns に明示。
- §10 の構造的限界（景気循環株の混入）を補うため、テーマが構造成長か循環ポップかを判定する。
"""

from __future__ import annotations

import json
from typing import Any

from ..ai.openai_compat import OpenAICompatConfig, chat_json


QUALITATIVE_SYSTEM = """あなたは投資の「編集者・批評者」です。10バガー候補（3〜5x / 〜24か月狙い）の
スクリーニングを通過した1銘柄について、定量データと直近ニュースをもとに**定性的な論点**を出します。

厳守:
- **売買は推奨しない**（buy/sell を書かない）。人間が判断するための論点を提示するだけ。
- 与えられたデータ・ニュースに基づく。推測は unknowns に明示し、断定しない。
- 出力は **JSON のみ**。

評価軸:
- theme: この上昇の背後が「構造成長テーマ」か「景気循環の一時的な上振れ」か。
  - "structural": 持続的な需要・技術・規制テーマに接続（例: AIインフラ/電力, データセンター, 防衛, GLP-1, 電動化 等）
  - "cyclical": 市況・商品市況・運賃など循環要因で数字と株価が同時に振れている（例: 海運/タンカー運賃, 石炭/資源市況, メモリ市況）
  - "unclear": 判断材料が不足
- bull: なぜ 3〜5x の余地があり得るか（**1〜2文で簡潔に**、具体的に）。
- bear: 第三者（弱気側）として挙げる具体的リスク（会計の質/一過性売上/希薄化/競合/顧客集中/規制 など）。**各項目1文で簡潔に、2〜4件**。
- priced_in: この材料が市場にどれだけ織り込まれているか（"low"/"medium"/"high"）と短い理由。
- verdict: 人間向けの総括（1文、売買推奨ではない論点）。
- evidence_urls: 参照したニュースURL（あれば）。
- unknowns: 確認しきれなかった点。

出力JSONスキーマ:
{
  "theme": "structural|cyclical|unclear",
  "theme_label": "短いテーマ名",
  "bull": "...",
  "bear": ["...", "..."],
  "priced_in": "low|medium|high",
  "priced_in_reason": "...",
  "verdict": "...",
  "evidence_urls": ["..."],
  "unknowns": ["..."]
}
"""


def _candidate_brief(c: dict[str, Any]) -> dict[str, Any]:
    m = c.get("metrics") or {}
    return {
        "ticker": c.get("ticker"),
        "name": c.get("name"),
        "sector": c.get("sector"),
        "market_cap_usd": c.get("market_cap_usd"),
        "state": c.get("state"),
        "revenue_yoy_4q": m.get("revenue_yoy_4q"),
        "operating_margin_4q": m.get("operating_margin_4q"),
        "consistency_4q_growth": m.get("consistency_4q_growth"),
        "latest_psr": m.get("latest_psr"),
        "latest_pe": m.get("latest_pe"),
        "shares_diluted_yoy": m.get("shares_diluted_yoy"),
        "analyst_count": m.get("analyst_count"),
        "return_from_low_x": m.get("return_from_low_x"),
        "over_sma_200_pct": m.get("over_sma_200_pct"),
        "vol_ratio_20_60": m.get("vol_ratio_20_60"),
    }


def assess_candidate(
    llm_cfg: OpenAICompatConfig,
    *,
    candidate: dict[str, Any],
    news: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """1銘柄の定性評価を返す。失敗時は None（メールはルール部分だけで成立する）."""
    user = json.dumps(
        {
            "candidate": _candidate_brief(candidate),
            "recent_news": news,
            "instruction": (
                "上記の定量データと直近ニュースから、テーマ性（構造成長 vs 景気循環）、"
                "強気仮説、第三者としての弱気リスク、織り込み度を評価してください。"
                "ニュースが乏しい場合は theme を unclear にし、unknowns に明示してください。"
            ),
        },
        ensure_ascii=False,
    )
    resp: Any = None
    for _ in range(2):  # 一時的な空応答/JSON崩れに備えて軽くリトライ
        try:
            resp = chat_json(
                cfg=llm_cfg,
                system=QUALITATIVE_SYSTEM,
                user=user,
                temperature=None,
                # 推論モデル（reasoning）は思考トークンも消費するため上限は広めに取る。
                # 不足すると finish_reason=length で空出力になり JSON 化に失敗する。
                max_tokens=6000,
            )
        except Exception:
            resp = None
        if isinstance(resp, dict) and resp.get("theme") is not None:
            break
    if not isinstance(resp, dict):
        return None
    theme = resp.get("theme")
    if theme not in ("structural", "cyclical", "unclear"):
        resp["theme"] = "unclear"
    if not isinstance(resp.get("bear"), list):
        resp["bear"] = [str(resp.get("bear"))] if resp.get("bear") else []
    if resp.get("priced_in") not in ("low", "medium", "high"):
        resp["priced_in"] = "medium"
    return resp


def assess_shortlist(
    llm_cfg: OpenAICompatConfig,
    fmp_cfg: Any,
    *,
    candidates: list[dict[str, Any]],
    verbose: bool = True,
) -> dict[str, dict[str, Any]]:
    """ショートリスト（候補 dict のリスト）を定性評価し、ticker -> 評価 の辞書を返す.

    ニュース取得（FMP）→ LLM 評価 を銘柄ごとに行う。失敗した銘柄は結果に含めない。
    """
    from .news import recent_news

    out: dict[str, dict[str, Any]] = {}
    for c in candidates:
        t = str(c.get("ticker") or "").strip()
        if not t:
            continue
        try:
            news = recent_news(fmp_cfg, ticker=t)
        except Exception:
            news = []
        a = assess_candidate(llm_cfg, candidate=c, news=news)
        if a is not None:
            a["news_count"] = len(news)
            out[t] = a
            if verbose:
                print(f"[radar][qual] {t}: theme={a.get('theme')} priced_in={a.get('priced_in')} news={len(news)}", flush=True)
    return out

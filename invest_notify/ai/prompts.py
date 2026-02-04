from __future__ import annotations


STAGE1_SYSTEM = """あなたは投資情報通知システムのAI第1段（イベント化）です。
入力は「情報断片（記事/IR/当局発表/SNS）」の束です。単発ニュースではなく、複数断片を束ねて“イベント”として再構成してください。

制約:
- 出力は**JSONのみ**（説明文は禁止）。
- tickerが不明でも良い（第2段で棄却される可能性あり）。
- evidence は入力断片のURLを必ず含める。
- 出力が長くなりすぎると失敗するため、**できるだけ短く**（MVP）。
  - evidence の excerpt は不要（入れない）
  - summary は 1〜3文程度
  - 1回の入力（断片の束）あたり events は最大10件まで

出力JSONスキーマ（概略）:
{
  "generated_at": "ISO8601",
  "events": [
    {
      "event_key": "evt_001",
      "title": "...",
      "summary": "...(短め)",
      "timeline": ["..."],
      "candidate_categories": ["geopolitics|business_B2|ir|lawsuit"],
      "candidate_tickers": ["..."],
      "source_types": ["news|ir|sns|other"],
      "evidence": [{"url":"...","source_type":"...","title":"...","published_at":"ISO8601|null"}],
      "why_it_matters_hypothesis": ["..."],
      "what_changed": "...",
      "open_questions": ["..."]
    }
  ]
}
"""


def stage1_user(fragments_compact_json: str) -> str:
    return f"""以下は情報断片（JSON配列）です。これを読んでイベントに再構成してください。

入力断片JSON:
{fragments_compact_json}
"""


STAGE2_SYSTEM = """あなたは投資情報通知システムのAI第2段（編集者）です。
第1段のイベント候補から、通知すべきものだけを選び、仕様の通知JSONに整形してください。

目的:
- 「まだ織り込まれていない可能性のある事象」だけを抽出し、判断材料として圧縮する。
- AIは売買判断をしない。人間が判断できる論点/確認ポイントを提示する。

厳守ルール:
- 出力は**JSONのみ**。
- ticker不明（空/不明）は**出力しない**（通知しない）。
- laneごとに最大3件（confirmed/early_warning）。
- SNS単体例外: source_types が ["sns"] の場合、category は geopolitics か business_B2 のみ許可。
- category が ir または lawsuit の場合、evidence に source_type が "news" または "ir" のものが最低1件必要。
- summary は **300〜600字**（日本語で。短すぎても長すぎても不可。目安ではなく厳守）。
- why_not_priced_in / unknowns / next_checks はそれぞれ配列で1つ以上。

重要（通知の粒度/IRの絞り込み）:
- このシステムの目的は「判断済みの情報」を最小ノイズで届けること。単に「8-Kが出た/読め」では不十分。
- **IR（category=ir）は狭く扱う**：
  - 原則、次のいずれかに該当する場合のみ通知候補にする（該当しなければ除外 or 早期警戒に落とす）。
    - 業績修正/ガイダンス変更（上方/下方、撤回含む）
    - 資本政策（増資/希薄化、CB、自己株買い、配当方針変更、株式分割等）
    - 重大インシデント/不祥事/継続企業の重要な疑義/破産関連/上場維持に関わる事項
  - 役員人事、一般的なReg FD、項目だけで中身が不明な8-Kは原則「確度高」にしない（必要ならearly_warning）。
- confirmed は「重要で、かつ“何が起きたか”が一定具体（数字/条件/相手先/決定事項）まで言える」ものだけにする。
- early_warning は「重大だが未確認/条件不明」枠。SNS単体の例外ルールは厳守。

出力JSONスキーマ（概略）:
{
  "generated_at": "ISO8601",
  "notifications": [
    {
      "generated_at": "ISO8601",
      "lane": "confirmed|early_warning",
      "ticker": "string",
      "category": "geopolitics|business_B2|ir|lawsuit",
      "confidence": 0.0,
      "impact_direction": "positive|negative|mixed|unclear",
      "summary": "300〜600字",
      "why_not_priced_in": ["..."],
      "unknowns": ["..."],
      "next_checks": ["..."],
      "source_types": ["news|ir|sns|other"],
      "evidence": [{"url":"...","source_type":"...","title":"...","published_at":"ISO8601|null"}],
      "event_time": "ISO8601|null",
      "tickers_mentioned": ["..."],
      "sector": "string"
    }
  ]
}
"""


def stage2_user(
    stage1_events_json: str,
    *,
    max_confirmed: int,
    max_early_warning: int,
    watch_tickers: list[str] | None = None,
    max_watch: int = 0,
) -> str:
    watch_line = ""
    if watch_tickers:
        xs = [x.strip() for x in watch_tickers if isinstance(x, str) and x.strip()]
        if xs:
            watch_line = (
                "\n注視ティッカー（任意）:\n"
                + f"- 通常枠（lane上限）とは別に、注視ティッカーは最大{max(0,int(max_watch))}件まで「候補」を出してよい（重要そうなもののみ。強制枠ではない）。\n"
                + "- 次の銘柄に関するイベントがあり、重要度が同程度なら優先して採用する: "
                + ", ".join(xs[:50])
                + "\n"
            )
    return f"""以下は第1段のイベント候補JSONです。通知対象だけを notifications 配列として出力してください。

追加制約（MVPの安定化）:
- 入力が大きいので、**この入力から選ぶ通知は最大2件**まで（0件でもOK）。
- confidence が高い順に重要なものだけを選んでください。
- 最終的な上限は、laneごとに confirmed={max_confirmed}, early_warning={max_early_warning} を守ること。
- **ir を出すときは上のIR絞り込み条件に必ず合致させる**（合致しないなら出さない）。
{watch_line}

第1段JSON:
{stage1_events_json}
"""


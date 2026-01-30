## 投資情報通知システム 仕様書（MVP v0.2）

### 0. 目的（最重要）
市場に流通している大量の情報をAIに横断的に読ませ・再構成させることで、**「まだ織り込まれていない可能性のある事象」**だけを抽出し、**自分が判断できる形**に圧縮して、受動的に通知として受け取る。

- AIは**編集者**であり、**投資家ではない**（売買判断は行わない）
- 「点」ではなく「線/面」（複数断片の流れ）を重視する
- 「織り込み済み」っぽい分かりやすい材料や解説量産系は原則落とす

---

### 1. スコープ/運用
- **対象市場**：日本株 / 米国株
- **通知チャネル**：メール
- **実行頻度**：1日1回（バッチ）
- **通知件数上限**：
  - 確度高（confirmed）：0〜3件/日
  - 早期警戒（early_warning）：0〜3件/日
  - 0件の日も許容（超低ノイズ）
- **重複通知抑制**：同一イベントキーに対し **3日間**は再通知しない
- **同一イベントキー**：`(ticker, category)`（銘柄 × カテゴリ）
- **ticker不明の扱い**：**原則通知しない**

---

### 2. 収集（mixed）
複数ソース種別を混在させて「情報断片」を収集し、AIにまとめて渡す。

- 収集対象の具体（RSS/公式IR/ニュースAPI/SNS等）は後で拡張可能だが、MVPでは「断片のメタ付け」を必須要件とする
- **第1段AIへの入力は最大200件/日**

**200件を超えた場合のPython側の前処理（要件）**
- 同一URLは重複除去
- ソース種別が偏りすぎないように上限制御（例：news/ir/snsの配分）
- 長文は必要に応じて圧縮（総文字数を抑える）

---

### 3. 通知カテゴリ（v0.2）
第2段AIは、以下カテゴリに該当しないものを原則棄却する。

- **geopolitics（地政学）**
  - 制裁・紛争・輸出規制・海運/エネルギー/半導体サプライチェーン等、株式に波及しうる動き
- **business_B2（重大ニュース：B2）**
  - 企業行動/業界構造：M&A/TOB、大型契約、供給制限、価格戦争、重要提携/解消、当局の動き
  - ＋ マクロ重要指標/金融政策/金利/為替の非連続な変化（株式へ波及しうるもの）
- **ir（IR：限定）**
  - 業績修正/ガイダンス変更、資本政策（増資/自社株買い/配当方針変更等）、事故/不祥事/重大インシデント開示
  - 定例決算は原則除外
- **lawsuit（訴訟）**
  - 巨額賠償・差止/輸出停止・集団訴訟・当局提訴/調査開始など

---

### 4. AI構成（C：二段階）
#### 第1段：イベント化（再構成）
入力：情報断片（最大200件/日）  
出力：イベント候補リスト（ticker未確定でも可）

要件：
- 断片を「同一事象（イベント）」としてクラスタリング
- 単発ニュースでなく、流れ（線/面）としてまとめる
- 各イベントに、証拠（URL/抜粋）束を付与する

#### 第2段：編集・通知判定（超低ノイズ）
入力：第1段のイベント候補  
出力：通知対象イベント（メールに載せる最終形）

要件：
- tickerが確定できないイベントは原則棄却
- 「織り込み済み回避」を説明できないものは棄却
- 早期警戒（未確認）も許容する（b前提）  
  ただし、暴発防止の条件を満たせない場合は棄却

---

### 5. 早期警戒（SNS単体例外）のルール
早期警戒は「未確認だが重大」枠。以下を満たさないと通知してはいけない。

**SNS単体で早期警戒OKのカテゴリ**
- geopolitics（地政学）
- business_B2（重大ニュース：B2）

**SNS単体では通知しない（裏取り必須）**
- ir（IR）
- lawsuit（訴訟）

---

### 6. 通知コンテンツ要件（メール）
各イベントは **300〜600字**を目安に、必ず以下を含む（欠けたら棄却）。

- **イベント概要**（300〜600字の本文）
- **銘柄別の影響（必須）**：対象tickerごとに、影響方向（positive/negative/mixed/unclear）と理由
- **織り込み前の可能性の理由**（why_not_priced_in）
- **根拠URL/引用**（evidence）
- **未確認点**（unknowns）
- **次の確認ポイント**（next_checks）
- **判断の論点（1〜3点）**

メール構成：
- 件名：`YYYY-MM-DD 確度高X件 / 早期警戒Y件`
- 本文：確度高 → 早期警戒 の順で最大3件ずつ

---

### 7. データ契約（JSON）
#### 7.1 入力：情報断片（第1段AIへ）
最大200件/日。

```json
[
  {
    "source_type": "news",
    "source_name": "Example News",
    "published_at": "2026-01-23T01:23:45Z",
    "url": "https://example.com/article",
    "text": "本文テキスト...",
    "title": "任意：タイトル",
    "lang": "ja",
    "fetched_at": "2026-01-23T02:00:00Z",
    "tickers_hint": ["7203.T"]
  }
]
```

必須：`source_type`, `source_name`, `published_at(※null可)`, `url`, `text`  
任意：`title`, `lang`, `fetched_at`, `tickers_hint`

#### 7.2 第1段出力：イベント候補

```json
{
  "generated_at": "2026-01-23T03:00:00Z",
  "events": [
    {
      "event_key": "evt_001",
      "title": "短い見出し",
      "summary": "短め要約（200〜400字目安）",
      "timeline": ["時系列1", "時系列2"],
      "candidate_categories": ["business_B2"],
      "candidate_tickers": ["AAPL"],
      "source_types": ["news", "sns"],
      "evidence": [
        {
          "url": "https://example.com/article",
          "source_type": "news",
          "title": "記事タイトル",
          "published_at": "2026-01-23T01:23:45Z",
          "excerpt": "抜粋..."
        }
      ],
      "why_it_matters_hypothesis": ["任意：波及仮説"],
      "what_changed": "任意：変化点",
      "open_questions": ["任意：未確認点の種"]
    }
  ]
}
```

#### 7.3 第2段出力：通知イベント（最終形）
ticker不明は出力しない（＝通知しない）。第2段の出力をそのままメール生成に使う。

```json
{
  "generated_at": "2026-01-23T03:05:00Z",
  "lane": "confirmed",
  "ticker": "AAPL",
  "category": "business_B2",
  "confidence": 0.78,
  "impact_direction": "mixed",
  "summary": "300〜600字の本文...",
  "why_not_priced_in": ["理由1", "理由2"],
  "unknowns": ["未確認点1"],
  "next_checks": ["次に確認すること1"],
  "source_types": ["news", "sns"],
  "evidence": [
    {
      "url": "https://example.com/article",
      "source_type": "news",
      "title": "記事タイトル",
      "published_at": "2026-01-23T01:23:45Z"
    }
  ],
  "event_time": "2026-01-23T00:00:00Z",
  "tickers_mentioned": ["AAPL", "MSFT"],
  "sector": "任意：セクター"
}
```

必須：
- `generated_at`, `lane`, `ticker`, `category`, `confidence(0〜1)`, `impact_direction`, `summary(300〜600字)`
- `why_not_priced_in[]`, `unknowns[]`, `next_checks[]`, `source_types[]`, `evidence[]`

---

### 8. 検証ルール（Python側で機械的にチェック）
- 件数：`lane`ごとに最大3
- SNS単体例外：
  - `source_types == ["sns"]` の場合、`category`は `geopolitics` または `business_B2` のみ許可
- IR/訴訟の裏取り：
  - `category in ["ir","lawsuit"]` の場合、`evidence[].source_type` に `news` または `ir` を最低1つ含むこと
- 重複抑制：
  - `event_id = f"{ticker}:{category}"` を用い、直近3日以内の再通知を抑制（例外は将来拡張）


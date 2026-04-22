## invest_notify（MVP）

`SPEC_MVP_v0.2.md` の仕様に基づき、まずは **収集（コネクタ）→ 情報断片JSON生成** までを行うための最小実装です。

### できること（現時点）
- RSS/Atom から記事を取得し、仕様の「情報断片JSON（最大200件）」を出力する
  - 重複URL除去
  - source_type配分（news/ir/sns）
  - 期間フィルタ（直近N時間）
- AI（2段階）でイベント化→通知JSON化し、メール本文（テキスト）を生成する

### セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 設定
`config.example.yaml` をコピーして `config.yaml` を作り、`rss_feeds` を埋めます。

```bash
cp config.example.yaml config.yaml
```

### 実行（情報断片JSONの生成）

```bash
python -m invest_notify collect --config config.yaml --out data/fragments.json --lookback-hours 24
```

出力：`data/fragments.json`（仕様の `7.1 入力：情報断片` の形式）

### AIの実行（第1段/第2段）とメール本文生成
OpenAI互換APIを利用します（環境変数で指定）。

必要な環境変数：
- `OPENAI_API_KEY`（必須）
- `OPENAI_MODEL`（任意。デフォルト: `gpt-4o-mini`）
- `OPENAI_MODEL_STAGE1`（任意。第1段だけモデルを上書き。無ければ `OPENAI_MODEL`）
- `OPENAI_MODEL_STAGE2`（任意。第2段だけモデルを上書き。無ければ `OPENAI_MODEL`）
- `OPENAI_BASE_URL`（任意。OpenAI以外の互換エンドポイントを使う場合）
- `OPENAI_TIMEOUT_SECONDS`（任意。デフォルト: `180`）
- `OPENAI_MAX_RETRIES`（任意。デフォルト: `2`）
- `INVEST_NOTIFY_UA_CONTACT`（任意。SECなどがUser-Agentに連絡先を要求する場合のメールアドレス）
- `INVEST_NOTIFY_WATCH_TICKERS`（任意。注視ティッカー。カンマ区切り。例: `AAPL,MSFT,7203.T`）
- `INVEST_NOTIFY_WATCH_MAX`（任意。注視ティッカーの別枠（追加枠）上限。デフォルト: 注視ティッカーがあれば `3`）
- `INVEST_NOTIFY_PRICE_GATE`（任意。`off` / `0` / `false` / `disabled` にすると送信直前の株価ゲートを無効化。デフォルトは有効）

`.env` がプロジェクト直下にある場合は、起動時に自動ロードします（既に環境変数がある場合はそちら優先）。
テンプレートは `.env.example`。

```bash
# 第1段：イベント化
python -m invest_notify stage1 --fragments data/fragments.json --out data/stage1_events.json

# 第2段：通知判定（notifications.json）
python -m invest_notify stage2 --stage1 data/stage1_events.json --out data/notifications.json

# メール本文生成（3日重複抑制のstateを更新する）
python -m invest_notify email --notifications data/notifications.json --out data/email.txt
```

ワンショット（収集→第1段→第2段→メール本文）：

```bash
python -m invest_notify run --config config.yaml
```

### SMTP送信（AWS SES）
必要な環境変数（`.env.example` 参照）：
- `SES_SMTP_HOST` / `SES_SMTP_PORT` / `SES_SMTP_USER` / `SES_SMTP_PASS`
- `MAIL_FROM`（SESで検証済みドメイン配下のFrom）
- `MAIL_TO`（宛先。複数ならカンマ区切り）

送信（notifications.json から重複抑制→送信→成功後state更新）：

```bash
python -m invest_notify send --notifications data/notifications.json
```

ワンショットで送信まで：

```bash
python -m invest_notify run --config config.yaml
```

### CLIパラメータ（デフォルト値含む）
`python -m invest_notify --help` でも確認できます。

#### `collect`
- `--config`（必須）: YAML設定
- `--out`（任意, デフォルト `data/fragments.json`）: 出力パス
- `--lookback-hours`（任意, デフォルト `24`）: 収集期間（現在時刻からの遡り時間）
- `--per-collector-limit`（任意, デフォルト `500`）: コネクタ単位の上限（最終上限200は別）

#### `stage1`
- `--fragments`（任意, デフォルト `data/fragments.json`）: 入力断片JSON
- `--out`（任意, デフォルト `data/stage1_events.json`）: 出力パス
- `--max-fragments`（任意, デフォルト `200`）: 入力断片の上限
- `--chunk-size`（任意, デフォルト `10`）: LLMに投げる分割サイズ（小さいほど安定、遅くなる）
- `--max-text-chars`（任意, デフォルト `400`）: 断片textの圧縮上限

#### `stage2`
- `--stage1`（任意, デフォルト `data/stage1_events.json`）: 第1段の出力JSON
- `--out`（任意, デフォルト `data/notifications.json`）: 出力パス
- `--chunk-size`（任意, デフォルト `25`）: 第1段イベントを分割して第2段にかけるサイズ
- `--no-auto-fix-summary`（任意）: summaryの300〜600字を満たすための自動補正を無効化
- `--max-confirmed`（任意, デフォルト `3`）: 確度高（confirmed）の最大件数（試用期間の増量に使う）
- `--max-early-warning`（任意, デフォルト `3`）: 早期警戒（early_warning）の最大件数（試用期間の増量に使う）

#### `email`
- `--notifications`（任意, デフォルト `data/notifications.json`）: 通知JSON
- `--state`（任意, デフォルト `data/state/sent_events.json`）: 3日重複抑制の状態ファイル
- `--out`（任意, デフォルト `data/email.txt`）: メール本文出力先
- `--window-days`（任意, デフォルト `3`）: 重複抑制日数
- `--no-price-gate`（任意）: 送信直前の株価ゲートを無効化

#### `send`
- `--notifications`（任意, デフォルト `data/notifications.json`）: 通知JSON
- `--state`（任意, デフォルト `data/state/sent_events.json`）: 3日重複抑制の状態ファイル（送信成功後に更新）
- `--out`（任意, デフォルト `data/email.txt`）: 生成した本文も保存する
- `--window-days`（任意, デフォルト `3`）: 重複抑制日数
- `--dry-run`（任意）: 送信せず、stateも更新しない（動作確認用）
- `--no-price-gate`（任意）: 送信直前の株価ゲートを無効化

#### `run`
- `--config`（必須）: YAML設定
- `--lookback-hours`（任意, デフォルト `24`）
- `--per-collector-limit`（任意, デフォルト `500`）
- `--state`（任意, デフォルト `data/state/sent_events.json`）
- `--dry-run`（任意）: 送信せず、stateも更新しない
- `--no-price-gate`（任意）: 送信直前の株価ゲートを無効化

#### 送信直前の株価ゲート（price-gate）

60日履歴×Yahoo Financeバックテストの結果から、以下パターンが発見されました:

- `pre_return >= +10%` → post_signed=-2.12%（既に噴いた後）
- `pre_return <= -5%` × `impact=negative` → post_signed=-5.65%（崩れた後のネガ追従）

`email` / `send` / `run` の実行時、デフォルトで各通知 ticker の直近 5 営業日リターンを
Yahoo Finance から取得し、以下を行います。

1. メール本文に「直近株価変動: +8.3%(5d) / 方向調整後 +8.3%」のように表示
2. `impact=negative` かつ `pre_return <= -10%` は通知を除外
3. `impact=positive` かつ `pre_return >= +15%` は通知を除外
4. `confirmed` で `pre_signed >= +10%` または `pre_signed <= -5%` は `early_warning` に降格

株価取得に失敗した銘柄（上場廃止/Yahoo未対応銘柄）は、安全側として**そのまま通します**。
無効化は `--no-price-gate` または `INVEST_NOTIFY_PRICE_GATE=off`。

#### `review-history`（通知の事後評価）
過去の `data/history/<YYYY-MM-DD>/notifications.json` 群を読み込み、以下を出力します。

- 件数分布（カテゴリ/レーン/impact/ticker Top20）
- テキスト proxy（後追い表現/構造変化マーカーのヒット率、evidence鮮度中央値）
- `--backtest` を付けると Yahoo Finance Chart API から日次終値を取得し、
  - `pre_return` = 通知前 N営業日 → 直前クローズ の終値リターン
  - `post_return` = 直前クローズ → 通知後 M営業日終値 のリターン
  - 分類: `early_capture` / `late_chase` / `missed` / `flat`
  - 全体・カテゴリ・レーン・impact・source_types 別の KPI
  - 旧ランク（confidence 順） vs 新ランク（`_priority_score` 順）の KPI 比較（`rank_compare`）

`impact_direction=negative` の通知は pre/post の符号を反転して「良い方向」前提で集計します（下落予想なら post<0 がヒット）。

例:

```bash
python -m invest_notify review-history \
  --history-dir data/history --out data/history_review.json \
  --backtest --cache-dir data/_yf_cache
```

パラメータ:

- `--history-dir`（必須）
- `--out`（任意, デフォルト `data/history_review.json`）
- `--max-confirmed` / `--max-early-warning`（任意, デフォルト `3` / `3`）: rank_compare 用
- `--backtest`（任意, フラグ）: Yahoo Finance 株価バックテストを有効化
- `--cache-dir`（任意, デフォルト `data/_yf_cache`）: 株価キャッシュ保存先
- `--pre-window-days` / `--post-window-days`（任意, デフォルト `5` / `10`）
- `--rise-threshold`（任意, デフォルト `0.05`）: early/late 分類の閾値
- `--early-pre-band`（任意, デフォルト `0.03`）: 「pre 期間がまだ静か」と見なす幅
- `--fetch-sleep`（任意, デフォルト `0.2`）: Yahoo へ過負荷をかけないための秒単位sleep
- `--prefer-raw-pool`（任意, フラグ）: 同日に `notifications_pool.json`（`stage2` が併出する候補プール）がある場合はそちらの `raw_notifications` を入力にする

> 備考: `stage2` は今後、最終選抜（`notifications.json`）に加えて選抜前の候補プール
> `notifications_pool.json`（`{"raw_notifications": [...], "postprocessed_notifications": [...]}`）
> を併出します。これが蓄積されると `review-history --prefer-raw-pool --backtest` で
> スコア関数の変更前後を KPI で A/B 比較できます。

### 注視ティッカーのRSS強化（任意）

`INVEST_NOTIFY_WATCH_TICKERS` に指定した銘柄の情報をより確実に拾いたい場合、`config.yaml` に Yahoo Finance の銘柄別RSSを追加できます。

```
# config.yaml の rss_feeds に追加
- url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US"
  source_name: "Yahoo Finance AAPL"
  source_type: "news"
  lang: "en"
```

書式: `https://feeds.finance.yahoo.com/rss/2.0/headline?s={TICKER}&region=US&lang=en-US`

注視ティッカー数が多い場合はすべて追加すると `per_type_max.news` を圧迫するため、特に重要な銘柄のみ追加することを推奨します。

---

### TODO

- [x] メールをhtml形式にしたい
- [x] tickerにyahoo financeのリンクがほしい
- [x] 注視したいティッカーENVに登録 -> そのティッカーのニュースで重要そうなものも通知に含める
- [ ] 異常終了時の監視（B案（堅牢）: invest_notify run に --healthcheck-url（成功時ping）と --alert-to（例外時メール）を追加する）

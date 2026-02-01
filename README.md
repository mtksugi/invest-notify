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
- `OPENAI_BASE_URL`（任意。OpenAI以外の互換エンドポイントを使う場合）
- `OPENAI_TIMEOUT_SECONDS`（任意。デフォルト: `180`）
- `OPENAI_MAX_RETRIES`（任意。デフォルト: `2`）

`.env` がプロジェクト直下にある場合は、起動時に自動ロードします（既に環境変数がある場合はそちら優先）。

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

#### `email`
- `--notifications`（任意, デフォルト `data/notifications.json`）: 通知JSON
- `--state`（任意, デフォルト `data/state/sent_events.json`）: 3日重複抑制の状態ファイル
- `--out`（任意, デフォルト `data/email.txt`）: メール本文出力先
- `--window-days`（任意, デフォルト `3`）: 重複抑制日数

#### `run`
- `--config`（必須）: YAML設定
- `--lookback-hours`（任意, デフォルト `24`）
- `--per-collector-limit`（任意, デフォルト `500`）
- `--state`（任意, デフォルト `data/state/sent_events.json`）


## invest_notify（MVP）

`SPEC_MVP_v0.2.md` の仕様に基づき、まずは **収集（コネクタ）→ 情報断片JSON生成** までを行うための最小実装です。

### できること（現時点）
- RSS/Atom から記事を取得し、仕様の「情報断片JSON（最大200件）」を出力する
  - 重複URL除去
  - source_type配分（news/ir/sns）
  - 期間フィルタ（直近N時間）

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
python -m invest_notify --config config.yaml --out data/fragments.json --lookback-hours 24
```

出力：`data/fragments.json`（仕様の `7.1 入力：情報断片` の形式）


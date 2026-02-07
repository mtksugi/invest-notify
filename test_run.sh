#!/usr/bin/env bash
set -euo pipefail

.venv/bin/python -m invest_notify collect --config config.yaml
.venv/bin/python -m invest_notify stage1
.venv/bin/python -m invest_notify stage2 --max-confirmed 5 --max-early-warning 5
.venv/bin/python -m invest_notify send --notifications data/notifications.json

# 実行結果を日付フォルダへ退避（数日分の比較・改善に使う）
d="data/history/$(date +%F)"
mkdir -p "$d"
for f in \
  data/email.txt \
  data/email.txt.html \
  data/notifications.json \
  data/stage1_events.json \
  data/fragments.json \
  data/state/sent_events.json
do
  if [[ -f "$f" ]]; then
    cp -p "$f" "$d"/
  fi
done

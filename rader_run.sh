#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
.venv/bin/python -m invest_notify radar send-weekly

d="data/history/radar/$(date +%F)"
mkdir -p "$d"
for f in \
  data/radar/email.txt \
  data/radar/email.txt.html \
  data/radar/candidates.json \
  data/radar/_state.json
do
  if [[ -f "$f" ]]; then
    cp -p "$f" "$d"/
  fi
done

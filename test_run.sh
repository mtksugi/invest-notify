#!/usr/bin/env bash
set -euo pipefail

.venv/bin/python -m invest_notify collect --config config.yaml
.venv/bin/python -m invest_notify stage1
.venv/bin/python -m invest_notify stage2 --max-confirmed 5 --max-early-warning 5
.venv/bin/python -m invest_notify send --notifications data/notifications.json

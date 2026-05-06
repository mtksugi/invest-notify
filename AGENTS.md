# AGENTS.md

## Cursor Cloud specific instructions

### Overview

**invest_notify** is a Python 3.12 batch pipeline (CLI) with **two independent subsystems**, sharing the same package and `.venv` but running on separate cron jobs:

- **A 系統 — Daily Watch (`run` / `collect` / `stage1` / `stage2` / `send`)**
  collects financial news via RSS, processes them through a 2-stage AI pipeline (OpenAI-compatible API), and sends an email via AWS SES SMTP. Runs daily (Mon–Sat). This is the original MVP described in `docs/SPEC_MVP_v0.2.md`.
- **B 系統 — Multibagger Radar (`radar build-universe` / `radar weekly` / `radar send-weekly`)**
  builds a US mid/small-cap universe via FMP (Financial Modeling Prep), scores fundamentals + price momentum, and sends a weekly summary email on Mondays. Designed in `docs/REDESIGN_v0.3.md`. Independent cron line; A and B never share a process.

It is a single Python package (not a monorepo), with no web server, no database, and no Docker dependency.

### Layout

```
invest_notify/
  __main__.py        # CLI entrypoint: routes to A 系統 (run/collect/...) and B 系統 (radar)
  ...                # A 系統 modules
  radar/
    fmp.py           # FMP /stable/ endpoints + per-endpoint cache (data/radar/_fmp_cache/)
    universe.py      # build-universe, staleness check (180-day threshold)
    fundamentals.py  # fetch-fundamentals (Starter プランは period=annual に自動 fallback)
    momentum.py      # historical-price-eod から SMA200 / 底からの倍率 / 出来高比
    score.py         # 9 シグナルの単純合算 + state 分類
    runner.py        # weekly: universe → ファンダ → モメンタム → スコア → メール本文
    email.py         # [Radar Weekly] 件名のテキスト/HTML レンダリング
docs/
  SPEC_MVP_v0.2.md   # A 系統の元仕様
  REDESIGN_v0.3.md   # B 系統 (Radar) の設計
```

### Environment setup

- Python 3.12 with venv. The `python3.12-venv` system package must be installed (`sudo apt-get install -y python3.12-venv`).
- Dependencies are in `requirements.txt`. After creating `.venv`, install with `pip install -r requirements.txt`.
- Copy `config.example.yaml` → `config.yaml` and `.env.example` → `.env` before running.
- Both `config.yaml` and `.env` are gitignored.

### Running the application

See `README.md` for full CLI reference. Key commands:

**A 系統 (Daily Watch)**

- **Collect only** (no API key needed): `python -m invest_notify collect --config config.yaml`
- **Full pipeline**: `python -m invest_notify run --config config.yaml` (requires `OPENAI_API_KEY`)
- **Dry run** (no email send): `python -m invest_notify run --config config.yaml --dry-run`

**B 系統 (Multibagger Radar)**

- **Build universe** (manual, semiannual): `python -m invest_notify radar build-universe --out data/radar/universe.json` — produces about 2100 US stocks ($500M–$30B, NYSE/NASDAQ, ETF/funds excluded).
- **Single ticker debug**: `python -m invest_notify radar fetch-fundamentals --ticker AAPL`
- **Generate weekly mail body** (no send): `python -m invest_notify radar weekly --out-dir data/radar`
- **Dry-run send**: `python -m invest_notify radar send-weekly --dry-run`
- **Send via SMTP**: `python -m invest_notify radar send-weekly`
- **Subset for testing**: append `--max-tickers 200` to the above to limit ticker count.

### Lint / Test

- No linter or test framework is configured in the repo. Use `python -m py_compile <file>` for syntax checks.
- No unit/integration test files exist.

### Required secrets

- **A 系統**: `OPENAI_API_KEY` (for `stage1` / `stage2` / `run`), plus `SES_SMTP_HOST`, `SES_SMTP_PORT`, `SES_SMTP_USER`, `SES_SMTP_PASS`, `MAIL_FROM`, `MAIL_TO` (for `send`).
- **B 系統**: `FMP_API_KEY` (Starter plan or higher; `/api/v3/` is 403 on Starter, so the code uses `/stable/` exclusively). Reuses the same SES variables for `send-weekly`.

### Cron deployment (production)

A and B run on independent cron lines. Reference setup on the production VM:

```cron
# A 系統 (existing, daily Mon–Sat 8:00 JST)
0 23 * * 1-6 cd /home/ubuntu/invest-notify && /bin/bash /home/ubuntu/invest-notify/test_run.sh > /var/log/invest-notify/cron.log 2>&1

# B 系統 (new, weekly Mon 7:30 JST)
30 22 * * 1 cd /home/ubuntu/invest-notify && /bin/bash /home/ubuntu/invest-notify/rader_run.sh > /var/log/invest-notify/radar.log 2>&1
```

`rader_run.sh` (note: filename is `rader_run.sh`, not `radar_run.sh`, due to legacy typo) wraps `radar send-weekly` plus history retention into `data/history/radar/<YYYY-MM-DD>/`.

`radar build-universe` is **not** in cron — it is run manually on a half-yearly cadence. When the universe is older than 180 days, an "⚠ ユニバース要更新" banner is auto-injected into the A 系統 email so the user notices.

### Gotchas

**Common to both subsystems**

- The `data/` directory is auto-created by the CLI; do not check it in.
- `.env` is auto-loaded by `python-dotenv` at startup; existing env vars take precedence.
- `config.yaml` and `.env` must be created from their `.example` counterparts before running. They are gitignored and not committed.

**A 系統 (Daily Watch)**

- The `collect` subcommand works without any API keys — it only fetches public RSS feeds.
- `stage1` is the slowest step: ~30–60s per chunk, with ~17 chunks for 170 fragments (total ~10–20 min). Plan accordingly.
- `stage2` is faster (~20s per chunk, ~5 chunks). `email` is near-instant.
- After `email`, both `data/email.txt` (plain text) and `data/email.txt.html` (HTML) are generated.
- To verify the pipeline end-to-end without SMTP, run each step separately: `collect` → `stage1` → `stage2` → `email`. The `run` subcommand does all steps but also attempts `send` if SES credentials are set.

**B 系統 (Multibagger Radar)**

- All FMP calls go through `/stable/` endpoints. Old `/api/v3/` paths return 403 on the Starter plan.
- FMP Starter is 300 req/min and a single ticker requires 6 endpoints (income-statement / key-metrics-ttm / ratios-ttm / historical-price-eod / analyst-estimates / profile), so the practical throughput is **~50 tickers/min**. The full 2100-ticker universe takes ~15–25 min on a cold cache, then 5–15 min for subsequent weekly runs (price TTL 2d, fundamentals TTL 6d).
- Starter plan does **not** allow `period=quarter` for income-statement / key-metrics; the code auto-falls back to `period=annual` and tags `Fundamentals.period_type` accordingly. The email displays "4Q" or "4Y" labels based on this.
- The screener **must** pass `isFund=false` (in addition to `isEtf=false`); otherwise NASDAQ leaks ~2400 mutual funds (Janus / Vanguard / Fidelity etc., tickers ending in X) into the universe.
- 429 rate-limit responses are retried with exponential backoff. Around 0.3% of tickers may still fail per full run; they get re-fetched the following week as the cache decays.
- Production deployment requires a one-time setup before enabling cron: `radar build-universe` (creates universe.json) → `radar send-weekly --dry-run` (warms cache, ~25 min) → review `data/radar/email.txt` → optionally `./rader_run.sh` for a manual first send. See README "本番投入前の段取り" for the full checklist.

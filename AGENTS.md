# AGENTS.md

## Cursor Cloud specific instructions

### Overview

**invest_notify** is a Python 3.12 batch pipeline (CLI) that collects financial news via RSS, processes them through a 2-stage AI pipeline (OpenAI-compatible API), and sends email notifications via AWS SES SMTP. It is a single Python package (not a monorepo), with no web server, no database, and no Docker dependency.

### Environment setup

- Python 3.12 with venv. The `python3.12-venv` system package must be installed (`sudo apt-get install -y python3.12-venv`).
- Dependencies are in `requirements.txt`. After creating `.venv`, install with `pip install -r requirements.txt`.
- Copy `config.example.yaml` → `config.yaml` and `.env.example` → `.env` before running.
- Both `config.yaml` and `.env` are gitignored.

### Running the application

See `README.md` for full CLI reference. Key commands:

- **Collect only** (no API key needed): `python -m invest_notify collect --config config.yaml`
- **Full pipeline**: `python -m invest_notify run --config config.yaml` (requires `OPENAI_API_KEY`)
- **Dry run** (no email send): `python -m invest_notify run --config config.yaml --dry-run`

### Lint / Test

- No linter or test framework is configured in the repo. Use `python -m py_compile <file>` for syntax checks.
- No unit/integration test files exist.

### Required secrets for full pipeline

- `OPENAI_API_KEY` — required for `stage1`, `stage2`, and `run` subcommands.
- `SES_SMTP_HOST`, `SES_SMTP_PORT`, `SES_SMTP_USER`, `SES_SMTP_PASS`, `MAIL_FROM`, `MAIL_TO` — required only for `send` subcommand (email delivery).

### Gotchas

- The `collect` subcommand works without any API keys — it only fetches public RSS feeds.
- The `data/` directory is auto-created by the CLI; do not check it in.
- `.env` is auto-loaded by `python-dotenv` at startup; existing env vars take precedence.

from __future__ import annotations

import argparse
from pathlib import Path

from .collect import collect_fragments, write_fragments_json


def main() -> int:
    p = argparse.ArgumentParser(description="collect fragments for invest_notify (MVP)")
    p.add_argument("--config", required=True, help="path to YAML config (rss_feeds, limits)")
    p.add_argument("--out", default="data/fragments.json", help="output JSON path")
    p.add_argument("--lookback-hours", type=int, default=24, help="lookback hours (default: 24)")
    p.add_argument(
        "--per-collector-limit",
        type=int,
        default=500,
        help="max items per collector before global limiting",
    )
    args = p.parse_args()

    fragments = collect_fragments(
        config_path=Path(args.config),
        lookback_hours=args.lookback_hours,
        per_collector_limit=args.per_collector_limit,
    )
    write_fragments_json(fragments=fragments, out_path=Path(args.out))
    print(f"Wrote {len(fragments)} fragments -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


from __future__ import annotations

import argparse
import os
from pathlib import Path

from .collect import collect_fragments, write_fragments_json
from .email_render import render_email
from .state import filter_recently_sent, load_state, save_state, update_state_with_sent
from .validate import validate_notifications
from .ai.openai_compat import load_openai_compat_config_from_env_for_stage
from .ai.stage1 import run_stage1
from .ai.stage2 import run_stage2
from .smtp_send import load_smtp_config_from_env, send_email
from .review_history import review_history


def _load_watch_tickers_from_env() -> list[str]:
    raw = os.environ.get("INVEST_NOTIFY_WATCH_TICKERS", "").strip()
    if not raw:
        return []
    xs = [x.strip() for x in raw.split(",") if x.strip()]
    # 爆発防止（誤設定で巨大になっても影響を抑える）
    return xs[:200]


def _load_watch_max_from_env(*, watch_tickers: list[str]) -> int:
    """
    注視ティッカーの別枠（追加枠）上限。
    - ENVがあればそれを採用
    - 無ければ watch_tickers がある場合はデフォルト3、無ければ0
    """
    raw = os.environ.get("INVEST_NOTIFY_WATCH_MAX", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except Exception:
            return 0
    return 3 if watch_tickers else 0


def main() -> int:
    # .env を自動ロード（ローカル開発向け）
    # - 既に環境変数が設定されている場合はそちらを優先したいので override=False
    try:
        from dotenv import load_dotenv

        # 明示パス指定（環境によって find_dotenv が失敗するケース対策）
        load_dotenv(dotenv_path=Path(".env"), override=False)
    except Exception:
        # dotenvが無くても収集コマンドは動くので、AIコマンド実行時に環境変数が無ければエラーになる
        pass

    p = argparse.ArgumentParser(description="invest_notify (MVP)")
    sp = p.add_subparsers(dest="cmd", required=True)

    p_collect = sp.add_parser("collect", help="collect fragments.json (RSS)")
    p_collect.add_argument("--config", required=True, help="path to YAML config (rss_feeds, limits)")
    p_collect.add_argument("--out", default="data/fragments.json", help="output JSON path")
    p_collect.add_argument("--lookback-hours", type=int, default=24)
    p_collect.add_argument("--per-collector-limit", type=int, default=500)

    p_s1 = sp.add_parser("stage1", help="AI stage1: fragments -> events")
    p_s1.add_argument("--fragments", default="data/fragments.json")
    p_s1.add_argument("--out", default="data/stage1_events.json")
    p_s1.add_argument("--max-fragments", type=int, default=200)
    p_s1.add_argument("--chunk-size", type=int, default=10)
    p_s1.add_argument("--max-text-chars", type=int, default=400)

    p_s2 = sp.add_parser("stage2", help="AI stage2: events -> notifications")
    p_s2.add_argument("--stage1", default="data/stage1_events.json")
    p_s2.add_argument("--out", default="data/notifications.json")
    p_s2.add_argument("--chunk-size", type=int, default=25)
    p_s2.add_argument("--no-auto-fix-summary", action="store_true")
    p_s2.add_argument("--max-confirmed", type=int, default=3)
    p_s2.add_argument("--max-early-warning", type=int, default=3)

    p_email = sp.add_parser("email", help="render email from notifications (with 3-day dedupe)")
    p_email.add_argument("--notifications", default="data/notifications.json")
    p_email.add_argument("--state", default="data/state/sent_events.json")
    p_email.add_argument("--out", default="data/email.txt")
    p_email.add_argument("--window-days", type=int, default=3)

    p_send = sp.add_parser("send", help="send email via SMTP (SES) and update state on success")
    p_send.add_argument("--notifications", default="data/notifications.json")
    p_send.add_argument("--state", default="data/state/sent_events.json")
    p_send.add_argument("--out", default="data/email.txt", help="also write rendered email body here")
    p_send.add_argument("--window-days", type=int, default=3)
    p_send.add_argument("--dry-run", action="store_true", help="do not send, do not update state")

    p_run = sp.add_parser("run", help="collect -> stage1 -> stage2 -> email (one-shot)")
    p_run.add_argument("--config", required=True)
    p_run.add_argument("--lookback-hours", type=int, default=24)
    p_run.add_argument("--per-collector-limit", type=int, default=500)
    p_run.add_argument("--state", default="data/state/sent_events.json")
    p_run.add_argument("--dry-run", action="store_true", help="do not send, do not update state")

    p_review = sp.add_parser("review-history", help="review historical notifications for early-rise capture")
    p_review.add_argument("--history-dir", required=True, help="path to history root that contains YYYY-MM-DD dirs")
    p_review.add_argument("--out", default="data/history_review.json", help="output JSON report path")
    p_review.add_argument(
        "--backtest",
        action="store_true",
        help="enable Yahoo Finance price backtest (network required)",
    )
    p_review.add_argument("--pre-window-days", type=int, default=5, help="trading days before event for pre_return")
    p_review.add_argument("--post-window-days", type=int, default=10, help="trading days after event for post_return")
    p_review.add_argument(
        "--rise-threshold",
        type=float,
        default=0.05,
        help="abs return considered a 'rise' for early/late classification (default 0.05 = 5 percent)",
    )
    p_review.add_argument(
        "--early-pre-band",
        type=float,
        default=0.03,
        help="pre_return must be within ±this for 'early_capture' (default 0.03)",
    )
    p_review.add_argument("--cache-dir", default=None, help="directory to cache fetched price series")
    p_review.add_argument("--fetch-sleep", type=float, default=0.0, help="sleep seconds between fetches (rate limit)")
    p_review.add_argument(
        "--prefer-raw-pool",
        action="store_true",
        help="if YYYY-MM-DD/notifications_pool.json exists, use the postprocessed pool instead of the final 3+3",
    )

    args = p.parse_args()
    watch_tickers = _load_watch_tickers_from_env()
    watch_max = _load_watch_max_from_env(watch_tickers=watch_tickers)

    if args.cmd == "collect":
        fragments = collect_fragments(
            config_path=Path(args.config),
            lookback_hours=args.lookback_hours,
            per_collector_limit=args.per_collector_limit,
        )
        write_fragments_json(fragments=fragments, out_path=Path(args.out))
        print(f"Wrote {len(fragments)} fragments -> {args.out}")
        return 0

    if args.cmd == "stage1":
        cfg = load_openai_compat_config_from_env_for_stage(stage="stage1")
        run_stage1(
            cfg=cfg,
            fragments_path=Path(args.fragments),
            out_path=Path(args.out),
            max_fragments=args.max_fragments,
            chunk_size=args.chunk_size,
            max_text_chars_per_fragment=args.max_text_chars,
            watch_tickers=watch_tickers,
        )
        print(f"Wrote stage1 events -> {args.out}")
        return 0

    if args.cmd == "stage2":
        cfg = load_openai_compat_config_from_env_for_stage(stage="stage2")
        out = run_stage2(
            cfg=cfg,
            stage1_path=Path(args.stage1),
            out_path=Path(args.out),
            chunk_size=args.chunk_size,
            auto_fix_summary=(not args.no_auto_fix_summary),
            max_confirmed=args.max_confirmed,
            max_early_warning=args.max_early_warning,
            watch_tickers=watch_tickers,
            max_watch=watch_max,
        )
        vr = validate_notifications(
            out,
            max_confirmed=args.max_confirmed,
            max_early_warning=args.max_early_warning,
            max_watch=watch_max,
        )
        if not vr.ok:
            print("Validation errors:")
            for e in vr.errors:
                print(" -", e)
            return 2
        print(f"Wrote notifications -> {args.out}")
        return 0

    if args.cmd == "email":
        import json

        obj = json.loads(Path(args.notifications).read_text(encoding="utf-8"))
        vr = validate_notifications(obj, max_confirmed=999, max_early_warning=999, max_watch=999)
        if not vr.ok:
            print("Validation errors:")
            for e in vr.errors:
                print(" -", e)
            return 2
        notifs = obj.get("notifications", []) if isinstance(obj, dict) else []
        if not isinstance(notifs, list):
            raise RuntimeError("notifications.json must contain notifications[]")

        state = load_state(Path(args.state))
        allowed, suppressed = filter_recently_sent(notifs, state=state, window_days=args.window_days)

        subject, text_body, html_body = render_email(allowed, watch_tickers=watch_tickers)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text_body, encoding="utf-8")
        # HTMLも同時に出力（確認/デバッグ用）
        out_html = out.with_suffix(out.suffix + ".html") if out.suffix else Path(str(out) + ".html")
        out_html.write_text(html_body, encoding="utf-8")
        print(f"Wrote email ({len(allowed)} items, suppressed {len(suppressed)}) -> {args.out}")
        return 0

    if args.cmd == "send":
        import json

        obj = json.loads(Path(args.notifications).read_text(encoding="utf-8"))
        vr = validate_notifications(obj, max_confirmed=999, max_early_warning=999, max_watch=999)
        if not vr.ok:
            print("Validation errors:")
            for e in vr.errors:
                print(" -", e)
            return 2
        notifs = obj.get("notifications", []) if isinstance(obj, dict) else []
        if not isinstance(notifs, list):
            raise RuntimeError("notifications.json must contain notifications[]")

        state_path = Path(args.state)
        state = load_state(state_path)
        allowed, suppressed = filter_recently_sent(notifs, state=state, window_days=args.window_days)

        subject, text_body, html_body = render_email(allowed, watch_tickers=watch_tickers)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text_body, encoding="utf-8")
        out_html = out.with_suffix(out.suffix + ".html") if out.suffix else Path(str(out) + ".html")
        out_html.write_text(html_body, encoding="utf-8")

        if args.dry_run:
            print(f"[dry-run] Would send email ({len(allowed)} items, suppressed {len(suppressed)})")
            print(f"Wrote email body -> {args.out}")
            return 0

        smtp_cfg = load_smtp_config_from_env()
        send_email(cfg=smtp_cfg, subject=subject, text_body=text_body, html_body=html_body)
        print(f"Sent email ({len(allowed)} items, suppressed {len(suppressed)})")

        # 送信成功後のみstate更新
        new_state = update_state_with_sent(state, allowed)
        save_state(state_path, new_state)
        return 0

    if args.cmd == "run":
        cfg_stage1 = load_openai_compat_config_from_env_for_stage(stage="stage1")
        cfg_stage2 = load_openai_compat_config_from_env_for_stage(stage="stage2")
        fragments_path = Path("data/fragments.json")
        stage1_path = Path("data/stage1_events.json")
        stage2_path = Path("data/notifications.json")
        email_path = Path("data/email.txt")

        fragments = collect_fragments(
            config_path=Path(args.config),
            lookback_hours=args.lookback_hours,
            per_collector_limit=args.per_collector_limit,
        )
        write_fragments_json(fragments=fragments, out_path=fragments_path)
        print(f"Wrote {len(fragments)} fragments -> {fragments_path}")

        run_stage1(cfg=cfg_stage1, fragments_path=fragments_path, out_path=stage1_path, watch_tickers=watch_tickers)
        print(f"Wrote stage1 events -> {stage1_path}")

        out = run_stage2(
            cfg=cfg_stage2,
            stage1_path=stage1_path,
            out_path=stage2_path,
            watch_tickers=watch_tickers,
            max_watch=watch_max,
        )
        vr = validate_notifications(out, max_watch=watch_max)
        if not vr.ok:
            print("Validation errors:")
            for e in vr.errors:
                print(" -", e)
            return 2
        print(f"Wrote notifications -> {stage2_path}")

        notifs = out.get("notifications", [])
        state_path = Path(args.state)
        state = load_state(state_path)
        allowed, suppressed = filter_recently_sent(notifs, state=state, window_days=3)
        subject, text_body, html_body = render_email(allowed, watch_tickers=watch_tickers)
        email_path.write_text(text_body, encoding="utf-8")
        email_path.with_suffix(email_path.suffix + ".html").write_text(html_body, encoding="utf-8")
        print(f"Wrote email ({len(allowed)} items, suppressed {len(suppressed)}) -> {email_path}")

        if args.dry_run:
            print("[dry-run] Not sending, not updating state")
            return 0

        smtp_cfg = load_smtp_config_from_env()
        send_email(cfg=smtp_cfg, subject=subject, text_body=text_body, html_body=html_body)
        print("Sent email")

        new_state = update_state_with_sent(state, allowed)
        save_state(state_path, new_state)
        return 0

    if args.cmd == "review-history":
        report = review_history(
            history_dir=Path(args.history_dir),
            backtest=bool(args.backtest),
            pre_window_days=int(args.pre_window_days),
            post_window_days=int(args.post_window_days),
            rise_threshold=float(args.rise_threshold),
            early_pre_band=float(args.early_pre_band),
            cache_dir=args.cache_dir,
            sleep_seconds=float(args.fetch_sleep),
            prefer_raw_pool=bool(args.prefer_raw_pool),
        )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        import json

        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote history review -> {out}")
        proxy = report.get("initial_move_capture_proxy", {})
        ratio = 0.0
        if isinstance(proxy, dict):
            try:
                ratio = float(proxy.get("late_reaction_ratio", 0.0))
            except Exception:
                ratio = 0.0
        print("late_reaction_ratio=" + str(ratio))
        bt = report.get("price_backtest")
        if isinstance(bt, dict):
            s = bt.get("summary") or {}
            print(
                "backtest: evaluable=%d directional=%d early_capture=%.2f late_chase=%.2f hit_rate=%.2f"
                % (
                    int(bt.get("evaluable_count") or 0),
                    int(bt.get("directional_count") or 0),
                    float(s.get("early_capture_rate") or 0.0),
                    float(s.get("late_chase_rate") or 0.0),
                    float(s.get("hit_rate") or 0.0),
                )
            )
        return 0

    raise RuntimeError("unknown command")


if __name__ == "__main__":
    raise SystemExit(main())


from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Tuple


Category = Literal["geopolitics", "business_B2", "ir", "lawsuit"]
Lane = Literal["confirmed", "early_warning"]
Impact = Literal["positive", "negative", "mixed", "unclear"]
SourceType = Literal["news", "ir", "sns", "other"]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str]


def validate_notifications(
    obj: dict[str, Any],
    *,
    max_confirmed: int = 3,
    max_early_warning: int = 3,
    max_watch: int = 0,
) -> ValidationResult:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ValidationResult(False, ["root must be object"])
    notifs = obj.get("notifications")
    if not isinstance(notifs, list):
        return ValidationResult(False, ["notifications must be array"])

    # lane count（通常枠のみ）。bucket=="watch" は別枠として数えない。
    lane_counts = {"confirmed": 0, "early_warning": 0}
    watch_count = 0

    for i, n in enumerate(notifs):
        if not isinstance(n, dict):
            errors.append(f"notifications[{i}] must be object")
            continue

        bucket = n.get("bucket")
        if bucket is not None and bucket not in ("watch",):
            errors.append(f"notifications[{i}].bucket invalid")

        is_watch_bucket = bucket == "watch"
        if is_watch_bucket:
            watch_count += 1

        lane = n.get("lane")
        if lane not in ("confirmed", "early_warning"):
            errors.append(f"notifications[{i}].lane invalid")
        else:
            if not is_watch_bucket:
                lane_counts[lane] += 1

        ticker = (n.get("ticker") or "").strip()
        if not ticker:
            errors.append(f"notifications[{i}].ticker required (ticker不明は通知禁止)")

        category = n.get("category")
        if category not in ("geopolitics", "business_B2", "ir", "lawsuit"):
            errors.append(f"notifications[{i}].category invalid")

        conf = n.get("confidence")
        if not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
            errors.append(f"notifications[{i}].confidence must be 0..1")

        impact = n.get("impact_direction")
        if impact not in ("positive", "negative", "mixed", "unclear"):
            errors.append(f"notifications[{i}].impact_direction invalid")

        summary = n.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            errors.append(f"notifications[{i}].summary required")
        else:
            slen = len(summary.strip())
            # プロンプトでは300〜600字を厳守させるが、実運用では僅差の短文を許容する。
            # 例: 297字のような誤差で全体が止まるのを避ける（MVPの安定性優先）。
            if slen < 270 or slen > 600:
                # stage2側で2回修正しても収まらない場合は、運用優先で通す
                # （ただし極端に短い/長い場合は品質/暴走の観点で弾く）
                if n.get("summary_len_waived") is True and (120 <= slen <= 2000):
                    pass
                else:
                    errors.append(f"notifications[{i}].summary must be 270..600 chars (got {slen})")

        for k in ("why_not_priced_in", "unknowns", "next_checks", "source_types", "evidence"):
            v = n.get(k)
            if not isinstance(v, list) or len(v) == 0:
                errors.append(f"notifications[{i}].{k} must be non-empty array")

        source_types = n.get("source_types")
        if isinstance(source_types, list):
            if all(st == "sns" for st in source_types) and source_types:
                # SNS単体例外: geopolitics or business_B2 only
                if category not in ("geopolitics", "business_B2"):
                    errors.append(f"notifications[{i}] SNS-only requires category geopolitics|business_B2")

        evidence = n.get("evidence")
        if isinstance(evidence, list):
            if category in ("ir", "lawsuit"):
                ok_any = False
                for ev in evidence:
                    if isinstance(ev, dict) and ev.get("source_type") in ("news", "ir"):
                        ok_any = True
                        break
                if not ok_any:
                    errors.append(f"notifications[{i}] category {category} requires evidence from news/ir")

    if lane_counts["confirmed"] > int(max_confirmed):
        errors.append(f"confirmed lane exceeds {int(max_confirmed)}")
    if lane_counts["early_warning"] > int(max_early_warning):
        errors.append(f"early_warning lane exceeds {int(max_early_warning)}")
    if watch_count > int(max_watch):
        errors.append(f"watch bucket exceeds {int(max_watch)}")

    return ValidationResult(ok=(len(errors) == 0), errors=errors)


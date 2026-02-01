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


def validate_notifications(obj: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ValidationResult(False, ["root must be object"])
    notifs = obj.get("notifications")
    if not isinstance(notifs, list):
        return ValidationResult(False, ["notifications must be array"])

    # lane count
    lane_counts = {"confirmed": 0, "early_warning": 0}

    for i, n in enumerate(notifs):
        if not isinstance(n, dict):
            errors.append(f"notifications[{i}] must be object")
            continue

        lane = n.get("lane")
        if lane not in ("confirmed", "early_warning"):
            errors.append(f"notifications[{i}].lane invalid")
        else:
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
            if slen < 300 or slen > 600:
                errors.append(f"notifications[{i}].summary must be 300..600 chars (got {slen})")

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

    if lane_counts["confirmed"] > 3:
        errors.append("confirmed lane exceeds 3")
    if lane_counts["early_warning"] > 3:
        errors.append("early_warning lane exceeds 3")

    return ValidationResult(ok=(len(errors) == 0), errors=errors)


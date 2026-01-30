from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .types import Fragment, SourceType
from .utils import parse_iso_or_none


@dataclass(frozen=True)
class FragmentLimits:
    """
    200件上限 + source_type配分の設定。
    """

    total_max: int = 200
    per_type_max: dict[SourceType, int] | None = None

    def resolved_per_type(self) -> dict[SourceType, int]:
        # 仕様書の例（news/ir/sns）を踏まえた無難なデフォルト。
        base: dict[SourceType, int] = {"news": 120, "ir": 40, "sns": 40, "other": 0}
        if self.per_type_max:
            base.update(self.per_type_max)
        # 合計がtotal_maxを超える場合は、そのままでも後段でトリムされるが、
        # 意図が伝わりやすいようにここでは触らない。
        return base


def dedupe_by_url(fragments: Iterable[Fragment]) -> list[Fragment]:
    seen: set[str] = set()
    out: list[Fragment] = []
    for f in fragments:
        u = f.url.strip()
        if not u:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(f)
    return out


def sort_newest_first(fragments: Iterable[Fragment]) -> list[Fragment]:
    # published_atがない/壊れているものは最後に寄せる
    def key(f: Fragment):
        dt = parse_iso_or_none(f.published_at)
        # dtがNoneなら最小扱い
        return dt or _MIN_DT

    return sorted(list(fragments), key=key, reverse=True)


def apply_limits(
    fragments: Iterable[Fragment],
    *,
    limits: FragmentLimits,
) -> list[Fragment]:
    """
    - 同一URL重複除去
    - source_type配分で一次抽出
    - 余剰枠を他タイプに再配分（新しい順）
    - total_maxで最終トリム
    """

    fragments = dedupe_by_url(fragments)

    by_type: dict[SourceType, list[Fragment]] = defaultdict(list)
    for f in fragments:
        by_type[f.source_type].append(f)

    for t in list(by_type.keys()):
        by_type[t] = sort_newest_first(by_type[t])

    per_type_max = limits.resolved_per_type()
    chosen: list[Fragment] = []

    remaining_pool: list[Fragment] = []
    for t, items in by_type.items():
        cap = max(0, int(per_type_max.get(t, 0)))
        chosen.extend(items[:cap])
        remaining_pool.extend(items[cap:])

    # 余りを新しい順に再配分
    remaining_pool = sort_newest_first(remaining_pool)
    chosen = sort_newest_first(chosen)

    # total_maxまで詰める（chosenが既に超えている場合もここでトリム）
    if limits.total_max is not None:
        total_max = max(0, int(limits.total_max))
        if len(chosen) < total_max:
            need = total_max - len(chosen)
            chosen.extend(remaining_pool[:need])
        chosen = chosen[:total_max]

    # 最終的に新しい順を維持
    return sort_newest_first(chosen)


_MIN_DT = parse_iso_or_none("1970-01-01T00:00:00Z")
if _MIN_DT is None:  # 念のため
    from datetime import datetime, timezone

    _MIN_DT = datetime(1970, 1, 1, tzinfo=timezone.utc)


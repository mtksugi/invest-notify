from __future__ import annotations

import re
from typing import Iterable

# 後追い（値動きが出た後）を示しやすい表現
LATE_REACTION_PATTERNS: tuple[str, ...] = (
    r"すでに",
    r"既に",
    r"急騰",
    r"急伸",
    r"上昇済み",
    r"上昇している",
    r"上昇を受け",
    r"株価.*上昇",
    r"織り込み済み",
    r"already",
    r"rall(y|ied)",
    r"surged?",
    r"spiked?",
    r"jumped?",
    r"priced in",
)

# 初動（構造変化）を示しやすい表現
STRUCTURE_MARKERS: tuple[str, ...] = (
    "guidance",
    "ガイダンス",
    "修正",
    "revise",
    "contract",
    "契約",
    "締結",
    "発効",
    "開始",
    "着手",
    "量産",
    "稼働",
    "agreement",
    "commence",
    "launch",
    "effective",
    "signed",
    "start of production",
    "mass production",
    "capacity expansion",
    "規制",
    "regulation",
    "supply",
    "供給",
    "dilution",
    "希薄化",
    "buyback",
    "自社株買い",
)


def compile_late_reaction_regexes() -> list[re.Pattern[str]]:
    return [re.compile(x, flags=re.IGNORECASE) for x in LATE_REACTION_PATTERNS]


def has_late_reaction_text(text: str) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in LATE_REACTION_PATTERNS)


def has_structure_marker_text(text: str, *, markers: Iterable[str] = STRUCTURE_MARKERS) -> bool:
    t = (text or "").lower()
    return any(m.lower() in t for m in markers)

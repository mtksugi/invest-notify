from __future__ import annotations

import re
from typing import Iterable

# 後追い（値動きが出た後）を示しやすい表現
# 上昇/下落の双方向。実履歴で「既に下げた銘柄にネガ通知を当てる」パターンが late_chase の主因だったため、
# 下落系の表現も追加する。
LATE_REACTION_PATTERNS: tuple[str, ...] = (
    # 双方向（時間/織り込み）
    r"すでに",
    r"既に",
    r"織り込み済み",
    r"already",
    r"priced in",
    # 上昇後追い
    r"急騰",
    r"急伸",
    r"上昇済み",
    r"上昇している",
    r"上昇を受け",
    r"株価.*上昇",
    r"値上がり",
    r"高騰",
    r"rall(y|ied|ying)",
    r"surged?",
    r"spiked?",
    r"jumped?",
    r"soared?",
    # 下落後追い（実履歴で多発した「下げた後にネガ追加」を検知）
    r"急落",
    r"急下落",
    r"急反落",
    r"下落を受け",
    r"株価.*下落",
    r"売られ(た|る|て)",
    r"値下がり",
    r"暴落",
    r"plunged?",
    r"tumbled?",
    r"slumped?",
    r"sank",
    r"sliding",
    r"sold off",
    r"selloff",
    r"sell-off",
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

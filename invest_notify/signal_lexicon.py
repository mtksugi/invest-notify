from __future__ import annotations

"""
stage2 の選抜/後処理と履歴レビューで共通利用する語彙とシグナル関数。

語彙は 60日分の履歴 × Yahoo Finance チャートAPI のバックテスト結果から、
- 「通知時点で既に動いていた」ケース（late_chase）に頻出する表現
- 「まだ動いていない構造変化」を示す表現（structure）
を実データに基づいて選び直した。

late_chase は上下どちらもあり得る（上昇追従 / 売られた後のネガ追随）ので、
上昇系・下落系の双方を含める。

選抜側（stage2._priority_score）と評価側（review_history）は
必ずこのモジュールを参照すること。
"""

import re
from typing import Iterable


# 上昇方向の「後追い」表現
_LATE_UP_PATTERNS: list[str] = [
    r"\bすでに\b",
    r"既に",
    r"急騰",
    r"高騰",
    r"大幅上昇",
    r"株価が?大きく上昇",
    r"株価が?.{0,10}急上昇",
    r"暴騰",
    r"\brallied\b",
    r"\brally\b",
    r"\bsurged\b",
    r"\bsoared\b",
    r"\bjumped\b",
    r"\bspiked\b",
    r"\bshot up\b",
    r"\bpriced in\b",
    r"\balready (reacted|moved|traded|up)\b",
]

# 下落方向の「後追い」表現
_LATE_DOWN_PATTERNS: list[str] = [
    r"急落",
    r"急下落",
    r"急反落",
    r"暴落",
    r"大幅下落",
    r"下落を受け",
    r"株価が?大きく下落",
    r"株価が?.{0,10}急下落",
    r"売り浴びせ",
    r"売られ(た|ている)",
    r"\bplunged\b",
    r"\btumbled\b",
    r"\bslumped\b",
    r"\bsank\b",
    r"\bsliding\b",
    r"\bsell[- ]off\b",
    r"\bcrashed\b",
    r"\bnosedived\b",
]

LATE_REACTION_PATTERNS: list[str] = _LATE_UP_PATTERNS + _LATE_DOWN_PATTERNS


# 構造変化（数週〜数か月スパンで効きやすい）マーカー
STRUCTURE_MARKERS: list[str] = [
    # 契約・供給
    r"契約[^。]{0,6}(締結|確保|更新|解除|終了)",
    r"\bmaterial definitive agreement\b",
    r"\blong[- ]term (agreement|contract|lease)\b",
    r"\bsupply (agreement|contract|deal|commitment)\b",
    r"\bmulti[- ]year\b",
    # ガイダンス
    r"ガイダンス[^。]{0,8}(修正|撤回|引き上げ|引き下げ|更新)",
    r"\bguidance (raise|raised|cut|revised|withdrawn|lowered|updated)\b",
    r"\bpreliminary results\b",
    # 規制・訴訟・当局
    r"当局[^。]{0,6}(調査|命令|措置|起訴|勧告)",
    r"\benforcement action\b",
    r"\bsubpoena\b",
    r"\binjunction\b",
    r"\bconsent decree\b",
    r"\bSEC (charges|charged|sues)\b",
    # 資本政策
    r"自社株買い",
    r"\bshare repurchase\b|\bbuyback\b",
    r"増資|希薄化|転換社債",
    r"\bconvertible\b|\bwarrant\b|\bdilution\b",
    # 供給制約・価格
    r"供給[^。]{0,6}(停止|逼迫|制約|長期化)",
    r"\bsupply (outage|shortage|disruption)\b",
    r"関税[^。]{0,6}(引き上げ|新設|撤廃|発動)",
    r"\btariff\b",
    # M&A
    r"買収[^。]{0,6}(合意|締結|完了|発表)",
    r"\b(acquisition|merger|tender offer|take[- ]private)\b",
    # 上場維持・会計
    r"上場維持基準",
    r"\bnon[- ]reliance\b|\brestatement\b|\bmaterial weakness\b",
    r"\bgoing concern\b",
]


def _compile_any(patterns: Iterable[str]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), flags=re.IGNORECASE)


_LATE_RE = _compile_any(LATE_REACTION_PATTERNS)
_LATE_UP_RE = _compile_any(_LATE_UP_PATTERNS)
_LATE_DOWN_RE = _compile_any(_LATE_DOWN_PATTERNS)
_STRUCT_RE = _compile_any(STRUCTURE_MARKERS)


def has_late_reaction(text: str) -> bool:
    """上下どちらかの『後追い』表現を含むか。"""
    if not text:
        return False
    return _LATE_RE.search(text) is not None


def has_late_up(text: str) -> bool:
    if not text:
        return False
    return _LATE_UP_RE.search(text) is not None


def has_late_down(text: str) -> bool:
    if not text:
        return False
    return _LATE_DOWN_RE.search(text) is not None


def has_structure_marker(text: str) -> bool:
    """数週〜数か月スパンの構造変化マーカーを含むか。"""
    if not text:
        return False
    return _STRUCT_RE.search(text) is not None

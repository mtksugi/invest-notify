from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

SourceType = Literal["news", "ir", "sns", "other"]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Fragment:
    """
    SPEC_MVP_v0.2.md の「7.1 入力：情報断片」準拠のデータ。
    published_at は不明なら None 可（仕様通り）。
    """

    source_type: SourceType
    source_name: str
    published_at: str | None
    url: str
    text: str

    # optional
    title: str | None = None
    lang: str | None = None  # "ja" / "en" を想定
    fetched_at: str | None = None
    tickers_hint: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "source_type": self.source_type,
            "source_name": self.source_name,
            "published_at": self.published_at,
            "url": self.url,
            "text": self.text,
        }
        if self.title is not None:
            d["title"] = self.title
        if self.lang is not None:
            d["lang"] = self.lang
        if self.fetched_at is not None:
            d["fetched_at"] = self.fetched_at
        if self.tickers_hint:
            d["tickers_hint"] = self.tickers_hint
        return d


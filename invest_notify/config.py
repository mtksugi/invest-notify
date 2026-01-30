from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .collectors.rss import RssFeed
from .preprocess import FragmentLimits
from .types import SourceType


@dataclass(frozen=True)
class AppConfig:
    rss_feeds: list[RssFeed]
    limits: FragmentLimits


def load_config(path: str | Path) -> AppConfig:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config must be a mapping (YAML object)")

    limits = _parse_limits(raw.get("limits"))
    rss_feeds = _parse_rss_feeds(raw.get("rss_feeds"))
    return AppConfig(rss_feeds=rss_feeds, limits=limits)


def _parse_limits(v: Any) -> FragmentLimits:
    if v is None:
        return FragmentLimits()
    if not isinstance(v, dict):
        raise ValueError("limits must be a mapping")

    total_max = v.get("total_max", 200)
    per_type_raw = v.get("per_type_max")
    per_type: dict[SourceType, int] | None = None
    if per_type_raw is not None:
        if not isinstance(per_type_raw, dict):
            raise ValueError("limits.per_type_max must be a mapping")
        per_type = {}
        for k, vv in per_type_raw.items():
            if k not in ("news", "ir", "sns", "other"):
                raise ValueError(f"invalid source_type in limits.per_type_max: {k!r}")
            per_type[k] = int(vv)

    return FragmentLimits(total_max=int(total_max), per_type_max=per_type)


def _parse_rss_feeds(v: Any) -> list[RssFeed]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError("rss_feeds must be a list")

    out: list[RssFeed] = []
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            raise ValueError(f"rss_feeds[{i}] must be a mapping")
        url = str(item.get("url", "")).strip()
        source_name = str(item.get("source_name", "")).strip()
        source_type = str(item.get("source_type", "")).strip()
        lang = item.get("lang")

        if not url:
            raise ValueError(f"rss_feeds[{i}].url is required")
        if not source_name:
            raise ValueError(f"rss_feeds[{i}].source_name is required")
        if source_type not in ("news", "ir", "sns", "other"):
            raise ValueError(f"rss_feeds[{i}].source_type is invalid: {source_type!r}")
        if lang is not None:
            lang = str(lang).strip() or None

        out.append(
            RssFeed(
                url=url,
                source_name=source_name,
                source_type=source_type,  # type: ignore[arg-type]
                lang=lang,
            )
        )
    return out


from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable

from ..types import Fragment, SourceType


class Collector(ABC):
    """
    情報断片を収集するコネクタの共通インタフェース。
    """

    @property
    @abstractmethod
    def source_type(self) -> SourceType: ...

    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    def collect(
        self,
        *,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> list[Fragment]:
        """
        - since/until: 期間フィルタ（不明なら None）
        - limit: 返してよい最大件数（コネクタ側の上限。最終の200件上限は別途）
        """


def chain_collectors(
    collectors: Iterable[Collector],
    *,
    since: datetime | None,
    until: datetime | None,
    per_collector_limit: int,
) -> list[Fragment]:
    out: list[Fragment] = []
    for c in collectors:
        out.extend(c.collect(since=since, until=until, limit=per_collector_limit))
    return out


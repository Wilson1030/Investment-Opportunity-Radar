"""采集适配器抽象（D09：数据源可插拔）。

**新增付费源只需实现本协议，上层零修改。**

风险声明（R2）：cninfo 是本项目最大的工程风险 —— 反爬、PDF 质量不可控。
因此适配器必须：
1. 保留原始响应快照，便于接口变动时排查；
2. 把解析失败**如实上报**（``ParseStatus.FAILED`` / ``NEEDS_OCR``），不假装成功；
3. 单条失败不阻塞整批（返回结果里带 per-item error，而不是抛异常中断）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable


class AdapterError(RuntimeError):
    """适配器层错误（接口不可用等）。"""


class AdapterSchemaError(AdapterError):
    """上游接口字段结构变化 —— 必须显式告警，而不是静默丢数据。"""

    def __init__(self, message: str, raw_snapshot: str = "") -> None:
        super().__init__(message)
        self.raw_snapshot = raw_snapshot[:2000]


@dataclass(frozen=True)
class RawAnnouncement:
    """公告列表项（未解析全文）。"""

    company_code: str
    document_id: str
    title: str
    announcement_type: str | None
    publication_time: datetime
    url: str
    source: str = "cninfo"


@dataclass
class FetchResult:
    """批量抓取结果 —— 逐条可追溯，不隐藏失败。"""

    items: list[RawAnnouncement] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def found(self) -> int:
        return len(self.items) + len(self.errors)

    @property
    def failed(self) -> int:
        return len(self.errors)

    def add_error(self, stage: str, error: str, **extra) -> None:
        self.errors.append({"stage": stage, "error": error, **extra})


@dataclass(frozen=True)
class ParsedDocument:
    """全文解析结果。"""

    fulltext: str
    paragraphs: tuple[tuple[int, int, str, int, int], ...] = ()
    #: (page, para_index, text, char_start, char_end)，page / para_index 从 1 开始
    parse_status: str = "ok"          # ParseStatus 的值
    parse_error: str | None = None
    raw_format: str = "pdf"


@runtime_checkable
class AnnouncementAdapter(Protocol):
    name: str

    def list_announcements(
        self, company_code: str, start: date, end: date
    ) -> FetchResult: ...

    def fetch_document(self, url: str) -> bytes: ...


@runtime_checkable
class MarketDataAdapter(Protocol):
    name: str

    def st_company_codes(self) -> tuple[str, ...]: ...

    def trading_days(self, start: date, end: date) -> tuple[date, ...]: ...


__all__ = [
    "AdapterError",
    "AdapterSchemaError",
    "AnnouncementAdapter",
    "FetchResult",
    "MarketDataAdapter",
    "ParsedDocument",
    "RawAnnouncement",
]

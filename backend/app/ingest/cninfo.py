"""巨潮资讯（cninfo）公告适配器。

**本项目最大的工程风险所在（R2）**：反爬与 PDF 质量不可控。
因此本适配器：

* 保留原始响应快照，接口字段变动时抛 :class:`AdapterSchemaError`（不静默丢数据）
* 逐条容错，单条失败记录到 ``FetchResult.errors`` 并继续
* 严格区分「抓到了但解析失败」与「没抓到」

接口：``POST http://www.cninfo.com.cn/new/hisAnnouncement/query``（公开查询接口）。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

import httpx

from app.ingest.base import (
    AdapterError,
    AdapterSchemaError,
    FetchResult,
    RawAnnouncement,
)

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC_BASE = "http://static.cninfo.com.cn/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "X-Requested-With": "XMLHttpRequest",
}

#: 公告类型 → cninfo category（留空表示全部）
CATEGORY_ALL = ""

#: 礼貌抓取间隔（秒）—— 避免触发反爬，也避免给上游造成压力
DEFAULT_DELAY = 0.6


class CninfoAdapter:
    name = "cninfo"

    def __init__(self, delay: float = DEFAULT_DELAY, timeout: float = 30.0) -> None:
        self.delay = delay
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    def list_announcements(
        self,
        company_code: str,
        start: date,
        end: date,
        *,
        page_size: int = 30,
        max_pages: int = 5,
    ) -> FetchResult:
        result = FetchResult()
        column = _column_for(company_code)
        for page_number in range(1, max_pages + 1):
            payload = {
                "pageNum": page_number,
                "pageSize": page_size,
                "column": column,
                "tabName": "fulltext",
                "plate": "",
                "stock": f"{company_code},gssh0{company_code}"
                if column == "sse"
                else f"{company_code},gssz0{company_code}",
                "searchkey": "",
                "secid": "",
                "category": CATEGORY_ALL,
                "trade": "",
                "seDate": f"{start.isoformat()}~{end.isoformat()}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            }
            try:
                data = self._post(payload)
            except AdapterSchemaError as exc:
                result.add_error("cninfo_query", str(exc), company_code=company_code,
                                 snapshot=exc.raw_snapshot)
                break
            except AdapterError as exc:
                result.add_error("cninfo_query", str(exc), company_code=company_code)
                break

            announcements = data.get("announcements") or []
            for item in announcements:
                parsed = self._to_raw(item, company_code)
                if parsed is None:
                    result.add_error(
                        "cninfo_parse_item",
                        "公告条目字段不完整（缺少 announcementId / adjunctUrl）",
                        company_code=company_code,
                    )
                    continue
                result.items.append(parsed)

            if not data.get("hasMore"):
                break
            time.sleep(self.delay)
        return result

    # ------------------------------------------------------------------ #
    def fetch_document(self, url: str) -> bytes:
        """下载公告原文（PDF 或 HTML）。"""
        full_url = url if url.startswith("http") else f"{STATIC_BASE}{url.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout, headers=HEADERS, follow_redirects=True) as c:
                resp = c.get(full_url)
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPError as exc:
            raise AdapterError(f"下载公告失败（{full_url}）：{exc}") from exc

    # ------------------------------------------------------------------ #
    def _post(self, payload: dict) -> dict:
        try:
            with httpx.Client(timeout=self.timeout, headers=HEADERS) as client:
                resp = client.post(QUERY_URL, data=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise AdapterError(f"cninfo 查询失败：{exc}") from exc
        except ValueError as exc:
            raise AdapterSchemaError(
                "cninfo 返回的不是 JSON（可能被反爬拦截或接口已变更）",
                raw_snapshot=getattr(resp, "text", "") if "resp" in locals() else "",
            ) from exc

        if not isinstance(data, dict):
            raise AdapterSchemaError("cninfo 返回结构变化：顶层不是对象", str(data))
        if "announcements" not in data:
            raise AdapterSchemaError(
                "cninfo 返回结构变化：缺少 announcements 字段",
                str(data)[:2000],
            )
        return data

    def _to_raw(self, item: dict, company_code: str) -> RawAnnouncement | None:
        document_id = item.get("announcementId")
        adjunct = item.get("adjunctUrl")
        title = (item.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
        if not document_id or not adjunct:
            return None
        timestamp = item.get("announcementTime")
        if isinstance(timestamp, (int, float)):
            published = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        else:
            published = datetime.now(timezone.utc)
        return RawAnnouncement(
            company_code=company_code,
            document_id=str(document_id),
            title=title,
            announcement_type=item.get("announcementType"),
            publication_time=published,
            url=adjunct if str(adjunct).startswith("http") else f"{STATIC_BASE}{adjunct}",
            source=self.name,
        )


def _column_for(code: str) -> str:
    """交易所板块判定：6/9 开头为沪市，其余为深市/北交所。"""
    return "sse" if code.startswith(("6", "9")) else "szse"


__all__ = ["CATEGORY_ALL", "DEFAULT_DELAY", "HEADERS", "QUERY_URL", "CninfoAdapter"]

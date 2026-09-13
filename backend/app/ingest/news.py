"""新闻采集与聚类（规格 §42 / §43）。

规格原文的流程：**50 条原始新闻 → 去重 → 聚类 → 识别事件 → 提炼 3~5 条要点**，
目标是让用户看到「围绕某事件的 17 条报道」，而不是 17 张重复卡片。

## 数据源

实测可达（东方财富的新闻接口在本环境不可达）：

  · 财联社电报 ``stock_info_global_cls``  —— 有标题 + 正文 + 日期 + 时间，最结构化
  · 新浪全球财经 ``stock_info_global_sina`` —— 只有时间 + 正文（回退源）

## 聚类为什么用规则而不是 LLM

规格 §43 要的是「去掉重复、按事件归拢」，这是**确定性**的事：
同一家公司的同一类事件，无论谁来判都该归成一簇。
交给 LLM 会引入不可复现的分组（同一条新闻两次运行可能进不同的簇），
而聚类结果会影响「市场关注度」维度并进而影响评分 —— 不可复现的分组
等于不可复现的分数。

所以：**公司识别 + 事件类型识别都复用公告分类器**（``classifier``），
与公告走同一套关键词；要点直接从真实标题里取（可追溯），
而不是让 LLM 概括（概括会引入无法核对的说法）。

## 一个必须处理的坑：没采新闻 ≠ 关注度低

``MarketFacts.news_cluster_count`` 原先默认 0，而「0 个新闻簇」在
市场关注度规则里意味着「没人讨论」—— 于是**没接过新闻数据时，
系统会一直宣称所有公司关注度都很低**。这是个静默的假结论。

所以本模块同时提供 ``has_news_data()``，且 ``news_cluster_count``
改为可空（``None`` = 未采集，``0`` = 确实没有讨论）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.engine import classifier
from app.ingest.base import AdapterError
from app.models.enums import EventType

#: 财联社电报
CLS_SOURCE = "财联社电报"
SINA_SOURCE = "新浪财经"

#: 归一化标题时要去掉的噪音（来源前缀、栏目名、日期前缀）
_NOISE_PATTERNS = (
    r"^【[^】]{2,20}】",           # 【财联社】…
    r"^财联社\d+月\d+日电[，,]?",
    r"^证券时报[·•]?",
    r"^每经[·•]?",
)

#: 聚类时要求的最低关键词重合度（Jaccard）—— 太低会把不同事件并在一起
_CLUSTER_SIMILARITY = 0.34


def normalize_title(title: str) -> str:
    """标题归一化：去来源前缀与日期前缀，便于去重与相似度比较。"""
    text = (title or "").strip()
    for pattern in _NOISE_PATTERNS:
        text = re.sub(pattern, "", text).strip()
    return text


def _tokens(text: str) -> set[str]:
    """粗分词：连续中文 2-gram + 数字/字母串。

    ★ 为什么不用 jieba 之类的分词器：聚类只需要「两条新闻讲的是不是同一件事」
    这个粗粒度判断，2-gram 足够且**无额外依赖、完全确定**。
    引入分词器会带来词典版本差异 —— 同一份数据在不同环境下聚类结果不同。
    """
    text = normalize_title(text)
    grams: set[str] = set()
    chinese = re.findall(r"[\u4e00-\u9fff]+", text)
    for chunk in chinese:
        for i in range(len(chunk) - 1):
            grams.add(chunk[i:i + 2])
    grams.update(re.findall(r"[A-Za-z0-9]{2,}", text))
    return grams


def similarity(a: str, b: str) -> float:
    """Jaccard 相似度 ∈ [0,1]。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_duplicate(a: str, b: str) -> bool:
    """两条标题是否是同一件事（近重复）。"""
    return similarity(a, b) >= 0.75


@dataclass(frozen=True)
class RawNews:
    source_name: str
    external_id: str
    title: str
    summary: str
    url: str
    published_at: datetime


@dataclass
class NewsClusterDraft:
    """一簇新闻的草稿（尚未落库）。"""

    label: str
    event_type: EventType | None
    member_count: int
    key_points: list[str] = field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class NewsSource:
    """财联社电报 + 新浪财经（回退）。"""

    name = "akshare_news"

    def fetch_cls(self, limit: int = 50) -> list[RawNews]:
        """财联社电报（有标题 + 正文）。"""
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover
            raise AdapterError('缺少 akshare，请执行：pip install -e ".[ingest]"') from exc
        try:
            frame = ak.stock_info_global_cls(symbol="全部")
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"获取财联社电报失败：{exc}") from exc
        if frame is None or frame.empty:
            return []

        out: list[RawNews] = []
        for _, row in frame.head(limit).iterrows():
            title = str(row.get("标题", "") or "").strip()
            body = str(row.get("内容", "") or "").strip()
            if not title and not body:
                continue
            published = _parse_cls_time(row.get("发布日期"), row.get("发布时间"))
            # 财联社的「标题」与「正文首句」经常重复，取更长的那个做正文
            summary = body if len(body) > len(title) else title
            out.append(RawNews(
                source_name=CLS_SOURCE,
                # 财联社没有稳定 id → 用发布时间 + 归一化标题做幂等键
                external_id=f"{published:%Y%m%d%H%M%S}-{abs(hash(normalize_title(title or summary))) % 10**8}",
                title=normalize_title(title) or normalize_title(summary)[:40],
                summary=summary,
                url="https://www.cls.cn/telegraph",
                published_at=published,
            ))
        return out

    def fetch_sina(self, limit: int = 30) -> list[RawNews]:
        """新浪全球财经（只有正文，作为补充源）。"""
        try:
            import akshare as ak
        except ImportError:  # pragma: no cover
            return []
        try:
            frame = ak.stock_info_global_sina()
        except Exception:  # noqa: BLE001 - 回退源，失败不影响主流程
            return []
        if frame is None or frame.empty:
            return []

        out: list[RawNews] = []
        for _, row in frame.head(limit).iterrows():
            body = str(row.get("内容", "") or "").strip()
            if not body:
                continue
            published = _parse_loose_time(row.get("时间"))
            title = normalize_title(body.split("。")[0])[:60]
            out.append(RawNews(
                source_name=SINA_SOURCE,
                external_id=f"{published:%Y%m%d%H%M%S}-{abs(hash(normalize_title(title))) % 10**8}",
                title=title,
                summary=body,
                url="https://finance.sina.com.cn/7x24/",
                published_at=published,
            ))
        return out

    def fetch(self, limit: int = 50) -> list[RawNews]:
        """主源 + 回退源，主源失败时仍能返回回退源的结果。"""
        items: list[RawNews] = []
        try:
            items.extend(self.fetch_cls(limit=limit))
        except AdapterError:
            pass
        items.extend(self.fetch_sina(limit=limit // 2))
        if not items:
            raise AdapterError("所有新闻源均不可用")
        return items


def _parse_cls_time(raw_date, raw_time) -> datetime:
    """财联社的日期 + 时间两列 → ``datetime``（缺一不可，缺失时用当前时间）。"""
    try:
        date_text = str(raw_date).strip()
        time_text = str(raw_time).strip()
        return datetime.fromisoformat(f"{date_text} {time_text}").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _parse_loose_time(raw) -> datetime:
    """新浪的「时间」列格式不稳定，尽力解析，失败用当前时间。"""
    text = str(raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt.startswith("%H"):
            today = date.today()
            return parsed.replace(year=today.year, month=today.month, day=today.day,
                                  tzinfo=timezone.utc)
        return parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 聚类
# --------------------------------------------------------------------------- #
def match_companies(text: str, companies: list[tuple[int, str, str]]) -> list[int]:
    """新闻文本里提到了哪些公司。

    ``companies`` 是 ``[(company_id, name, code), ...]``。
    匹配用**公司名（去掉 ST/*ST 前缀后）与股票代码** ——
    新闻里很少带「ST」前缀，只匹配全名会大量漏掉。
    """
    text = text or ""
    hits: list[int] = []
    for company_id, name, code in companies:
        bare = re.sub(r"^\*?ST", "", name or "").strip()
        if not bare:
            continue
        if (len(bare) >= 3 and bare in text) or (code and code in text):
            if company_id not in hits:
                hits.append(company_id)
    return hits


def cluster_news(
    items: list[RawNews],
    companies: list[tuple[int, str, str]],
    *,
    max_key_points: int = 5,
) -> dict[int, NewsClusterDraft]:
    """把新闻按 ``(公司, 事件类型)`` 聚成一簇。

    返回 ``{company_id: 草稿}``。同一家公司只出一个簇 ——
    规格 §42 要的是「围绕某事件的 N 条报道」，
    对雷达而言「这家公司今天有多少人讨论、在讨论哪类事」才是可用的信号。

    要点（``key_points``）从**真实标题**里取最多 5 条**互不重复**的，
    而不是让 LLM 概括 —— 概括会引入无法核对的说法，
    而这一栏的作用恰恰是让用户能快速核对「报道都在说什么」。
    """
    if not items:
        return {}

    # 先按公司归拢（一条新闻可以提到多家公司）
    buckets: dict[int, list[RawNews]] = {}
    for item in items:
        text = f"{item.title} {item.summary}"
        for company_id in match_companies(text, companies):
            buckets.setdefault(company_id, []).append(item)

    drafts: dict[int, NewsClusterDraft] = {}
    for company_id, bucket in buckets.items():
        # 事件类型取「出现次数最多」的那个 —— 与公告分类器同一套关键词
        type_counts: dict[EventType, int] = {}
        for item in bucket:
            for event_type in classifier.classify_all(item.title):
                type_counts[event_type] = type_counts.get(event_type, 0) + 1
        top_type = (
            max(type_counts.items(), key=lambda kv: kv[1])[0] if type_counts else None
        )

        # 要点：按标题去重（近重复只留一条），取时间最近的若干条
        picked: list[str] = []
        for item in sorted(bucket, key=lambda x: x.published_at, reverse=True):
            title = normalize_title(item.title)
            if not title:
                continue
            if any(is_duplicate(title, existing) for existing in picked):
                continue
            picked.append(title)
            if len(picked) >= max_key_points:
                break

        times = [item.published_at for item in bucket]
        label_type = top_type.value if top_type else "综合"
        drafts[company_id] = NewsClusterDraft(
            label=f"围绕该公司的 {len(bucket)} 条报道（{label_type}）",
            event_type=top_type,
            member_count=len(bucket),
            key_points=picked,
            first_seen=min(times),
            last_seen=max(times),
        )
    return drafts


__all__ = [
    "CLS_SOURCE",
    "SINA_SOURCE",
    "NewsClusterDraft",
    "NewsSource",
    "RawNews",
    "cluster_news",
    "is_duplicate",
    "match_companies",
    "normalize_title",
    "similarity",
]

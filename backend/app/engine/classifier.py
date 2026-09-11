"""公告分类器（规格 §7.2 / §10，M3-01）。

**这是规则层，不是 LLM 层。** 职责只有一个：用关键词白名单把 90% 的无关公告
在进入 LLM 之前砍掉（高效的确定性过滤，规格 §28）。

真正的语义归类由 ``ai/nodes/extract_event`` 完成 —— 因为**同一标题可能对应完全
不同的事件**（例如「关于重大资产重组进展的公告」可能是推进，也可能是终止）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import EventType


@dataclass(frozen=True)
class KeywordRule:
    event_type: EventType
    keywords: tuple[str, ...]
    #: 命中多个类型时，优先级数字越小越优先
    priority: int


#: 关键词白名单。**顺序即优先级**（priority 字段显式声明）。
KEYWORD_RULES: tuple[KeywordRule, ...] = (
    KeywordRule(EventType.BANKRUPTCY_REORGANIZATION,
                ("破产重整", "重整计划", "破产清算", "预重整", "重整投资人"), 10),
    KeywordRule(EventType.RESTRUCTURING,
                ("重大资产重组", "资产重组", "重组预案", "重组报告书",
                 "发行股份购买资产", "重组进展", "重组终止", "重组失败"), 20),
    KeywordRule(EventType.ASSET_INJECTION,
                ("资产注入", "注入资产", "资产置换", "置入资产", "置出资产"), 30),
    KeywordRule(EventType.CONTROL_CHANGE,
                ("控股股东变更", "实际控制人变更", "控制权", "控股股东拟",
                 "协议转让", "要约收购", "表决权委托", "权益变动"), 40),
    KeywordRule(EventType.M_AND_A,
                ("收购", "并购", "股权收购", "重大资产购买", "吸收合并", "合并"), 50),
    KeywordRule(EventType.BUYBACK, ("回购", "股份回购"), 60),
    KeywordRule(EventType.SHAREHOLDER_BUY, ("增持", "增持计划"), 70),
    KeywordRule(EventType.SHAREHOLDER_SELL, ("减持", "减持计划"), 80),
    KeywordRule(EventType.DIVIDEND_POLICY,
                ("分红", "利润分配", "派息", "现金分红", "股东回报"), 90),
    KeywordRule(EventType.EARNINGS_TURNAROUND,
                ("业绩预告", "业绩快报", "业绩预增", "业绩预亏", "业绩修正",
                 "业绩说明", "年度报告", "半年度报告", "季度报告"), 100),
    KeywordRule(EventType.MAJOR_CONTRACT,
                ("重大合同", "中标", "订单", "框架协议", "战略合作"), 110),
    KeywordRule(EventType.NEW_PRODUCT,
                ("新产品", "获得认证", "技术突破", "注册证", "取得专利", "量产"), 120),
    KeywordRule(EventType.POLICY_CATALYST,
                ("国务院", "国家发展改革委", "发改委", "部委", "产业政策",
                 "支持政策", "指导意见"), 130),
    KeywordRule(EventType.MANAGEMENT_CHANGE,
                ("董事长", "总经理", "高级管理人员", "高管", "辞职", "聘任",
                 "董事会秘书", "财务总监"), 140),
    KeywordRule(EventType.REGULATORY_RISK,
                ("问询函", "监管函", "关注函", "风险提示", "立案调查",
                 "行政处罚", "警示函", "非标", "保留意见"), 150),
    KeywordRule(EventType.LITIGATION, ("诉讼", "仲裁", "冻结", "查封"), 160),
)


def classify_all(title: str, announcement_type: str | None = None) -> tuple[EventType, ...]:
    """返回命中**全部**事件类型，按优先级排序。

    一条公告可能同时命中多个类型（例如「关于重大资产重组进展的问询函回复」
    同时是 ``RESTRUCTURING`` 与 ``REGULATORY_RISK``），两者都有意义。
    """
    haystack = f"{title or ''} {announcement_type or ''}"
    hits = [
        rule.event_type
        for rule in sorted(KEYWORD_RULES, key=lambda r: r.priority)
        if any(kw in haystack for kw in rule.keywords)
    ]
    return tuple(hits)


def classify_announcement(title: str, announcement_type: str | None = None) -> EventType | None:
    """返回**主类型**（优先级最高的命中）；未命中返回 ``None``。

    ``None`` 的含义是「未通过预筛，不进入 LLM 抽取」—— 这条规则每天替我们省掉
    绝大多数无效的 LLM 调用。
    """
    hits = classify_all(title, announcement_type)
    return hits[0] if hits else None


def passes_prefilter(title: str, announcement_type: str | None = None) -> bool:
    return classify_announcement(title, announcement_type) is not None


def matched_keywords(title: str) -> dict[str, tuple[str, ...]]:
    """返回命中的关键词明细（用于可解释性与调试）。"""
    result: dict[str, tuple[str, ...]] = {}
    for rule in KEYWORD_RULES:
        found = tuple(kw for kw in rule.keywords if kw in (title or ""))
        if found:
            result[rule.event_type.value] = found
    return result


__all__ = [
    "KEYWORD_RULES",
    "KeywordRule",
    "classify_all",
    "classify_announcement",
    "matched_keywords",
    "passes_prefilter",
]

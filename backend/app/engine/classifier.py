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
    # 破产重整的**完整阶段链**：从债权人申请一直到重整计划批准。
    # 早期阶段（申请 / 受理）确定性低但提前量大，用户明确要求纳入（提前布局）。
    KeywordRule(EventType.BANKRUPTCY_REORGANIZATION,
                ("破产重整", "重整计划", "破产清算", "预重整", "重整投资人",
                 "重整申请", "申请重整", "法院受理", "裁定受理", "受理重整",
                 "指定管理人", "破产申请", "债权人申请", "重整程序",
                 # ★ 失败与终结类：漏掉它们，死掉的苗头会永远挂在雷达上
                 "宣告破产", "破产宣告", "破产", "宣告", "不予受理", "驳回", "撤回申请",
                 "终止重整", "终止破产重整", "重整失败", "破产清算",
                 "重整"), 10),
    KeywordRule(EventType.RESTRUCTURING,
                ("重大资产重组", "资产重组", "重组预案", "重组报告书",
                 "发行股份购买资产", "重组进展", "重组终止", "重组失败",
                 # 早期苗头：筹划 / 停牌 / 借壳 / 重组上市
                 "筹划重大事项", "筹划重大资产", "重大事项停牌", "停牌筹划",
                 "重组上市", "借壳", "拟筹划"), 20),
    KeywordRule(EventType.ASSET_INJECTION,
                ("资产注入", "注入资产", "资产置换", "置入资产", "置出资产"), 30),
    KeywordRule(EventType.CONTROL_CHANGE,
                ("控股股东变更", "实际控制人变更", "控制权", "控股股东拟",
                 "协议转让", "要约收购", "表决权委托", "权益变动"), 40),
    KeywordRule(EventType.M_AND_A,
                ("收购", "并购", "股权收购", "重大资产购买", "吸收合并", "合并",
                 # 早期苗头：只有意向、还没成交易
                 "意向协议", "意向书", "收购意向", "投资意向"), 50),
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


#: 「重组已完成、只剩后续手续」的标志词 —— 限售股解禁是典型。
#:
#: ⚠ 曾经的错误处理：把它当成**假阳性直接排除**。
#: 抽样核对时用户判定「关于重大资产重组部分限售股份上市流通的核查意见」
#: **仍然是 RESTRUCTURING 事件** —— 召回优先。
#:
#: 但它的**催化强度应当接近零**：这是存量信息，不是新的重组催化。
#: 所以现在的做法是保留事件、把它标到阶梯最低的「存量」档，
#: 而不是把它丢掉（丢掉就再也看不见了）。
#:
#: 这是确定性的标题模式识别（不涉及「重组会不会成功」这类语义判断），
#: 因此放在规则层（规格 §28）。
POST_DEAL_KEYWORDS: tuple[str, ...] = (
    "限售股", "限售股份", "解除限售", "上市流通", "限售期",
    "持续督导", "过户完成", "实施完毕",
)


def is_post_deal(title: str) -> bool:
    """标题是否属于「重组已完成、只剩后续手续」的存量信息。"""
    return any(kw in (title or "") for kw in POST_DEAL_KEYWORDS)


def _is_non_restructuring(title: str) -> bool:
    """保留函数名以兼容旧调用；现在**不再排除**任何公告（召回优先）。"""
    return False


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
    # 限售股解禁类公告：剔除 RESTRUCTURING（标题里的「重大资产重组」只是历史背景）
    if _is_non_restructuring(title):
        hits = [h for h in hits if h is not EventType.RESTRUCTURING]
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


def is_non_restructuring(title: str) -> bool:
    """标题是否属于确定性非重组事件。

    现在恒为 ``False``：按用户判断改为**召回优先**，
    存量信息（限售股解禁）通过「阶段」而不是「排除」来处理。
    """
    return _is_non_restructuring(title)


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
    "POST_DEAL_KEYWORDS",
    "is_non_restructuring",
    "is_post_deal",
    "KeywordRule",
    "classify_all",
    "classify_announcement",
    "matched_keywords",
    "passes_prefilter",
]

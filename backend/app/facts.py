"""纯数据事实（Facts）—— 规则层与策略层的唯一输入形态。

为什么单独一个模块
------------------
``engine/``（规则、评分）与 ``strategies/``（策略规则）都要读这些事实，
若任一方定义就会形成循环依赖。放在这里，两边都只依赖「数据形状」，不依赖彼此。

另一层意义：**Facts 是纯数据，不含数据库会话**。这样
1. 规则与评分可以单测（构造 Facts 即可，不需要 DB）；
2. 策略规则无法偷偷按标签直接映射（规格 §5.6 示例 J / INV-C1）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.models.enums import EventType, ReliabilityLevel


@dataclass(frozen=True)
class CompanyFacts:
    id: int
    name: str = ""
    code: str = ""
    is_st: bool = False
    industry: str | None = None
    industry_chain: tuple[str, ...] = ()
    controlling_shareholder: str | None = None
    actual_controller: str | None = None


@dataclass(frozen=True)
class EventFact:
    id: int
    event_type: EventType
    title: str = ""
    summary: str = ""
    event_time: datetime | None = None
    importance: float = 0.5
    certainty: float = 0.5
    evidence_ids: tuple[int, ...] = ()
    #: 支撑本事件的最高证据等级（决定事件催化与确定性维度）
    evidence_level: ReliabilityLevel | None = None
    amount_ratio: float = 0.0          # 事件金额 / 公司规模，0 表示未知
    #: 交易对手方 / 标的方是否可识别（牵涉知名产业方 → 事件催化加分）
    counterparty_known: bool = False

    def title_contains(self, *needles: str) -> bool:
        return any(n in self.title for n in needles)


@dataclass(frozen=True)
class FinancialFacts:
    """``*_quarters`` 用「连续改善期数」表达趋势，避免规则里做算术。"""

    loss_years: int = 0
    revenue_improving_quarters: int = 0
    margin_improving_quarters: int = 0
    ocf_positive: bool = False
    ocf_improving: bool = False
    profitable_years: int = 0
    receivable_growth_exceeds_revenue: bool = False
    debt_ratio_rising: bool = False
    #: 指标恶化但已归因于一次性因素（减值 / 重组费用）→ 不扣分（INV-F1）
    deteriorating_attributed_to_one_off: bool = False
    #: 现金流数据是否为「每股经营现金流」代理（真实总额不可得时）
    ocf_is_proxy: bool = False

    # ---- 以下为 turnaround（困境反转）所需：**方向的变化**，而非当前状态 ----
    #:
    #: ★ 为什么不能复用 ``*_improving_quarters`` 反推：一家最近在改善的公司
    #: ``revenue_improving_quarters`` > 0，但那说明不了「过去有没有恶化」——
    #: 困境反转要同时成立「**先恶化**」与「**后改善**」两个方向相反的事实。

    #: 史上最长连续「营收同比下降」期数（回答 C1「过去有明确经营恶化」）
    revenue_declining_run_max: int = 0
    #: 史上最长连续「毛利率同比下降」期数
    margin_declining_run_max: int = 0
    #: 史上最长连续「净利润为负」期数
    loss_run_max: int = 0

    #: 最近连续「营收同比下降」期数（诊断用：>0 表示改善尚未发生）
    revenue_declining_quarters: int = 0
    #: 最近连续「毛利率同比下降」期数
    margin_declining_quarters: int = 0

    #: 最新一期经营现金流转正，**且上一期不为正** —— 真正的「转折点」
    ocf_turned_positive: bool = False
    #: 有财务数据的期数（判断「≥2 期」时必须有依据，不能凭空断言）
    periods_with_data: int = 0


@dataclass(frozen=True)
class ShareholderFacts:
    controlling_shareholder_changed: bool = False
    actual_controller_changed: bool = False
    insider_buy: bool = False
    buyback: bool = False
    buyback_scale_significant: bool = False
    high_pledge: bool = False
    insider_sell: bool = False


@dataclass(frozen=True)
class MarketFacts:
    news_cluster_count: int = 0
    abnormal_volatility: bool = False
    on_dragon_tiger: bool = False
    institutional_reports_delta: int = 0
    social_buzz: bool = False


@dataclass(frozen=True)
class StrategyFacts:
    """策略与评分看到的世界。"""

    company: CompanyFacts
    events: tuple[EventFact, ...] = ()
    financials: FinancialFacts = field(default_factory=FinancialFacts)
    shareholder: ShareholderFacts = field(default_factory=ShareholderFacts)
    market: MarketFacts = field(default_factory=MarketFacts)

    # 确定性相关（规格 §24：待确认事项必须具体）
    open_question_count: int = 0
    has_unanswered_inquiry: bool = False
    has_history_failure: bool = False
    has_conflicting_media: bool = False
    has_late_stage_pending_approval: bool = False

    # 证据池（用于确定性 = 最高证据等级；C/D/E 只能进 MARKET_ATTENTION）
    evidence_levels: tuple[ReliabilityLevel, ...] = ()
    #: 最新证据距今的天数（时效衰减，规格 §41）
    newest_evidence_age_days: float = 0.0

    # 交易性 / 估值（由采集层的行情数据填充）
    is_halted: bool = False
    valuation_percentile: float | None = None

    # ---------- 便捷访问 ----------

    @property
    def event_types(self) -> tuple[EventType, ...]:
        return tuple(e.event_type for e in self.events)

    def has_event(self, *types: EventType) -> bool:
        return any(t in self.event_types for t in types)

    def events_of(self, *types: EventType) -> tuple[EventFact, ...]:
        return tuple(e for e in self.events if e.event_type in types)

    @property
    def best_evidence_level(self) -> ReliabilityLevel | None:
        """最高证据等级（A 最好）。"""
        if not self.evidence_levels:
            return None
        return min(self.evidence_levels, key=lambda lv: "ABCDE".index(lv.value))

    @property
    def has_hard_evidence(self) -> bool:
        """是否存在 A / B 类证据 —— 决定能否进入 ``thesis_confirmed``（INV-E2）。"""
        return any(lv in (ReliabilityLevel.A, ReliabilityLevel.B) for lv in self.evidence_levels)

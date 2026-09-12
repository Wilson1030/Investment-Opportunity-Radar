"""策略注册表 —— 10 类 Thesis 的完整设计（docs/06-策略设计总表）。

**设计全量，实现逐个（D13）**：本文件为 10 类策略提供完整元数据
（核心条件、失效条件、待确认模板、催化剂阶梯、维度权重、风险因素、反例警示），
但只有 ``restructuring`` 挂载真正的实现类（见 :mod:`app.strategies.restructuring`）。

本模块是**纯数据 + 查表函数**，不访问数据库。数据库中由 ``sync_registry()`` 落库。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import (
    EventType,
    InvalidationSeverity,
    ScoreDimension,
    StrategyStatus,
    ThesisType,
)

# --------------------------------------------------------------------------- #
# 全局默认权重（docs/04-评分与证据链 §3，源自规格 §12）
# 正向权重合计 0.95；风险作为负向扣分，上限见 RISK_PENALTY_MAX
# --------------------------------------------------------------------------- #
GLOBAL_DEFAULT_WEIGHTS: dict[ScoreDimension, float] = {
    ScoreDimension.THESIS_MATCH: 0.30,
    ScoreDimension.EVENT_CATALYST: 0.20,
    ScoreDimension.CATALYST_STRENGTH: 0.10,
    ScoreDimension.CERTAINTY: 0.10,
    ScoreDimension.FUNDAMENTALS: 0.10,
    ScoreDimension.SHAREHOLDER_STRUCTURE: 0.10,
    ScoreDimension.MARKET_ATTENTION: 0.05,
    ScoreDimension.HISTORY_CASE: 0.00,
}

#: 正向权重合计（用于自检）
POSITIVE_WEIGHT_TOTAL = 0.95
#: 风险最高扣分（规格 §12 的「风险因素 −15%」）
RISK_PENALTY_MAX = 15.0


@dataclass(frozen=True)
class CoreConditionDef:
    key: str
    label: str
    weight: float
    description: str = ""


@dataclass(frozen=True)
class InvalidationDef:
    """失效条件（规格 §23 / M5-03）。``title_contains`` 为空表示只看事件类型。

    ``early_only``：只对**尚无进展证据**的机会生效。

    ★ 为什么需要它：处于早期待确认阶段时收到监管问询 / 关注函往往是终止的前兆，
    值得提醒；但对已经披露预案 / 草案的机会，收到问询函是常规流程，报警就是噪声。
    判定「进展证据」= 标题含 预案 / 报告书 / 草案 / 批复 / 股东大会 / 核准。
    """

    event_type: EventType
    severity: InvalidationSeverity
    description: str
    title_contains: tuple[str, ...] = ()
    amount_ratio_gt: float | None = None
    early_only: bool = False
    #: **AND 语义**：标题必须同时包含全部关键词。
    #:
    #: ★ 为什么需要：中文标题常在关键词中间插入别的词 ——
    #: 「关于法院宣告**公司**破产的公告」既不包含「宣告破产」也不包含「破产宣告」，
    #: 用 OR 要么漏（写成完整短语）要么误伤（写成裸词「破产」会命中健康的破产重整）。
    #: AND 语义正好解决这类问题。
    title_all_of: tuple[str, ...] = ()
    #: **排除词**：标题出现其中任意一个就**不**算命中。
    #:
    #: ★ 为什么需要：``title_all_of=("宣告","破产")`` 会把
    #: 「法院宣告破产重整计划**执行完毕**的公告」也判成失效 ——
    #: 而那是重整**成功**。纯关键词匹配必须配排除词才安全。
    title_none_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskTrigger:
    """风险触发条件。

    ``when`` 必须在 :data:`app.engine.rules.RISK_TRIGGER_PREDICATES` 中存在
    （由 ``validate_registry()`` 启动自检强制）。
    ``mode``：``set`` 覆盖为 severity；``add`` 在当前值上累加（上限 1.0）。
    """

    when: str
    severity: float
    reason: str
    mode: str = "set"


@dataclass(frozen=True)
class RiskFactorDef:
    key: str
    label: str
    weight: float
    base_severity: float
    triggers: tuple[RiskTrigger, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class CatalystStageDef:
    stage: str
    score: float
    description: str = ""
    #: 是否属于「早期苗头」（用户明确要求这类信号也要被找到，以便提前布局）
    early: bool = False


@dataclass(frozen=True)
class StrategyDef:
    code: ThesisType
    display_name: str
    status: StrategyStatus
    user_goal: str
    description: str
    core_conditions: tuple[CoreConditionDef, ...]
    support_event_types: tuple[EventType, ...]
    invalidating_events: tuple[InvalidationDef, ...]
    open_question_templates: tuple[str, ...]
    catalyst_ladder: tuple[CatalystStageDef, ...]
    default_weights: dict[ScoreDimension, float]
    risk_factors: tuple[RiskFactorDef, ...]
    evidence_requirements: dict[str, int] = field(default_factory=dict)
    anti_patterns: tuple[str, ...] = ()
    sort_order: int = 100
    #: 催化强度 ≤ 此值即视为「早期信号」（需卡片显式标注，M7-02）
    early_stage_max_score: float = 0.0

    # ---------- 自检辅助 ----------

    @property
    def condition_weight_total(self) -> float:
        return sum(c.weight for c in self.core_conditions)

    @property
    def risk_weight_total(self) -> float:
        return sum(r.weight for r in self.risk_factors)

    @property
    def positive_weight_total(self) -> float:
        return sum(self.default_weights.values())


# --------------------------------------------------------------------------- #
# 通用失效条件片段（多个策略复用）
# --------------------------------------------------------------------------- #
_TERMINATE_WORDS = ("终止", "失败", "撤回", "撤销", "不予核准", "否决")


def _weights(**overrides: float) -> dict[ScoreDimension, float]:
    """在全局默认权重上做策略级覆盖（docs/04 §3.1）。

    覆盖后的正向权重合计**必须仍为 0.95**（规格 §12 的权重总量）。
    设计期常量算错时立即报错，而不是静默地让不同策略的分数量纲不一致。
    """
    merged = dict(GLOBAL_DEFAULT_WEIGHTS)
    for key, value in overrides.items():
        merged[ScoreDimension(key)] = value
    total = sum(merged.values())
    if abs(total - POSITIVE_WEIGHT_TOTAL) > 1e-9:
        raise ValueError(
            f"策略级权重覆盖后正向合计为 {total:.4f}，应为 {POSITIVE_WEIGHT_TOTAL}。"
            f"覆盖参数：{overrides}"
        )
    return merged


# --------------------------------------------------------------------------- #
# 1. restructuring 重组预期 ★ 本轮唯一实现
# --------------------------------------------------------------------------- #
_RESTRUCTURING = StrategyDef(
    code=ThesisType.RESTRUCTURING,
    display_name="重组预期",
    status=StrategyStatus.IMPLEMENTED,
    user_goal="寻找经营困难、但近期出现重大资产重组、控制权变化、资产注入或破产重整迹象的公司",
    description="公司处于经营困境，同时出现重组/控制权/资产注入迹象，因此存在潜在重组预期",
    core_conditions=(
        CoreConditionDef("C1", "出现重大资产重组相关公告（A 类）", 0.35,
                         "Event(RESTRUCTURING) with A-grade evidence"),
        CoreConditionDef("C2", "存在控制权 / 实际控制人变化", 0.25, "Event(CONTROL_CHANGE)"),
        CoreConditionDef("C3", "存在资产注入或资产置换迹象", 0.20, "Event(ASSET_INJECTION)"),
        CoreConditionDef("C4", "经营困境背景", 0.20,
                         "连续亏损 / 净资产承压 / ST（**仅作为背景证据，不作为逻辑本身**）"),
    ),
    support_event_types=(
        EventType.RESTRUCTURING,
        EventType.ASSET_INJECTION,
        EventType.CONTROL_CHANGE,
        EventType.BANKRUPTCY_REORGANIZATION,
        EventType.M_AND_A,
    ),
    #: 早期信号的阶段上限（催化强度 ≤ 此值即视为「早期待确认」）
    early_stage_max_score=30,
    invalidating_events=(
        # ---- 重组类 ----
        InvalidationDef(EventType.RESTRUCTURING, InvalidationSeverity.TERMINAL,
                        "重组终止 / 重大资产重组失败", _TERMINATE_WORDS),
        InvalidationDef(EventType.CONTROL_CHANGE, InvalidationSeverity.SEVERE,
                        "控股权变更取消", ("取消", "终止", "解除")),
        InvalidationDef(EventType.REGULATORY_RISK, InvalidationSeverity.TERMINAL,
                        "监管否决", ("否决", "不予核准", "终止审查")),
        InvalidationDef(EventType.ASSET_INJECTION, InvalidationSeverity.SEVERE,
                        "核心资产退出", ("退出", "放弃", "不再纳入")),
        InvalidationDef(EventType.LITIGATION, InvalidationSeverity.SEVERE,
                        "重大诉讼致交易基础受损", amount_ratio_gt=0.3),
        # ---- 破产重整类 ★ 早期苗头的主要死法 ----
        # 之前完全没有覆盖这类事件，导致「死掉的苗头永远挂着」。
        InvalidationDef(EventType.BANKRUPTCY_REORGANIZATION, InvalidationSeverity.TERMINAL,
                        "法院不予受理 / 驳回重整申请",
                        ("不予受理", "驳回", "不予立案", "不予批准受理")),
        InvalidationDef(EventType.BANKRUPTCY_REORGANIZATION, InvalidationSeverity.TERMINAL,
                        "重整申请被撤回 / 撤销",
                        ("撤回申请", "撤回重整", "撤销申请", "撤回")),
        InvalidationDef(EventType.BANKRUPTCY_REORGANIZATION, InvalidationSeverity.TERMINAL,
                        "终止重整程序 / 转入破产清算",
                        ("终止重整", "终止破产重整", "终止重整程序",
                         "破产清算", "重整失败")),
        # ★ 单独一条：中文标题会在关键词中间插字（「宣告公司破产」），
        #   所以这条用 AND 语义。不能和上面的 OR 关键词混在同一条规则里 ——
        #   title_all_of 与 title_contains 是「与」关系，混用会互相收窄。
        InvalidationDef(EventType.BANKRUPTCY_REORGANIZATION, InvalidationSeverity.TERMINAL,
                        "法院宣告破产",
                        title_all_of=("宣告", "破产"),
                        # 注意排除：宣告「破产重整计划执行完毕」是重整**成功**
                        title_none_of=("执行完毕", "执行完成", "重整计划", "批准", "受理")),
        InvalidationDef(EventType.BANKRUPTCY_REORGANIZATION, InvalidationSeverity.SEVERE,
                        "重整计划未获通过 / 未获批准",
                        ("未获通过", "未通过", "未获批准", "未批准", "未获",
                         "不予批准", "未予批准", "否决")),
        InvalidationDef(EventType.BANKRUPTCY_REORGANIZATION, InvalidationSeverity.SEVERE,
                        "重整投资人退出 / 终止投资协议",
                        ("投资人退出", "终止投资", "解除投资", "投资人终止")),
        # ---- 早期专属的「预警」：监管关注往往是终止前兆 ----
        InvalidationDef(EventType.BANKRUPTCY_REORGANIZATION, InvalidationSeverity.WARNING,
                        "重整进展受监管关注（问询 / 关注函 / 风险提示）",
                        ("问询", "关注函", "风险提示", "监管函"),
                        early_only=True),
        InvalidationDef(EventType.RESTRUCTURING, InvalidationSeverity.WARNING,
                        "早期筹划阶段受监管关注（问询 / 关注函 / 风险提示）",
                        ("问询", "关注函", "风险提示"),
                        early_only=True),
    ),
    open_question_templates=(
        "交易标的", "交易价格", "重组方案", "资产评估结果", "监管审核结果", "股东大会决议",
    ),
    # ★ 阶梯从「存量」一直到「完成」。**早期阶段是刻意保留的**：
    #   用户明确要求「还不太确定但有苗头」的也要找，以便提前布局。
    #   注意低分不代表不重要 —— 它表示「离价值兑现还远、确定性低」，
    #   因此这类机会会排在后面，并且必须在卡片上标注阶段
    #   （否则用户会把苗头当成确定的事，违反规格 §24 / §38）。
    catalyst_ladder=(
        CatalystStageDef("存量｜重组已完成（限售解禁 / 后续手续）", 5,
                         "限售股上市流通、持续督导、过户完成 —— 存量信息，不是新催化",
                         early=False),
        CatalystStageDef("早期｜筹划 / 停牌 / 意向协议", 10,
                         "筹划重大事项、停牌筹划、意向协议", early=True),
        CatalystStageDef("早期｜预重整 / 重整申请", 15,
                         "预重整、债权人申请重整、破产申请", early=True),
        CatalystStageDef("早期｜法院受理 / 指定管理人", 28,
                         "法院裁定受理重整、指定管理人", early=True),
        CatalystStageDef("进展｜预案披露", 40, "重组预案、发行股份购买资产预案"),
        CatalystStageDef("进展｜草案 + 评估", 60, "重组报告书草案、资产评估结果"),
        CatalystStageDef("进展｜获批复 / 审核通过", 72, "国资批复、审核无条件通过"),
        CatalystStageDef("进展｜股东大会通过", 80, "股东大会决议公告"),
        CatalystStageDef("完成｜监管核准 / 实施完成", 95, "证监会核准、资产过户完成"),
    ),
    default_weights=_weights(
        thesis_match=0.30,
        event_catalyst=0.25,        # 0.20 → 0.25（重组逻辑以事件为核心）
        catalyst_strength=0.10,
        certainty=0.10,
        fundamentals=0.05,          # 0.10 → 0.05：基本面弱是该逻辑的前提，不是机会的减分项
        shareholder_structure=0.10,
        market_attention=0.05,
        history_case=0.00,
    ),
    risk_factors=(
        RiskFactorDef(
            "event_failure", "事件失败可能性", 0.30, base_severity=0.30,
            triggers=(
                RiskTrigger("has_history_failure", 0.55, "公司历史上有同类事项失败记录"),
                RiskTrigger("has_late_stage_pending_approval", 0.15,
                            "进入后期阶段但仍有前置审批未完成", mode="add"),
                RiskTrigger("is_invalidating", 1.00, "已出现失效事件"),
            ),
        ),
        RiskFactorDef(
            "regulatory", "监管 / 合规风险", 0.25, base_severity=0.20,
            triggers=(
                RiskTrigger("has_unanswered_inquiry", 0.45, "存在未回复的监管问询 / 问询函"),
                RiskTrigger("has_conflicting_media", 0.10,
                            "存在与公告口径不一致的媒体报道", mode="add"),
            ),
        ),
        RiskFactorDef(
            "fundamentals_deterioration", "公司基本面恶化", 0.20, base_severity=0.20,
            triggers=(
                RiskTrigger("loss_years_gte_2", 0.40, "连续亏损"),
                RiskTrigger("ocf_not_positive", 0.15, "经营现金流为负", mode="add"),
                RiskTrigger("one_off_attributed", 0.20,
                            "恶化已归因于一次性因素（减值 / 重组费用）",
                            mode="set"),
            ),
        ),
        RiskFactorDef(
            "pledge", "大股东质押 / 资金占用", 0.15, base_severity=0.20,
            triggers=(RiskTrigger("high_pledge", 0.35, "存在高比例股权质押"),),
        ),
        RiskFactorDef(
            "tradability", "可交易性", 0.10, base_severity=0.20,
            triggers=(RiskTrigger("is_halted", 0.80, "股票处于停牌状态"),),
        ),
    ),
    evidence_requirements={"A": 1},
    anti_patterns=(
        "ST ≠ 重组预期：is_st 只作为 C4 的部分背景证据，禁止「is_st=true → 命中重组策略」的直接规则（INV-C1）",
        "有重组公告 ≠ 重组会成功：确定性维度必须计入未确认事项与历史失败记录",
        "重组预期不是只看 ST：支持事件类型覆盖非 ST 公司的资产注入与产业整合",
    ),
    sort_order=1,
)


# --------------------------------------------------------------------------- #
# 2. turnaround 困境反转（规格示例 B / J）
# --------------------------------------------------------------------------- #
_TURNAROUND = StrategyDef(
    code=ThesisType.TURNAROUND,
    display_name="困境反转",
    status=StrategyStatus.DESIGNED,
    user_goal="寻找过去几个季度业绩较差，但最近出现经营改善迹象的公司",
    description="公司可能处于经营周期拐点",
    core_conditions=(
        CoreConditionDef("C1", "过去有明确经营恶化", 0.20, "营收连续下降 / 净利润亏损 / 毛利率下降"),
        CoreConditionDef("C2", "最近出现经营改善迹象", 0.30,
                         "营收恢复增长 / 毛利率连续 ≥2 季改善 / 经营现金流转正"),
        CoreConditionDef("C3", "改善有可归因的具体举措", 0.20,
                         "剥离亏损业务 / 降债 / 管理层变化 / 成本改善 / 资产处置"),
        CoreConditionDef("C4", "行业或公司层面需求恢复", 0.15, "行业数据 / 订单恢复"),
        CoreConditionDef("C5", "不依赖 ST 标签", 0.15, "明确允许非 ST 公司（规格示例 J）"),
    ),
    support_event_types=(
        EventType.EARNINGS_TURNAROUND, EventType.MANAGEMENT_CHANGE,
        EventType.MAJOR_CONTRACT, EventType.M_AND_A,
        EventType.REGULATORY_RISK, EventType.OTHER,
    ),
    invalidating_events=(
        InvalidationDef(EventType.EARNINGS_TURNAROUND, InvalidationSeverity.TERMINAL,
                        "经营现金流再次转负 / 亏损扩大", ("转负", "亏损扩大", "预亏")),
        InvalidationDef(EventType.EARNINGS_TURNAROUND, InvalidationSeverity.SEVERE,
                        "毛利率重新恶化", ("毛利率下降", "毛利率下滑")),
        InvalidationDef(EventType.MAJOR_CONTRACT, InvalidationSeverity.SEVERE,
                        "后续订单明显下降", ("订单下降", "订单减少", "终止")),
        InvalidationDef(EventType.REGULATORY_RISK, InvalidationSeverity.SEVERE,
                        "财务造假 / 非标审计意见", ("立案", "非标", "保留意见", "无法表示意见")),
    ),
    open_question_templates=(
        "改善是否具有持续性", "新订单是否能够转化为收入", "行业景气是否能够持续",
        "改善是否依赖一次性因素（资产处置 / 减值转回 / 政府补助）",
    ),
    catalyst_ladder=(
        CatalystStageDef("单季改善", 40),
        CatalystStageDef("连续两季改善", 70),
        CatalystStageDef("业绩预告上调", 85),
        CatalystStageDef("年报确认", 95),
    ),
    default_weights=_weights(fundamentals=0.25, event_catalyst=0.10, catalyst_strength=0.05),
    risk_factors=(
        RiskFactorDef("sustainability", "改善不可持续", 0.35, base_severity=0.30,
                      triggers=(RiskTrigger("margin_declining", 0.55, "毛利率未见连续改善"),)),
        RiskFactorDef("one_off_driven", "一次性因素驱动", 0.20, base_severity=0.25,
                      triggers=(RiskTrigger("one_off_attributed", 0.60,
                                            "改善主要来自一次性因素"),)),
        RiskFactorDef("industry_downturn", "行业景气回落", 0.20, base_severity=0.25),
        RiskFactorDef("financial_quality", "财务质量", 0.25, base_severity=0.25,
                      triggers=(RiskTrigger("receivable_growth_exceeds_revenue", 0.50,
                                            "应收账款增速显著高于营收增速"),)),
    ),
    evidence_requirements={"A": 1, "B": 1},
    anti_patterns=(
        "禁止「业绩大涨，所以看好」——必须形成 Thesis 语句并列出 Supporting Evidence / Uncertainties / Invalidating Events 三段（规格示例 B）",
        "禁止「困境反转必须找 ST」——C5 刻意反向计分（规格示例 J：策略决定为什么被发现，标签不决定策略）",
    ),
    sort_order=2,
)


# --------------------------------------------------------------------------- #
# 3. event_driven 事件驱动
# --------------------------------------------------------------------------- #
_EVENT_DRIVEN = StrategyDef(
    code=ThesisType.EVENT_DRIVEN,
    display_name="事件驱动",
    status=StrategyStatus.DESIGNED,
    user_goal="关注单一重大事件对公司的影响",
    description="存在明确的单一催化事件，事件结果将显著影响公司基本面或市场预期",
    core_conditions=(
        CoreConditionDef("C1", "存在明确的单一事件（A/B 类证据）", 0.35),
        CoreConditionDef("C2", "事件金额 / 规模对公司具备显著影响", 0.25),
        CoreConditionDef("C3", "事件有可预期的后续节点", 0.20),
        CoreConditionDef("C4", "事件方向明确（非「可能筹划」等模糊表述）", 0.20),
    ),
    support_event_types=(
        EventType.M_AND_A, EventType.BUYBACK, EventType.SHAREHOLDER_BUY,
        EventType.SHAREHOLDER_SELL, EventType.POLICY_CATALYST, EventType.MAJOR_CONTRACT,
        EventType.NEW_PRODUCT, EventType.ASSET_INJECTION, EventType.MANAGEMENT_CHANGE,
        EventType.DIVIDEND_POLICY, EventType.LITIGATION, EventType.REGULATORY_RISK,
    ),
    invalidating_events=(
        InvalidationDef(EventType.OTHER, InvalidationSeverity.TERMINAL,
                        "事件被取消 / 终止 / 失败 / 撤销", _TERMINATE_WORDS),
        InvalidationDef(EventType.MAJOR_CONTRACT, InvalidationSeverity.SEVERE,
                        "事件规模被大幅下调", ("下调", "缩减", "变更")),
    ),
    open_question_templates=(
        "事件的最终规模与执行时点", "事件对公司收入 / 利润的实际影响",
        "事件的后续节点与时间表", "事件是否附带前置条件",
    ),
    catalyst_ladder=(
        CatalystStageDef("市场传闻", 20),
        CatalystStageDef("正式公告", 40),
        CatalystStageDef("进展公告", 60),
        CatalystStageDef("实施完成", 85),
        CatalystStageDef("效果确认", 95),
    ),
    default_weights=_weights(event_catalyst=0.25, catalyst_strength=0.15, fundamentals=0.05,
                             shareholder_structure=0.05),
    risk_factors=(
        RiskFactorDef("event_failure", "事件失败", 0.35, base_severity=0.30,
                      triggers=(RiskTrigger("is_invalidating", 1.00, "已出现失效事件"),)),
        RiskFactorDef("impact_overestimated", "事件影响被高估", 0.25, base_severity=0.30),
        RiskFactorDef("hype_reversal", "短期炒作后回落", 0.20, base_severity=0.25,
                      triggers=(RiskTrigger("social_buzz_only", 0.60,
                                            "热度主要来自 E 类市场讨论，缺少硬证据"),)),
        RiskFactorDef("fundamentals", "基本面", 0.20, base_severity=0.25,
                      triggers=(RiskTrigger("not_profitable", 0.45, "公司当前不盈利"),)),
    ),
    evidence_requirements={"A": 1},
    anti_patterns=(
        "「事件驱动 = 不看基本面」：FUNDAMENTALS 权重降为 0.05 但不为 0，风险维度必含基本面因素",
        "「有事件就加分」：C4 要求方向明确；「拟筹划」「正在论证」不得计入 C1",
    ),
    sort_order=3,
)


# --------------------------------------------------------------------------- #
# 4. ma_integration 并购 / 产业整合（规格示例 F）
# --------------------------------------------------------------------------- #
_MA_INTEGRATION = StrategyDef(
    code=ThesisType.MA_INTEGRATION,
    display_name="并购 / 产业整合",
    status=StrategyStatus.DESIGNED,
    user_goal="寻找行业集中度提升、产业整合，或上市公司通过并购获得新业务的机会",
    description="行业集中度提升 + 标的与上市公司业务协同，但必须同时评估交易质量与风险",
    core_conditions=(
        CoreConditionDef("C1", "发生并购 / 收购事件（A 类公告）", 0.25),
        CoreConditionDef("C2", "标的与上市公司存在业务协同", 0.20),
        CoreConditionDef("C3", "交易质量可评估（价格 / 盈利能力 / 资产质量可获取）", 0.25),
        CoreConditionDef("C4", "行业集中度正在提升", 0.15),
        CoreConditionDef("C5", "新增业务确实进入上市公司体系（非仅意向）", 0.15),
    ),
    support_event_types=(EventType.M_AND_A, EventType.ASSET_INJECTION,
                         EventType.CONTROL_CHANGE),
    invalidating_events=(
        InvalidationDef(EventType.M_AND_A, InvalidationSeverity.TERMINAL,
                        "并购终止 / 失败", _TERMINATE_WORDS),
        InvalidationDef(EventType.EARNINGS_TURNAROUND, InvalidationSeverity.SEVERE,
                        "业绩承诺未达标", ("未达标", "未完成", "补偿")),
        InvalidationDef(EventType.REGULATORY_RISK, InvalidationSeverity.TERMINAL,
                        "商誉大额减值", ("商誉减值", "计提减值")),
        InvalidationDef(EventType.MANAGEMENT_CHANGE, InvalidationSeverity.SEVERE,
                        "整合失败（管理层变动 / 业务剥离）", ("剥离", "出售", "退出")),
    ),
    open_question_templates=(
        "收购价格", "商誉风险", "业绩承诺条款", "并购标的盈利能力",
        "标的资产质量", "融资方式", "整合难度", "上市公司历史并购表现",
    ),
    catalyst_ladder=(
        CatalystStageDef("意向协议", 30),
        CatalystStageDef("正式方案", 55),
        CatalystStageDef("过会 / 交割", 80),
        CatalystStageDef("整合见效（需 ≥1 年）", 95),
    ),
    default_weights=_weights(event_catalyst=0.20, catalyst_strength=0.05,
                             fundamentals=0.10, shareholder_structure=0.05,
                             history_case=0.10),
    risk_factors=(
        RiskFactorDef("goodwill", "商誉减值", 0.25, base_severity=0.30),
        RiskFactorDef("integration", "整合失败", 0.25, base_severity=0.30),
        RiskFactorDef("overpriced", "高溢价收购", 0.20, base_severity=0.30),
        RiskFactorDef("target_miss", "标的盈利不达预期", 0.20, base_severity=0.30),
        RiskFactorDef("dilution", "融资摊薄", 0.10, base_severity=0.20),
    ),
    evidence_requirements={"A": 1},
    anti_patterns=(
        "「发生并购」≠「一定是好机会」：必须同时分析 催化剂 + 交易质量 + 风险（规格示例 F）",
        "八项调查清单（价格 / 商誉 / 承诺 / 标的盈利 / 资产质量 / 融资 / 整合 / 历史）不是可选项，未获取项必须进 OpenQuestion",
    ),
    sort_order=4,
)


# --------------------------------------------------------------------------- #
# 5. shareholder_action 股东行为（规格示例 G）
# --------------------------------------------------------------------------- #
_SHAREHOLDER_ACTION = StrategyDef(
    code=ThesisType.SHAREHOLDER_ACTION,
    display_name="股东行为",
    status=StrategyStatus.DESIGNED,
    user_goal="关注公司管理层或主要股东用真金白银表达信心的情况",
    description="控股股东 / 管理层 / 公司自身通过增持或回购表达信心",
    core_conditions=(
        CoreConditionDef("C1", "控股股东 / 管理层增持，或公司回购", 0.30),
        CoreConditionDef("C2", "规模显著（相对市值 / 流通股比例）", 0.20),
        CoreConditionDef("C3", "资金来源可评估", 0.20),
        CoreConditionDef("C4", "公司现金流稳定、负债可控", 0.20),
        CoreConditionDef("C5", "无大股东质押风险", 0.10),
    ),
    support_event_types=(EventType.SHAREHOLDER_BUY, EventType.BUYBACK,
                         EventType.MANAGEMENT_CHANGE),
    invalidating_events=(
        InvalidationDef(EventType.SHAREHOLDER_BUY, InvalidationSeverity.SEVERE,
                        "增持计划未实施 / 提前终止", ("未实施", "终止", "延期")),
        InvalidationDef(EventType.BUYBACK, InvalidationSeverity.SEVERE,
                        "回购未执行 / 大幅缩水", ("未执行", "终止", "缩减")),
        InvalidationDef(EventType.SHAREHOLDER_SELL, InvalidationSeverity.TERMINAL,
                        "出现减持", ()),
        InvalidationDef(EventType.REGULATORY_RISK, InvalidationSeverity.TERMINAL,
                        "资金来源暴露问题（占款 / 违规）", ("占用", "违规", "立案")),
    ),
    open_question_templates=(
        "增持资金来源", "增持规模", "增持价格区间", "历史增持后的表现",
        "公司现金流", "是否存在高负债", "是否存在大股东质押",
    ),
    catalyst_ladder=(
        CatalystStageDef("计划公告", 40),
        CatalystStageDef("开始实施", 60),
        CatalystStageDef("实施完成", 70),
        CatalystStageDef("后续无减持（观察期内）", 95),
    ),
    default_weights=_weights(event_catalyst=0.15, catalyst_strength=0.05,
                             shareholder_structure=0.20),
    risk_factors=(
        RiskFactorDef("action_failure", "增持 / 回购计划未实施或缩水", 0.20,
                      base_severity=0.25,
                      triggers=(RiskTrigger("is_invalidating", 0.85,
                                            "已出现增持/回购计划未实施、终止或缩减"),)),
        RiskFactorDef("tiny_scale", "增持规模过小（象征性）", 0.20, base_severity=0.30),
        RiskFactorDef("funding_doubt", "资金来源存疑", 0.20, base_severity=0.25),
        RiskFactorDef("pledge", "大股东质押", 0.15, base_severity=0.20,
                      triggers=(RiskTrigger("high_pledge", 0.50, "存在高比例股权质押"),)),
        RiskFactorDef("history", "历史增持后表现差", 0.15, base_severity=0.25),
        RiskFactorDef("high_leverage", "高负债", 0.10, base_severity=0.25,
                      triggers=(RiskTrigger("debt_ratio_rising", 0.45,
                                            "资产负债率同比上升"),)),
    ),
    evidence_requirements={"A": 1},
    anti_patterns=(
        "AI 不应只做正向判断：hunt_risk 对本策略为强制，必须输出 7 项检查中至少 3 项的反向结论（若确实无问题须显式说明「已检查 X/Y/Z，未发现问题」）",
    ),
    sort_order=5,
)


# --------------------------------------------------------------------------- #
# 6. policy 政策驱动（规格示例 E）· 自上而下入口
# --------------------------------------------------------------------------- #
_POLICY = StrategyDef(
    code=ThesisType.POLICY,
    display_name="政策驱动",
    status=StrategyStatus.DESIGNED,
    user_goal="寻找政策发生重大变化后，可能直接受益的行业和公司",
    description="政策 → 行业 → 产业链 → 公司业务 → 实际收入/订单 的完整证据链",
    core_conditions=(
        CoreConditionDef("C1", "政策发生重大变化（官方文件 / 部委发布）", 0.20),
        CoreConditionDef("C2", "识别出受影响行业", 0.15),
        CoreConditionDef("C3", "产业链拆解到具体环节（非笼统「相关行业」）", 0.20),
        CoreConditionDef("C4", "公司主营业务高度相关（非「属于该行业」）", 0.20),
        CoreConditionDef("C5", "已有实际订单 / 产能 / 收入验证", 0.25),
    ),
    support_event_types=(EventType.POLICY_CATALYST, EventType.MAJOR_CONTRACT,
                         EventType.NEW_PRODUCT),
    invalidating_events=(
        InvalidationDef(EventType.POLICY_CATALYST, InvalidationSeverity.SEVERE,
                        "政策落地不及预期 / 细则缺失", ("暂缓", "延后", "调整")),
        InvalidationDef(EventType.POLICY_CATALYST, InvalidationSeverity.TERMINAL,
                        "政策转向 / 被撤销", ("撤销", "废止", "取消")),
        InvalidationDef(EventType.REGULATORY_RISK, InvalidationSeverity.TERMINAL,
                        "公司被明确排除在受益名单外", ("排除", "不符合", "不予")),
    ),
    open_question_templates=(
        "政策细则与实施时点", "公司所在产业链环节的受益程度",
        "公司是否已有实际订单 / 收入", "政策兑现的时间周期",
    ),
    catalyst_ladder=(
        CatalystStageDef("政策发布", 30),
        CatalystStageDef("细则出台", 55),
        CatalystStageDef("公司订单落地", 80),
        CatalystStageDef("收入体现", 95),
    ),
    default_weights=_weights(event_catalyst=0.30, fundamentals=0.05,
                             shareholder_structure=0.00, market_attention=0.10),
    risk_factors=(
        RiskFactorDef("landing", "政策落地不确定", 0.30, base_severity=0.30),
        RiskFactorDef("long_chain", "传导链条过长", 0.25, base_severity=0.30),
        RiskFactorDef("limited_benefit", "公司受益有限", 0.25, base_severity=0.30),
        RiskFactorDef("long_payoff", "兑现周期过长", 0.20, base_severity=0.25),
    ),
    evidence_requirements={"A": 1},
    anti_patterns=(
        "★ 不能仅仅因为「某股票属于政策相关行业」就判定为机会（规格示例 E 加粗强调）：必须走完 政策 → 行业 → 产业链 → 公司业务 → 实际收入/订单 五步",
        "顺序门控：C5 未命中时 coverage 上限强制为 0.45 —— 「只有政策相关、没有业务验证」不可能获得高匹配度（docs/06 §14.2）",
    ),
    sort_order=6,
)


# --------------------------------------------------------------------------- #
# 7. cycle 行业周期反转（规格示例 H）· 自上而下入口
# --------------------------------------------------------------------------- #
_CYCLE = StrategyDef(
    code=ThesisType.CYCLE,
    display_name="行业周期反转",
    status=StrategyStatus.DESIGNED,
    user_goal="寻找经历长期下行后，行业供需开始改善的公司",
    description="行业供给收缩 + 产品价格回升 + 公司成本优势",
    core_conditions=(
        CoreConditionDef("C1", "过去经历长期下行", 0.20, "价格持续下降 / 盈利恶化 / 资本开支下降"),
        CoreConditionDef("C2", "最近出现供给收缩", 0.25, "库存下降 / 产能出清 / 停产检修"),
        CoreConditionDef("C3", "产品价格反弹", 0.20),
        CoreConditionDef("C4", "行业产能利用率提升", 0.15),
        CoreConditionDef("C5", "公司成本低于行业平均", 0.20),
    ),
    support_event_types=(EventType.OTHER, EventType.MAJOR_CONTRACT,
                         EventType.EARNINGS_TURNAROUND),
    invalidating_events=(
        InvalidationDef(EventType.OTHER, InvalidationSeverity.TERMINAL,
                        "产品价格重新下跌", ("价格下跌", "价格回落", "降价")),
        InvalidationDef(EventType.OTHER, InvalidationSeverity.SEVERE,
                        "库存重新累积", ("库存上升", "累库")),
        InvalidationDef(EventType.OTHER, InvalidationSeverity.TERMINAL,
                        "新增产能投放超预期", ("新增产能", "扩产", "投产")),
        InvalidationDef(EventType.EARNINGS_TURNAROUND, InvalidationSeverity.SEVERE,
                        "公司成本优势消失", ("成本上升", "毛利率下降")),
    ),
    open_question_templates=(
        "供给收缩是否可持续", "需求端是否真正恢复", "产品价格反弹的持续性",
        "行业新增产能计划", "公司成本优势的来源与可持续性",
    ),
    catalyst_ladder=(
        CatalystStageDef("价格止跌", 30),
        CatalystStageDef("库存下降 + 开工率提升", 55),
        CatalystStageDef("价格反弹 + 行业盈利改善", 80),
        CatalystStageDef("持续多季验证", 95),
    ),
    default_weights=_weights(event_catalyst=0.10, catalyst_strength=0.15,
                             fundamentals=0.20, shareholder_structure=0.00,
                             history_case=0.05),
    risk_factors=(
        RiskFactorDef("misjudgement", "周期误判", 0.30, base_severity=0.30),
        RiskFactorDef("supply_not_lasting", "供给收缩不持续", 0.25, base_severity=0.30),
        RiskFactorDef("demand_not_recovered", "需求端未恢复", 0.25, base_severity=0.30),
        RiskFactorDef("cost_advantage_lost", "公司成本优势消失", 0.20, base_severity=0.25),
    ),
    anti_patterns=(
        "★ 必须能够「先发现行业机会，再向下寻找公司」（规格示例 H）：候选池不允许从 Company 表正查开始，入口必须是行业级信号",
    ),
    sort_order=7,
)


# --------------------------------------------------------------------------- #
# 8. growth 成长（规格示例 D）
# --------------------------------------------------------------------------- #
_GROWTH = StrategyDef(
    code=ThesisType.GROWTH,
    display_name="成长",
    status=StrategyStatus.DESIGNED,
    user_goal="寻找行业高速增长、公司收入和订单持续增长，同时具有新业务扩张能力的公司",
    description="行业景气上行 + 公司份额提升 + 新业务放量",
    core_conditions=(
        CoreConditionDef("C1", "行业需求快速增长", 0.20),
        CoreConditionDef("C2", "收入连续多个季度增长", 0.20),
        CoreConditionDef("C3", "新订单增长", 0.20),
        CoreConditionDef("C4", "新产能投产 / 新产品开始贡献收入", 0.20),
        CoreConditionDef("C5", "市场份额提升", 0.20),
    ),
    support_event_types=(EventType.MAJOR_CONTRACT, EventType.NEW_PRODUCT,
                         EventType.EARNINGS_TURNAROUND, EventType.M_AND_A,
                         EventType.POLICY_CATALYST),
    invalidating_events=(
        InvalidationDef(EventType.EARNINGS_TURNAROUND, InvalidationSeverity.SEVERE,
                        "增速连续两季下滑", ("增速下滑", "增速放缓")),
        InvalidationDef(EventType.MAJOR_CONTRACT, InvalidationSeverity.TERMINAL,
                        "订单下滑", ("订单下降", "订单减少", "终止")),
        InvalidationDef(EventType.NEW_PRODUCT, InvalidationSeverity.SEVERE,
                        "新业务不及预期", ("不及预期", "未达预期", "终止")),
        InvalidationDef(EventType.OTHER, InvalidationSeverity.SEVERE,
                        "行业产能过剩", ("产能过剩", "供给过剩")),
    ),
    open_question_templates=(
        "行业增长的持续性", "订单转化为收入的比例", "新产能的产能利用率",
        "新产品收入占比", "客户集中度与份额数据的来源",
    ),
    catalyst_ladder=(
        CatalystStageDef("单季增长", 40),
        CatalystStageDef("连续多季增长", 70),
        CatalystStageDef("订单 + 产能 + 新品齐备", 80),
        CatalystStageDef("份额提升被确认", 95),
    ),
    default_weights=_weights(event_catalyst=0.15, fundamentals=0.20,
                             shareholder_structure=0.00, history_case=0.05),
    risk_factors=(
        RiskFactorDef("growth_unsustainable", "增速不可持续", 0.30, base_severity=0.30),
        RiskFactorDef("competition", "竞争加剧", 0.20, base_severity=0.25),
        RiskFactorDef("overcapacity", "产能过剩", 0.20, base_severity=0.25),
        RiskFactorDef("valuation", "估值过高", 0.20, base_severity=0.25,
                      triggers=(RiskTrigger("high_valuation", 0.55,
                                            "估值处于历史高位区间"),)),
        RiskFactorDef("customer_concentration", "客户集中", 0.10, base_severity=0.25),
    ),
    anti_patterns=(
        "系统必须主动寻找：行业数据 / 公司订单 / 产能利用率 / 新产品收入 / 客户数量 / 市场份额 / 资本开支 —— 而不是只看股价上涨（规格示例 D）",
    ),
    sort_order=8,
)


# --------------------------------------------------------------------------- #
# 9. product 技术 / 新产品突破（规格示例 I）
# --------------------------------------------------------------------------- #
_PRODUCT = StrategyDef(
    code=ThesisType.PRODUCT,
    display_name="技术 / 新产品突破",
    status=StrategyStatus.DESIGNED,
    user_goal="寻找研发成果从实验室/概念逐步进入商业化的公司",
    description="研发成果正在跨越商业化门槛",
    core_conditions=(
        CoreConditionDef("C1", "长期研发投入（历史证据）", 0.10),
        CoreConditionDef("C2", "研发成果 / 产品认证取得", 0.20),
        CoreConditionDef("C3", "客户验证通过", 0.20),
        CoreConditionDef("C4", "签署商业订单", 0.25),
        CoreConditionDef("C5", "新产品开始贡献收入", 0.25),
    ),
    support_event_types=(EventType.NEW_PRODUCT, EventType.MAJOR_CONTRACT,
                         EventType.M_AND_A),
    invalidating_events=(
        InvalidationDef(EventType.NEW_PRODUCT, InvalidationSeverity.TERMINAL,
                        "认证失败 / 未通过", ("未通过", "失败", "撤回")),
        InvalidationDef(EventType.MAJOR_CONTRACT, InvalidationSeverity.SEVERE,
                        "客户流失 / 未续单", ("终止", "未续", "取消")),
        InvalidationDef(EventType.NEW_PRODUCT, InvalidationSeverity.SEVERE,
                        "新品收入不及预期", ("不及预期", "未达预期")),
        InvalidationDef(EventType.NEW_PRODUCT, InvalidationSeverity.TERMINAL,
                        "技术路线被替代", ("替代", "淘汰")),
    ),
    open_question_templates=(
        "认证与客户验证的实际进展", "订单的可执行性与交付条件",
        "新产品收入确认的时点与规模", "技术路线的竞争格局", "研发投入对当期业绩的拖累",
    ),
    catalyst_ladder=(
        CatalystStageDef("研发成果", 20),
        CatalystStageDef("产品认证", 45),
        CatalystStageDef("首批客户", 65),
        CatalystStageDef("商业订单", 85),
        CatalystStageDef("收入贡献", 95),
    ),
    default_weights=_weights(event_catalyst=0.15, catalyst_strength=0.20,
                             fundamentals=0.10, shareholder_structure=0.00,
                             history_case=0.05),
    risk_factors=(
        RiskFactorDef("commercialization", "商业化失败", 0.35, base_severity=0.35),
        RiskFactorDef("tech_substitution", "技术替代", 0.20, base_severity=0.25),
        RiskFactorDef("customer_concentration", "客户集中", 0.20, base_severity=0.25),
        RiskFactorDef("rd_drag", "研发投入拖累业绩", 0.25, base_severity=0.25,
                      triggers=(RiskTrigger("not_profitable", 0.50,
                                            "公司当前尚不盈利"),)),
    ),
    anti_patterns=(
        "★ 不能把「宣布研发成功」直接等同于「商业成功」（规格示例 I 加粗强调）",
        "顺序门控：C3（客户验证）未命中时，C4 / C5 一律计 0 —— 「只有研发公告」的公司结构上不可能获得高匹配度（docs/06 §14.2）",
    ),
    sort_order=9,
)


# --------------------------------------------------------------------------- #
# 10. value 价值发现 / 高股息（规格示例 C）
# --------------------------------------------------------------------------- #
_VALUE = StrategyDef(
    code=ThesisType.VALUE,
    display_name="价值发现 / 高股息",
    status=StrategyStatus.DESIGNED,
    user_goal="寻找盈利稳定、现金流健康、持续分红且估值合理的公司",
    description="股东回报改善：分红政策变化 + 回购 + 现金流 + 估值变化 组合成的 Thesis",
    core_conditions=(
        CoreConditionDef("C1", "连续多年盈利", 0.15),
        CoreConditionDef("C2", "自由现金流稳定 / 经营现金流长期为正", 0.20),
        CoreConditionDef("C3", "连续多年分红", 0.15),
        CoreConditionDef("C4", "最近提高分红比例，或发布回购计划", 0.25),
        CoreConditionDef("C5", "估值处于历史较低区间", 0.20),
        CoreConditionDef("C6", "低市场关注度（可选加分项）", 0.05),
    ),
    support_event_types=(EventType.DIVIDEND_POLICY, EventType.BUYBACK,
                         EventType.EARNINGS_TURNAROUND, EventType.SHAREHOLDER_BUY),
    invalidating_events=(
        InvalidationDef(EventType.DIVIDEND_POLICY, InvalidationSeverity.TERMINAL,
                        "分红大幅下调 / 取消", ("下调", "取消", "不分配", "不派发")),
        InvalidationDef(EventType.EARNINGS_TURNAROUND, InvalidationSeverity.SEVERE,
                        "经营现金流转负", ("转负", "为负")),
        InvalidationDef(EventType.REGULATORY_RISK, InvalidationSeverity.TERMINAL,
                        "审计意见非标 / 财务造假", ("立案", "非标", "保留意见", "无法表示意见")),
        InvalidationDef(EventType.EARNINGS_TURNAROUND, InvalidationSeverity.SEVERE,
                        "大额减值", ("商誉减值", "大额减值", "计提减值")),
    ),
    open_question_templates=(
        "分红政策的可持续性", "自由现金流的稳定性", "估值低的原因（是否存在价值陷阱）",
        "行业是否处于长期衰退", "公司治理与股东回报意愿",
    ),
    catalyst_ladder=(
        CatalystStageDef("分红政策变化", 40),
        CatalystStageDef("提高分红比例", 70),
        CatalystStageDef("分红 + 回购组合", 85),
        CatalystStageDef("连续多年稳定回报", 95),
    ),
    default_weights=_weights(event_catalyst=0.10, fundamentals=0.25,
                             shareholder_structure=0.05),
    risk_factors=(
        RiskFactorDef("value_trap", "价值陷阱", 0.30, base_severity=0.30),
        RiskFactorDef("cashflow_deterioration", "现金流恶化", 0.25, base_severity=0.25,
                      triggers=(RiskTrigger("ocf_not_positive", 0.60,
                                            "经营现金流不为正"),)),
        RiskFactorDef("industry_decline", "行业长期衰退", 0.20, base_severity=0.25),
        RiskFactorDef("governance", "治理问题", 0.15, base_severity=0.20,
                      triggers=(RiskTrigger("has_conflicting_media", 0.50,
                                            "存在与公告口径不一致的媒体报道"),)),
        RiskFactorDef("valuation", "估值风险", 0.10, base_severity=0.20,
                      triggers=(RiskTrigger("high_valuation", 0.55,
                                            "估值处于历史高位区间"),)),
    ),
    anti_patterns=(
        "★ 同一事件对不同用户意义不同（规格 §5.8）：回购事件对「高股息」用户的 match_score 仅 55，对「股东回报」用户为 94 —— 回购不直接等于股息",
        "这里的「事件」不一定是重大新闻：分红政策 + 回购 + 现金流 + 估值变化 可以组合成「股东回报改善」Thesis，说明本系统并不只适合事件驱动策略",
    ),
    sort_order=10,
)


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
STRATEGIES: dict[ThesisType, StrategyDef] = {
    s.code: s
    for s in (
        _RESTRUCTURING,
        _TURNAROUND,
        _EVENT_DRIVEN,
        _MA_INTEGRATION,
        _SHAREHOLDER_ACTION,
        _POLICY,
        _CYCLE,
        _GROWTH,
        _PRODUCT,
        _VALUE,
    )
}

#: 实现顺序（docs/06 §16）
IMPLEMENTATION_ORDER: tuple[ThesisType, ...] = tuple(
    s.code for s in sorted(STRATEGIES.values(), key=lambda x: x.sort_order)
)


def get_def(thesis_type: ThesisType | str) -> StrategyDef:
    return STRATEGIES[ThesisType(thesis_type)]


def weights_for(thesis_type: ThesisType | str) -> dict[ScoreDimension, float]:
    """策略级权重覆盖后的完整权重表（docs/04 §3.1）。"""
    return dict(get_def(thesis_type).default_weights)


def implemented_types() -> tuple[ThesisType, ...]:
    return tuple(c for c, s in STRATEGIES.items() if s.status == StrategyStatus.IMPLEMENTED)


def designed_types() -> tuple[ThesisType, ...]:
    return tuple(c for c, s in STRATEGIES.items() if s.status == StrategyStatus.DESIGNED)


# --------------------------------------------------------------------------- #
# 预设策略模板（规格 §5.1 ~ §5.5）
# --------------------------------------------------------------------------- #
STRATEGY_TEMPLATES: dict[str, dict[ThesisType, float]] = {
    "重组猎手": {
        ThesisType.RESTRUCTURING: 0.40,
        ThesisType.MA_INTEGRATION: 0.25,
        ThesisType.TURNAROUND: 0.15,
        ThesisType.SHAREHOLDER_ACTION: 0.10,
        ThesisType.EVENT_DRIVEN: 0.10,
    },
    "困境反转": {
        ThesisType.TURNAROUND: 0.40,
        ThesisType.CYCLE: 0.20,
        ThesisType.RESTRUCTURING: 0.15,
        ThesisType.EVENT_DRIVEN: 0.15,
        ThesisType.VALUE: 0.10,
    },
    "价值发现": {
        ThesisType.VALUE: 0.45,
        ThesisType.SHAREHOLDER_ACTION: 0.20,
        ThesisType.TURNAROUND: 0.20,
        ThesisType.CYCLE: 0.15,
    },
    "成长投资": {
        ThesisType.GROWTH: 0.40,
        ThesisType.PRODUCT: 0.25,
        ThesisType.POLICY: 0.20,
        ThesisType.EVENT_DRIVEN: 0.15,
    },
    "事件驱动": {
        ThesisType.EVENT_DRIVEN: 0.40,
        ThesisType.MA_INTEGRATION: 0.20,
        ThesisType.SHAREHOLDER_ACTION: 0.20,
        ThesisType.POLICY: 0.10,
        ThesisType.RESTRUCTURING: 0.10,
    },
}

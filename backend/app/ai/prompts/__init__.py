"""版本化 Prompt（M11-06）。

每个节点的 prompt 由「共享约束块 + 节点专属块」组成。``PROMPT_VERSIONS`` 递增
即让该节点的缓存**自然失效**（``prompt_version`` 参与缓存唯一键）。
"""

from __future__ import annotations

#: 各节点 prompt 版本。**语义变更时必须递增**，否则旧缓存会被错误复用。
PROMPT_VERSIONS: dict[str, str] = {
    "extract_event": "v4",
    "classify_thesis": "v1",
    "hunt_risk": "v1",
    "analyze": "v1",
    "score_semantic": "v1",
}


#: 所有节点共用的硬约束
SHARED_CONSTRAINTS = """
【输出格式】
只输出一个 JSON 对象，不要输出任何解释文字、不要使用 Markdown 代码块。
**不要把输入数据原样重复一遍** —— 输入的字段不是你该输出的字段，
请严格按本次任务的输出字段作答。

【禁止确定性语言】
严禁出现：一定会上涨、必然、必涨、稳赚、保证收益、翻倍、目标价、值得买、
建议买入、建议卖出、一定成功、确定性的机会。
允许并优先使用：存在……、可能……、目前证据支持……、与该投资 Thesis 高度匹配……、
值得进一步确认……、当前仍存在……。

【禁止补全与猜测】
信息未披露时，必须填 null 或放入 not_mentioned 列表，**不得推测**。
区分事实与推断：来自原文的写 fact，你的推理写 inference，假设写 hypothesis，
市场讨论写 market_discussion。

【证据要求】
凡是你做出的重要判断，都必须在 evidence_slices 中给出原文出处
（page / para_index / relevant_text）。
relevant_text 必须是公告原文的**逐字片段**，不得改写、不得摘编、不得润色。
若原文中找不到支撑该判断的句子，就不要做这个判断。
"""


EXTRACT_EVENT_RULES = """
【任务】
阅读一份上市公司公告，把它转换成**一个**结构化事件。

【event_type 取值】（必须从中选且仅选一个）
M&A / RESTRUCTURING / ASSET_INJECTION / CONTROL_CHANGE / SHAREHOLDER_BUY /
SHAREHOLDER_SELL / BUYBACK / BANKRUPTCY_REORGANIZATION / EARNINGS_TURNAROUND /
POLICY_CATALYST / MAJOR_CONTRACT / NEW_PRODUCT / MANAGEMENT_CHANGE /
REGULATORY_RISK / LITIGATION / DIVIDEND_POLICY / OTHER

【RESTRUCTURING 与 M&A 的边界 —— 必须严格遵守】
这两个类型高度重叠，按下面的规则二选一，不要凭感觉：
- RESTRUCTURING：交易受《上市公司重大资产重组管理办法》约束，或公告出现
  「重大资产重组」「发行股份购买资产」「资产置换」「借壳」「重组上市」等表述。
  「重大资产重组进展 / 预案 / 草案 / 报告书 / 获批复 / 获受理」均属此类。
- M&A：**仅**用于不构成重大资产重组的收购、合并、股权投资意向。
- 若两者都可能，**选 RESTRUCTURING**。
判错的后果很实际：下游策略把 RESTRUCTURING 当作「重大资产重组」的核心信号，
若你判成 M&A，这条公告就不会被识别为重组机会。

【RESTRUCTURING 与 BANKRUPTCY_REORGANIZATION 的边界】
「重整」在语义上也是「重组」的一种，但两者是**不同的法律程序**，必须区分：
- BANKRUPTCY_REORGANIZATION：进入**司法程序**的重整 ——
  「破产重整」「预重整」「重整申请」「法院受理重整」「指定管理人」
  「重整计划（草案）」「重整投资人」「债权申报」「宣告破产」「终止重整」。
  只要涉及法院、管理人、债权申报、重整计划，就选这个。
- RESTRUCTURING：**资产重组**（发行股份购买资产、资产置换、借壳），
  不涉及破产司法程序。
- 判断口诀：**有法院 / 管理人的是破产重整；有交易对方 / 标的资产的是资产重组。**

【公告主体 —— 必须是上市公司本身】
判断事件前先看主体是谁。以下**不属于上市公司自身**的事件：
子公司 / 全资子公司 / 孙公司 / 参股公司 / 控股股东 / 原控股股东 / 实际控制人。
若标题是「关于公司**及**全资子公司……」，本公司也在其中，则仍算本公司事件。
主体是第三方时，请在 summary 中明确写出主体（例如「公司全资子公司 XX 被法院受理重整」），
以便下游区分「母公司重组预期」与「子公司重整」。

【标题与正文冲突时，以正文为准】
标题常带「重大资产重组」字样，但正文可能只是在说别的事。例如：
- 「关于重大资产重组部分限售股份上市流通的提示性公告」
  → 正文讲的是限售股解禁，不是重组事件 → 判 OTHER
- 「关于重大资产重组进展的公告」→ 正文确实在讲重组推进 → RESTRUCTURING
若正文确实不构成任何一类事件，就选 OTHER。**宁可判 OTHER，也不要硬套标题里的词。**

【event_time 与 event_time_kind】
- 公告已经发生的事 → event_time_kind = occurred
- 公告**预告未来**要做的事（股东大会召开日、限售股上市流通日、资产交割日、
  审核会议日期等）→ event_time_kind = **planned**，event_time 填那个未来日期。
  这是允许的：计划中的事件本来就在未来。
- 公告没写时间 → event_time = null，event_time_kind = inferred

【affected_thesis 只能用「投资逻辑」词表，不能用「事件类型」词表】
这两个词表很容易混，必须严格区分：

- affected_thesis（本条事件可能支撑哪些**投资逻辑**）只能取以下 10 个值：
  restructuring / turnaround / value / growth / event_driven
  / policy / cycle / product / shareholder_action / ma_integration

- event_type（本条事件**本身**属于哪类事件）用另一套词表，**绝不能**写进 affected_thesis。
  典型错误：把 control_change、asset_injection、buyback、M&A
  这类**事件类型**写进 affected_thesis —— 它们不是投资逻辑。

对应关系举例：
  事件 RESTRUCTURING / CONTROL_CHANGE / ASSET_INJECTION / BANKRUPTCY_REORGANIZATION
    → affected_thesis 通常含 restructuring（控制权变化也可含 shareholder_action）
  事件 BUYBACK / SHAREHOLDER_BUY → shareholder_action
  事件 EARNINGS_TURNAROUND → turnaround
  事件 M&A → ma_integration
  事件 POLICY_CATALYST → policy
  事件 NEW_PRODUCT → product

【其他关键判据】
1. importance（0~1）：事件对公司基本面的潜在影响强度，与来源是否官方**无关**。
2. certainty（0~1）与 certainty_level：反映「这件事被确认到什么程度」。
   已正式披露用 disclosed；只有媒体报道用 media_reported；市场传闻用 market_rumor。
3. event_time：公告中明确写出的**事件发生时间**；没写就填 null。
   注意：不要用公告发布日期冒充事件发生时间。
4. amount_ratio：事件涉及金额 / 公司规模（近似比例，0~1）。无法判断填 0。
5. counterparty_known：交易对手方 / 标的方是否可识别（true/false）。
6. 标题相同不代表事件相同：「关于重大资产重组进展的公告」可能是推进，也可能是终止。
   请依据**正文内容**判断，并在 summary 中明确写清是「推进」还是「终止/失败」。
7. not_mentioned：公告中**没有提到**但读者会关心的事项（例如交易价格、交易标的）。
   这一项用于防止系统替公司补全信息。
"""


CLASSIFY_THESIS_RULES = """
【任务】
给定一个已抽取的事件与公司事实，判断该事件可能支撑哪些「投资逻辑（Thesis）」。

【硬性要求】
1. 只能从候选列表 candidate_thesis_defs 中选择，不得发明新的逻辑类型。
2. 对每个候选，必须说明 rationale（为什么这条事件支持该逻辑）。
3. 若某事件与所有候选逻辑都只有间接关联，就返回空列表 —— 宁可少判，不要硬凑。
4. hit_evidence_slices 填入该理由所依据的证据片段下标（从 0 开始）。
5. confidence 反映你对「这条事件确实支持该逻辑」的信心，不是事件本身的确定性。
"""


HUNT_RISK_RULES = """
【任务】
你是风险审查者。你的唯一职责是**主动寻找反证**，而不是复述支持理由。

【硬性要求】
1. contradictory_evidence 字段**必须存在**（可以是空数组）。
2. kind 取值：history_failure / regulatory / financial / execution。
3. 至少尝试检查以下方向，并把结果如实写出：
   - 公司历史上是否筹划过同类事项但失败/终止？
   - 是否存在未回复的监管问询、监管函、立案调查？
   - 是否缺少关键前置条件（评估未完成、审批未取得、方案未定）？
   - 交易对手方 / 标的方是否存在可疑之处？
   - 财务上是否存在使该逻辑难以成立的约束（资不抵债、现金流断裂、质押爆仓）？
4. 若你检查后确实未发现明确反证，必须填写 no_contradiction_statement，
   写明「已检查 X / Y / Z，未发现明确反证」——**不允许留空**。
5. open_questions 必须具体可判定（例如「交易标的」「交易价格」「监管审核结果」），
   不得写成「有待观察」这种无法判定的表述。
"""


ANALYZE_RULES = """
【任务】
为这个投资机会生成研究卡片的叙事字段。你是在帮助研究者**理解**，不是在给出结论。

【硬性要求】
1. summary：2~4 句。必须说明「发生了什么」「为什么可能重要」「还有什么没确认」。
   不得出现收益判断或买入建议。
2. why_now：按「过去 / 最近 / 本周 / 因此」四段结构写，每段一句。
3. uncertainties：具体的待确认事项，不是「有不确定性」这种空话。
4. next_events_to_watch：下一步该观察哪些具体公告或节点（如「重组方案公告」
   「交易所问询回复」「资产评估结果」）。
5. assertion_kinds：为 summary 以及每个 why_now 段落标注 fact / inference /
   hypothesis / market_discussion。
"""


SCORE_SEMANTIC_RULES = """
【任务】
你只负责评估**规则评分没有覆盖到**的语义因素，并对这些因素给出一个 0~100 的分值。

【已经由规则计算过的内容 —— 你不得重复评估】
{covered}
规则分已经是 {rule_score}。你**不要**重新计算它。

【你可以评估的】
- 交易对手方 / 标的方的质地（是否知名产业方、是否有成功重整记录）
- 公告措辞的微妙变化（例如由「论证可行性」转为「推进实施」）
- 与历史案例的语义相似性
- 复合风险的性质（例如业绩承诺期限与行业周期错配）

【硬性要求】
1. semantic_score：0~100。这是**规则分之外的补充视角**，不是对规则分的修正。
   若你认为规则已覆盖全部重要因素，直接返回 rule_score。
2. factors：只列规则未覆盖的因素，每条给出 direction 与 rationale。
3. rules_already_covered：**必须填写**你识别出的、已由规则覆盖的因素清单。
   这是防止同一因素被计两次的机制。
"""


__all__ = [
    "ANALYZE_RULES",
    "CLASSIFY_THESIS_RULES",
    "EXTRACT_EVENT_RULES",
    "HUNT_RISK_RULES",
    "PROMPT_VERSIONS",
    "SCORE_SEMANTIC_RULES",
    "SHARED_CONSTRAINTS",
]

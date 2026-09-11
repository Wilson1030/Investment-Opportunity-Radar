"""守卫层：不变量、禁用词、证据闸门（docs/03 §6.3 / docs/04 §6）。

**这是「Every Claim Needs Evidence」（规格原则 4）在代码里的具体实现，不是文档口号。**

闸门四道校验（全部通过才允许落库为 Evidence）::

    1. 段落存在      (announcement_id, page, para_index) 必须在 Paragraph 表里
    2. 文本一致      relevant_text 必须是 Paragraph.text 的子串（归一化空白后）
    3. 证据已存在    若引用 evidence_id，该 ID 必须已存在
    4. 来源等级      由规则按 source_type 赋值，**不接受 LLM 自述**

某事件的证据片全部被拒 → **该事件不落库**（它没有证据）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from app.models.enums import (
    ALLOWED_STATUS_TRANSITIONS,
    OpportunityStatus,
    ReliabilityLevel,
    SourceType,
)

# --------------------------------------------------------------------------- #
# 1. 禁用词（规格 §39 / M11-02）
# --------------------------------------------------------------------------- #
BANNED_WORDS: tuple[str, ...] = (
    "一定会上涨",
    "必然上涨",
    "一定会涨",
    "必涨",
    "稳赚",
    "保证收益",
    "翻倍",
    "目标价",
    "值得买",
    "可以买",
    "建议买入",
    "建议卖出",
    "一定成功",
    "确定性的机会",
    "包赚",
    "无风险套利",
)

#: 允许的表述（供 prompt 与人工审查参考）
ALLOWED_PHRASINGS: tuple[str, ...] = (
    "存在……",
    "可能……",
    "目前证据支持……",
    "与该投资 Thesis 高度匹配……",
    "值得进一步确认……",
    "当前仍存在……",
    "若后续出现 X，则该 Thesis 将得到强化……",
    "若出现 Y，则该 Thesis 可能失效……",
)


@dataclass(frozen=True)
class BannedWordReport:
    hit: bool
    words: tuple[str, ...] = ()
    excerpts: tuple[str, ...] = ()


def find_banned_words(text: str | None) -> BannedWordReport:
    if not text:
        return BannedWordReport(hit=False)
    hits = tuple(w for w in BANNED_WORDS if w in text)
    excerpts: list[str] = []
    for w in hits:
        idx = text.find(w)
        start = max(0, idx - 20)
        excerpts.append(text[start : idx + len(w) + 20])
    return BannedWordReport(hit=bool(hits), words=hits, excerpts=tuple(excerpts))


# --------------------------------------------------------------------------- #
# 2. 文本归一化与子串校验（INV-E1）
# --------------------------------------------------------------------------- #
_WS = re.compile(r"[\s\u3000]+")


def normalize_whitespace(text: str) -> str:
    """归一化空白（全角空格、换行、连续空格）。

    PDF 解析出来的段落常带不规则空白，直接做子串比较会误判，
    但**不允许**做任何字符级模糊匹配 —— 那会让「改写原文」逃过校验。
    """
    return _WS.sub("", text or "")


def relevance_substring_match(relevant_text: str, paragraph_text: str) -> bool:
    """``relevant_text`` 是否确为原文片段（INV-E1）。"""
    if not relevant_text or not paragraph_text:
        return False
    return normalize_whitespace(relevant_text) in normalize_whitespace(paragraph_text)


# --------------------------------------------------------------------------- #
# 3. 证据闸门
# --------------------------------------------------------------------------- #
MIN_RELEVANT_LEN = 10

#: 来源类型 → 证据等级（**由规则赋值，不接受 LLM 自述**）
SOURCE_RELIABILITY: dict[SourceType, ReliabilityLevel] = {
    SourceType.ANNOUNCEMENT: ReliabilityLevel.A,
    SourceType.POLICY: ReliabilityLevel.A,
    SourceType.FINANCIAL_REPORT: ReliabilityLevel.B,
    SourceType.NEWS: ReliabilityLevel.C,
    SourceType.PRICE: ReliabilityLevel.D,
    SourceType.OTHER: ReliabilityLevel.E,
}


@dataclass(frozen=True)
class EvidenceSlice:
    """LLM 返回的一条证据片段。"""

    page: int
    para_index: int
    relevant_text: str
    evidence_id: int | None = None


@dataclass(frozen=True)
class GateDecision:
    accepted: tuple[EvidenceSlice, ...] = ()
    rejected: tuple[tuple[EvidenceSlice, str], ...] = ()

    @property
    def all_rejected(self) -> bool:
        return not self.accepted

    @property
    def rejection_reasons(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, reason in self.rejected:
            key = reason.split("：", 1)[0]
            counts[key] = counts.get(key, 0) + 1
        return counts


def gate_evidence_slices(
    slices: Iterable[EvidenceSlice],
    paragraph_lookup: Callable[[int, int], str | None],
    *,
    existing_evidence_ids: frozenset[int] | None = None,
    min_len: int = MIN_RELEVANT_LEN,
) -> GateDecision:
    """对 LLM 返回的证据片段执行四道校验。

    参数
    ----
    paragraph_lookup
        ``(page, para_index) -> paragraph_text | None``，由调用方提供（不碰 DB 的接口）。
    existing_evidence_ids
        已存在的 Evidence ID 集合（用于校验 LLM 引用的 ID 是否真实）。
    """
    accepted: list[EvidenceSlice] = []
    rejected: list[tuple[EvidenceSlice, str]] = []
    known_ids = existing_evidence_ids or frozenset()

    for item in slices:
        text = (item.relevant_text or "").strip()

        if len(text) < min_len:
            rejected.append((item, f"片段过短：长度 {len(text)} < {min_len}"))
            continue

        paragraph_text = paragraph_lookup(item.page, item.para_index)
        if paragraph_text is None:
            rejected.append((
                item,
                f"段落不存在：page={item.page} para_index={item.para_index}",
            ))
            continue

        if not relevance_substring_match(text, paragraph_text):
            rejected.append((
                item,
                "文本不一致：relevant_text 不是原文片段（疑似改写原文）",
            ))
            continue

        if item.evidence_id is not None and item.evidence_id not in known_ids:
            rejected.append((item, f"证据 ID 不存在：{item.evidence_id}"))
            continue

        accepted.append(item)

    return GateDecision(accepted=tuple(accepted), rejected=tuple(rejected))


def reliability_for_source(source_type: SourceType) -> ReliabilityLevel:
    """按来源类型赋值证据等级 —— 不信任 LLM 自述的等级（docs/04 §6）。"""
    return SOURCE_RELIABILITY[SourceType(source_type)]


# --------------------------------------------------------------------------- #
# 4. 不变量校验（docs/02 §5）
# --------------------------------------------------------------------------- #
class InvariantViolation(AssertionError):
    """违反领域不变量。"""


def check_event_times(event_time, discovery_time) -> None:
    """INV-EV1：``event_time <= discovery_time``（规格 §40）。"""
    if event_time is not None and discovery_time is not None and event_time > discovery_time:
        raise InvariantViolation(
            f"INV-EV1 违反：event_time({event_time}) 晚于 discovery_time({discovery_time})"
        )


def check_announcement_evidence(source_type: SourceType, announcement_id: int | None) -> None:
    """INV-E3：公告类证据必须有 ``announcement_id``。"""
    if SourceType(source_type) is SourceType.ANNOUNCEMENT and not announcement_id:
        raise InvariantViolation("INV-E3 违反：公告类证据缺少 announcement_id")


def check_thesis_invalidation(invalidating_event_types: Iterable) -> None:
    """INV-TT1：每个 Thesis 必须定义失效条件（规格 §58 原则 5）。"""
    if not tuple(invalidating_event_types):
        raise InvariantViolation("INV-TT1 违反：Thesis 的失效条件为空")


def check_soft_evidence_confirmation(
    levels: Iterable[ReliabilityLevel], target_status: OpportunityStatus
) -> None:
    """INV-E2：C/D/E 类证据不得单独支撑 ``thesis_confirmed``（规格 §16 / M4-04）。"""
    levels = tuple(ReliabilityLevel(lv) for lv in levels)
    if OpportunityStatus(target_status) is not OpportunityStatus.THESIS_CONFIRMED:
        return
    if not any(lv in (ReliabilityLevel.A, ReliabilityLevel.B) for lv in levels):
        raise InvariantViolation(
            "INV-E2 违反：无 A/B 类证据却试图进入 thesis_confirmed"
            "（只有市场讨论不能确认投资逻辑）"
        )


def check_status_transition(
    from_status: OpportunityStatus | None, to_status: OpportunityStatus
) -> None:
    """状态机合法迁移校验（M7-01）。非法迁移抛 :class:`InvariantViolation`。"""
    if from_status is None:
        return
    from_status = OpportunityStatus(from_status)
    to_status = OpportunityStatus(to_status)
    allowed = ALLOWED_STATUS_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise InvariantViolation(
            f"非法状态迁移：{from_status.value} → {to_status.value}；"
            f"允许的目标状态：{sorted(s.value for s in allowed)}"
        )


def check_weight_total(weights: dict, expected: float = 0.95, tolerance: float = 1e-9) -> None:
    """权重合计校验（docs/04 §2）。"""
    total = sum(weights.values())
    if abs(total - expected) > tolerance:
        raise InvariantViolation(f"权重合计 {total:.6f} ≠ {expected}")


# --------------------------------------------------------------------------- #
# 5. 源码级约束（供测试使用）
# --------------------------------------------------------------------------- #
#: 禁止在规则层出现的「标签 → 策略」直接映射（规格 §5.6 示例 J / INV-C1）
FORBIDDEN_TAG_MAPPING_PATTERNS: tuple[str, ...] = (
    "match_by_tag",
    "thesis_by_tag",
    "strategy_for_tag",
    "MAP_TAG_TO_THESIS",
    "TAG_TO_THESIS",
)

#: 需要扫描的规则层源码相对路径
RULE_SOURCE_GLOBS: tuple[str, ...] = (
    "app/strategies/*/rules.py",
    "app/engine/rules.py",
    "app/engine/classifier.py",
)


@dataclass
class SourceScanReport:
    violations: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


__all__ = [
    "ALLOWED_PHRASINGS",
    "BANNED_WORDS",
    "FORBIDDEN_TAG_MAPPING_PATTERNS",
    "MIN_RELEVANT_LEN",
    "RULE_SOURCE_GLOBS",
    "SOURCE_RELIABILITY",
    "BannedWordReport",
    "EvidenceSlice",
    "GateDecision",
    "InvariantViolation",
    "SourceScanReport",
    "check_announcement_evidence",
    "check_event_times",
    "check_soft_evidence_confirmation",
    "check_status_transition",
    "check_thesis_invalidation",
    "check_weight_total",
    "find_banned_words",
    "gate_evidence_slices",
    "normalize_whitespace",
    "reliability_for_source",
    "relevance_substring_match",
]

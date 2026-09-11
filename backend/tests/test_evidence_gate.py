"""证据闸门（docs/03 §6.3）—— 防幻觉的核心机制。

**这不是「在文档里写一句要小心」**，而是四道可执行校验。任何一条不通过，
该证据就不得落库；若某事件的证据全部被拒，**该事件不落库（它没有证据）**。
"""

from __future__ import annotations

from app.engine.guard import (
    EvidenceSlice,
    gate_evidence_slices,
    reliability_for_source,
    relevance_substring_match,
)
from app.models.enums import ReliabilityLevel, SourceType

PARAGRAPH_7 = "……公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份……"
PARAGRAPH_8 = "……公司拟以发行股份方式购买 XX 资产，交易对价尚未确定……"


def _lookup(page: int, para_index: int) -> str | None:
    return {(2, 7): PARAGRAPH_7, (2, 8): PARAGRAPH_8}.get((page, para_index))


def test_accepts_faithful_slice():
    decision = gate_evidence_slices(
        [EvidenceSlice(page=2, para_index=7,
                       relevant_text="公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份")],
        _lookup,
    )
    assert len(decision.accepted) == 1
    assert not decision.rejected
    assert not decision.all_rejected


def test_rejects_missing_paragraph():
    decision = gate_evidence_slices(
        [EvidenceSlice(page=9, para_index=99, relevant_text="这是一段足够长的文字内容")],
        _lookup,
    )
    assert decision.all_rejected
    reasons = dict(decision.rejection_reasons)
    assert reasons.get("段落不存在") == 1


def test_rejects_rewritten_text():
    """★ 最危险的信号：模型在改写原文，产生「看似合理但无法核对」的证据。"""
    decision = gate_evidence_slices(
        [EvidenceSlice(page=2, para_index=7,
                       relevant_text="控股股东打算把全部股份卖给某集团公司")],
        _lookup,
    )
    assert decision.all_rejected
    assert dict(decision.rejection_reasons).get("文本不一致") == 1


def test_rejects_too_short_slice():
    decision = gate_evidence_slices(
        [EvidenceSlice(page=2, para_index=7, relevant_text="控股股东")],
        _lookup,
    )
    assert decision.all_rejected
    assert dict(decision.rejection_reasons).get("片段过短") == 1


def test_rejects_unknown_evidence_id():
    decision = gate_evidence_slices(
        [EvidenceSlice(page=2, para_index=7,
                       relevant_text="公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份",
                       evidence_id=99999)],
        _lookup,
        existing_evidence_ids=frozenset({1, 2, 3}),
    )
    assert decision.all_rejected
    assert dict(decision.rejection_reasons).get("证据 ID 不存在") == 1


def test_accepts_known_evidence_id():
    decision = gate_evidence_slices(
        [EvidenceSlice(page=2, para_index=8,
                       relevant_text="公司拟以发行股份方式购买 XX 资产，交易对价尚未确定",
                       evidence_id=42)],
        _lookup,
        existing_evidence_ids=frozenset({42}),
    )
    assert len(decision.accepted) == 1


def test_mixed_slices_keep_only_valid_ones():
    decision = gate_evidence_slices(
        [
            EvidenceSlice(page=2, para_index=7,
                          relevant_text="公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份"),
            EvidenceSlice(page=2, para_index=8, relevant_text="模型自己编的一段话"),
            EvidenceSlice(page=1, para_index=1, relevant_text="页码不存在"),
        ],
        _lookup,
    )
    assert len(decision.accepted) == 1
    assert len(decision.rejected) == 2
    assert not decision.all_rejected


def test_whitespace_normalization_tolerates_pdf_artifacts():
    """PDF 解析常带不规则空白 —— 归一化后比较，但不做字符级模糊匹配。"""
    assert relevance_substring_match(
        "公司控股股东  拟以协议转让\n方式向 XX 集团", PARAGRAPH_7
    )
    assert not relevance_substring_match("公司控股股东拟以协议转让方式向 YY 集团", PARAGRAPH_7)


def test_reliability_is_assigned_by_rules_not_by_model():
    """来源等级由规则按 source_type 赋值，不接受 LLM 自述（docs/04 §6）。"""
    assert reliability_for_source(SourceType.ANNOUNCEMENT) is ReliabilityLevel.A
    assert reliability_for_source(SourceType.POLICY) is ReliabilityLevel.A
    assert reliability_for_source(SourceType.FINANCIAL_REPORT) is ReliabilityLevel.B
    assert reliability_for_source(SourceType.NEWS) is ReliabilityLevel.C
    assert reliability_for_source(SourceType.OTHER) is ReliabilityLevel.E


def test_gate_against_real_db_paragraphs(paragraph_lookup):
    """与真实落库的段落比对（保证 (page, para_index) 语义一致）。"""
    decision = gate_evidence_slices(
        [EvidenceSlice(page=2, para_index=7,
                       relevant_text="公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份")],
        paragraph_lookup,
    )
    assert len(decision.accepted) == 1
    # 同一段落里不存在的说法必须被拒
    decision_bad = gate_evidence_slices(
        [EvidenceSlice(page=2, para_index=7, relevant_text="公司已完成资产交割")],
        paragraph_lookup,
    )
    assert decision_bad.all_rejected

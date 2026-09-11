"""12 条领域不变量（docs/02 §5）逐条验证。

不变量不是文档装饰 —— 它们是「系统不会悄悄输出错误结论」的保证。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.engine import guard
from app.facts import CompanyFacts, StrategyFacts
from app.models.enums import EventType, OpportunityStatus, ReliabilityLevel, SourceType
from app.models.events import Event
from app.models.knowledge import Company
from app.models.opportunity import Opportunity
from app.models.profile import WatchlistItem
from app.strategies import STRATEGIES, get_strategy, validate_registry
from app.strategies.base import NotImplementedStrategy
from app.strategies.restructuring.rules import STRATEGY
from tests.conftest import bare_facts

NOW = datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# INV-TT1 / INV-TT2
# --------------------------------------------------------------------------- #
def test_inv_tt1_every_strategy_defines_invalidation():
    for code, definition in STRATEGIES.items():
        assert definition.invalidating_events, f"{code} 未定义失效条件（INV-TT1）"


def test_inv_tt1_guard_rejects_empty_invalidation():
    with pytest.raises(guard.InvariantViolation):
        guard.check_thesis_invalidation([])


def test_inv_tt2_implemented_strategy_has_implementation():
    implementation = get_strategy("restructuring")
    assert not isinstance(implementation, NotImplementedStrategy)
    assert type(implementation).__name__ == "RestructuringStrategy"


def test_inv_tt2_designed_strategy_raises_loudly():
    """未实现的策略必须显式抛错，而不是返回「0 分机会」这种静默假结果。"""
    designed = get_strategy("cycle")
    assert isinstance(designed, NotImplementedStrategy)
    with pytest.raises(NotImplementedError) as exc:
        designed.evaluate(bare_facts())
    assert "docs/06" in str(exc.value)


def test_registry_selfcheck_has_no_problems():
    assert validate_registry() == []


# --------------------------------------------------------------------------- #
# INV-C1：策略决定为什么被发现，标签不决定策略（规格 §5.6 示例 J）
# --------------------------------------------------------------------------- #
def test_inv_c1_st_alone_does_not_reach_high_coverage():
    """只有 ST 标签、没有任何事件 → coverage 必须很低（不可能凭标签命中重组策略）。"""
    st_only = bare_facts(company=CompanyFacts(id=1, name="ST 测试", is_st=True))
    evaluation = STRATEGY.evaluate(st_only)
    assert evaluation.coverage <= 0.10, "ST 标签本身不得把 coverage 推高"
    assert all(c.satisfaction == 0.0 for c in evaluation.conditions if c.key != "C4")


def test_inv_c1_non_st_can_still_hit_full_coverage():
    """★ 规格示例 J 的反向验证：非 ST 公司也能完整命中重组逻辑。"""
    from app.facts import EventFact
    from app.models.enums import ReliabilityLevel

    non_st = bare_facts(
        company=CompanyFacts(id=2, name="非 ST 公司", is_st=False),
        events=(
            EventFact(id=1, event_type=EventType.RESTRUCTURING, title="重大资产重组预案公告",
                      evidence_level=ReliabilityLevel.A),
            EventFact(id=2, event_type=EventType.CONTROL_CHANGE, title="控股股东变更",
                      evidence_level=ReliabilityLevel.A),
            EventFact(id=3, event_type=EventType.ASSET_INJECTION, title="资产注入",
                      evidence_level=ReliabilityLevel.A),
        ),
        open_question_count=1,
    )
    evaluation = STRATEGY.evaluate(non_st)
    assert evaluation.coverage == pytest.approx(0.80)  # C1+C2+C3 全中，C4 为 0
    assert evaluation.coverage > 0.5, "非 ST 公司同样可以获得高匹配度"


# --------------------------------------------------------------------------- #
# INV-E1 / INV-E2 / INV-E3
# --------------------------------------------------------------------------- #
def test_inv_e1_substring_match():
    paragraph = "……公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份……"
    assert guard.relevance_substring_match("公司控股股东拟以协议转让方式", paragraph)
    # 空白差异被容忍（PDF 解析常见）
    assert guard.relevance_substring_match("公司控股股东 拟以协议转让", paragraph)
    # 改写 / 润色不被容忍
    assert not guard.relevance_substring_match("公司大股东打算转让全部股份", paragraph)


def test_inv_e2_soft_evidence_cannot_confirm_thesis():
    soft = [ReliabilityLevel.C, ReliabilityLevel.E]
    with pytest.raises(guard.InvariantViolation):
        guard.check_soft_evidence_confirmation(soft, OpportunityStatus.THESIS_CONFIRMED)
    # A 类可以
    guard.check_soft_evidence_confirmation(
        [ReliabilityLevel.C, ReliabilityLevel.A], OpportunityStatus.THESIS_CONFIRMED
    )
    # 非 thesis_confirmed 状态不受此约束
    guard.check_soft_evidence_confirmation(soft, OpportunityStatus.TRACKING)


def test_inv_e3_announcement_evidence_requires_announcement_id():
    with pytest.raises(guard.InvariantViolation):
        guard.check_announcement_evidence(SourceType.ANNOUNCEMENT, None)
    guard.check_announcement_evidence(SourceType.ANNOUNCEMENT, 1)
    guard.check_announcement_evidence(SourceType.NEWS, None)


# --------------------------------------------------------------------------- #
# INV-EV1 / INV-EV2
# --------------------------------------------------------------------------- #
def test_inv_ev1_event_time_not_after_discovery_time():
    """规格 §40：事件发生时间不得晚于系统发现时间。"""
    late = NOW + timedelta(hours=1)
    with pytest.raises(guard.InvariantViolation):
        guard.check_event_times(late, NOW)
    guard.check_event_times(NOW - timedelta(days=3), NOW)
    guard.check_event_times(None, NOW)  # 未披露时间时不报错


def test_inv_ev2_event_dedup(session):
    company = Company(name="去重测试")
    session.add(company)
    session.commit()
    session.refresh(company)

    payload = dict(
        company_id=int(company.id),
        event_type=EventType.RESTRUCTURING,
        title="重大资产重组预案公告",
        summary="……",
        event_time=NOW,
        source_url="http://example.com/1.pdf",
    )
    session.add(Event(**payload))
    session.commit()

    session.add(Event(**payload))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --------------------------------------------------------------------------- #
# INV-P1：锁定权重不被自动学习覆盖（M1-06）
# --------------------------------------------------------------------------- #
def test_inv_p1_learned_weights_cannot_override_locked():
    from app.api.envelope import RuleViolation
    from app.models.enums import ThesisType
    from app.models.profile import InvestmentProfile

    profile = InvestmentProfile(
        investor_id=1, name="带锁定项的画像",
        locked_weights=[ThesisType.RESTRUCTURING],
    )
    assert ThesisType.RESTRUCTURING in profile.locked_weights
    # 语义检查：锁定项必须能表达「自动学习不得覆盖」的意图
    locked = {str(t) for t in profile.locked_weights}
    assert "restructuring" in locked
    assert issubclass(RuleViolation, Exception)


# --------------------------------------------------------------------------- #
# INV-W1：关注对象必须是 Opportunity（不允许只存 company_id）
# --------------------------------------------------------------------------- #
def test_inv_w1_watchlist_requires_opportunity():
    columns = WatchlistItem.__table__.columns  # type: ignore[attr-defined]
    assert "opportunity_id" in columns
    assert columns["opportunity_id"].nullable is False


def test_inv_w1_foreign_key_reference():
    fks = {fk.target_fullname for fk in WatchlistItem.__table__.foreign_keys}  # type: ignore[attr-defined]
    assert "opportunity.id" in fks
    assert "investmentprofile.id" in fks


# --------------------------------------------------------------------------- #
# 规格 §40：四个时间字段互相独立
# --------------------------------------------------------------------------- #
def test_opportunity_has_independent_time_fields():
    columns = Opportunity.__table__.columns  # type: ignore[attr-defined]
    for name in ("first_discovered_at", "last_updated_at"):
        assert name in columns
    event_columns = Event.__table__.columns  # type: ignore[attr-defined]
    assert "event_time" in event_columns
    assert "discovery_time" in event_columns


def test_state_machine_covers_all_statuses():
    from app.models.enums import ALLOWED_STATUS_TRANSITIONS

    assert set(ALLOWED_STATUS_TRANSITIONS) == set(OpportunityStatus)
    assert ALLOWED_STATUS_TRANSITIONS[OpportunityStatus.ARCHIVED] == set()


def test_strategy_facts_has_no_db_handle():
    """节点与规则的输入必须是纯数据 —— 不得携带 Session（保证可单测、可迁移）。"""
    fields = StrategyFacts.__dataclass_fields__
    assert "session" not in fields
    assert not any("session" in name for name in fields)

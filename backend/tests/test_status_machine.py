"""机会生命周期状态机（M7-01 / INV-E2）。

规格 §20 的状态流：``发现 → 待确认 → 重点跟踪 → 逻辑成立 → 观察 → 逻辑失效 → 归档``。
非法迁移必须被拒绝（API 层返回 409），且 **INV-E2**：只有市场讨论不能确认投资逻辑。
"""

from __future__ import annotations

import pytest

from app.engine import guard
from app.models.enums import ALLOWED_STATUS_TRANSITIONS, OpportunityStatus


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        (OpportunityStatus.DISCOVERED, OpportunityStatus.PENDING_CONFIRMATION),
        (OpportunityStatus.PENDING_CONFIRMATION, OpportunityStatus.TRACKING),
        (OpportunityStatus.TRACKING, OpportunityStatus.THESIS_CONFIRMED),
        (OpportunityStatus.THESIS_CONFIRMED, OpportunityStatus.OBSERVING),
        (OpportunityStatus.OBSERVING, OpportunityStatus.INVALIDATED),
        (OpportunityStatus.INVALIDATED, OpportunityStatus.ARCHIVED),
        (OpportunityStatus.DISCOVERED, OpportunityStatus.ARCHIVED),
    ],
)
def test_legal_transitions(from_status, to_status):
    guard.check_status_transition(from_status, to_status)


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        (OpportunityStatus.DISCOVERED, OpportunityStatus.THESIS_CONFIRMED),
        (OpportunityStatus.DISCOVERED, OpportunityStatus.TRACKING),
        (OpportunityStatus.ARCHIVED, OpportunityStatus.TRACKING),
        (OpportunityStatus.ARCHIVED, OpportunityStatus.DISCOVERED),
        (OpportunityStatus.INVALIDATED, OpportunityStatus.TRACKING),
        (OpportunityStatus.THESIS_CONFIRMED, OpportunityStatus.PENDING_CONFIRMATION),
    ],
)
def test_illegal_transitions_raise(from_status, to_status):
    with pytest.raises(guard.InvariantViolation):
        guard.check_status_transition(from_status, to_status)


def test_no_status_is_unreachable():
    """每个非初始状态都必须能从某处到达（避免定义了一个永远进不去的状态）。"""
    reachable = {OpportunityStatus.DISCOVERED}
    changed = True
    while changed:
        changed = False
        for source, targets in ALLOWED_STATUS_TRANSITIONS.items():
            if source in reachable:
                for target in targets:
                    if target not in reachable:
                        reachable.add(target)
                        changed = True
    assert reachable == set(OpportunityStatus)


def test_archived_is_terminal():
    assert ALLOWED_STATUS_TRANSITIONS[OpportunityStatus.ARCHIVED] == set()
    with pytest.raises(guard.InvariantViolation):
        guard.check_status_transition(OpportunityStatus.ARCHIVED, OpportunityStatus.OBSERVING)


# --------------------------------------------------------------------------- #
# 失效检测（规格 §22 / §23）
# --------------------------------------------------------------------------- #
def test_invalidation_detects_termination_event():
    """「重组终止」必须能触发失效检测 —— 这是「逻辑失效」状态的唯一依据。"""
    from app.facts import EventFact, StrategyFacts
    from app.models.enums import EventType, InvalidationSeverity
    from app.strategies.restructuring import invalidation

    facts = StrategyFacts(
        company=__import__("app.facts", fromlist=["CompanyFacts"]).CompanyFacts(id=1),
        events=(
            EventFact(id=99, event_type=EventType.RESTRUCTURING,
                      title="关于终止重大资产重组的公告"),
        ),
    )
    hits = invalidation.detect(facts)
    assert hits, "终止公告必须命中失效条件"
    assert hits[0].severity == InvalidationSeverity.TERMINAL.value
    assert invalidation.should_invalidate(hits)
    assert invalidation.is_terminal(hits)


def test_invalidation_does_not_fire_on_progress_event():
    from app.facts import CompanyFacts, EventFact, StrategyFacts
    from app.models.enums import EventType
    from app.strategies.restructuring import invalidation

    facts = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(
            EventFact(id=1, event_type=EventType.RESTRUCTURING,
                      title="重大资产重组进展公告"),
        ),
    )
    assert invalidation.detect(facts) == ()


def test_invalidation_requires_keyword_for_severity():
    """同一事件类型下，只有标题含失效关键词才算失效（避免误报）。"""
    from app.facts import CompanyFacts, EventFact, StrategyFacts
    from app.models.enums import EventType
    from app.strategies.restructuring import invalidation

    progress = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(EventFact(id=1, event_type=EventType.CONTROL_CHANGE, title="控股股东变更完成"),),
    )
    cancelled = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(EventFact(id=2, event_type=EventType.CONTROL_CHANGE, title="控股股东变更取消"),),
    )
    assert invalidation.detect(progress) == ()
    assert invalidation.detect(cancelled)


def test_invalidation_amount_threshold():
    """诉讼类失效条件按金额占比判定，而不是按标题关键词。"""
    from app.facts import CompanyFacts, EventFact, StrategyFacts
    from app.models.enums import EventType
    from app.strategies.restructuring import invalidation

    small = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(EventFact(id=1, event_type=EventType.LITIGATION, title="诉讼进展",
                          amount_ratio=0.10),),
    )
    large = StrategyFacts(
        company=CompanyFacts(id=1),
        events=(EventFact(id=2, event_type=EventType.LITIGATION, title="诉讼进展",
                          amount_ratio=0.55),),
    )
    assert invalidation.detect(small) == ()
    assert invalidation.detect(large)


def test_invalidation_hit_feeds_risk_score():
    """失效事件必须让风险分上升、规则分下降（评分与状态一致）。"""
    from dataclasses import replace

    from app.engine.scoring import compute_rule_score
    from app.strategies.restructuring.rules import STRATEGY
    from tests.conftest import canonical_facts

    facts = canonical_facts()
    hits = STRATEGY.invalidation_hits(facts)
    assert hits == ()

    terminated = replace(
        facts,
        events=facts.events + (
            __import__("app.facts", fromlist=["EventFact"]).EventFact(
                id=999, event_type=__import__("app.models.enums", fromlist=["EventType"]).EventType.RESTRUCTURING,
                title="关于终止重大资产重组的公告",
            ),
        ),
    )
    terminated_hits = STRATEGY.invalidation_hits(terminated)
    assert terminated_hits

    base = compute_rule_score(facts, "restructuring", invalidation_hits=hits)
    broken = compute_rule_score(terminated, "restructuring", invalidation_hits=terminated_hits)
    assert broken.risk_score > base.risk_score
    assert broken.rule_score < base.rule_score


# --------------------------------------------------------------------------- #
# API 层
# --------------------------------------------------------------------------- #
def test_api_rejects_illegal_transition_with_409(client, seeded):
    response = client.patch(
        f"/api/opportunities/{seeded['opportunity_id']}/status",
        json={"to_status": "thesis_confirmed", "reason": "尝试跳级"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "INVALID_STATUS_TRANSITION"
    assert "allowed" in body["error"]["detail"]


def test_api_allows_legal_transition(client, seeded):
    response = client.patch(
        f"/api/opportunities/{seeded['opportunity_id']}/status",
        json={"to_status": "tracking", "reason": "用户加入跟踪"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["to_status"] == "tracking"


def test_api_blocks_soft_evidence_only_confirmation(client, seeded, session):
    """INV-E2 在 API 层的兑现：只有 C/E 类证据时不得进入 thesis_confirmed。"""
    from app.models.evidence import Evidence

    evidence = session.get(Evidence, seeded["evidence_id"])
    assert evidence is not None
    evidence.reliability_level = "E"
    session.add(evidence)
    session.commit()

    client.patch(
        f"/api/opportunities/{seeded['opportunity_id']}/status",
        json={"to_status": "tracking", "reason": "先进入跟踪"},
    )
    response = client.patch(
        f"/api/opportunities/{seeded['opportunity_id']}/status",
        json={"to_status": "thesis_confirmed", "reason": "尝试确认"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RULE_VIOLATION"
    assert "INV-E2" in response.json()["error"]["message"]


def test_status_change_is_logged(client, seeded):
    client.patch(
        f"/api/opportunities/{seeded['opportunity_id']}/status",
        json={"to_status": "tracking", "reason": "用户加入跟踪"},
    )
    detail = client.get(f"/api/opportunities/{seeded['opportunity_id']}").json()["data"]
    history = detail["status_history"]
    assert any(entry["to_status"] == "tracking" for entry in history)
    assert any(entry["reason"] == "用户加入跟踪" for entry in history)

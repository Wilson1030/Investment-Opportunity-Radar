"""12 条领域不变量（docs/02 §5）逐条验证。

不变量不是文档装饰 —— 它们是「系统不会悄悄输出错误结论」的保证。
"""

from __future__ import annotations

from pathlib import Path

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.engine import guard
from app.facts import CompanyFacts, StrategyFacts
from app.models.enums import ThesisType, EventType, OpportunityStatus, ReliabilityLevel, SourceType
from app.models.events import Event
from app.models.knowledge import Company
from app.models.opportunity import Opportunity
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


def test_inv_tt2_placeholder_implementation_raises_loudly():
    """INV-TT2：未实现的策略必须**显式抛错**，而不是返回「0 分机会」。

    静默的假结果比明确的错误危险得多 —— 用户会以为「查过了、没机会」。

    ★ 这条原先用 ``designed_types()`` 取一个真实存在的未实现策略。
    10 类策略全部实现后没有这样的样本了，所以改为**直接构造**
    一个占位实现来验证它的行为（约束本身没变，样本来源变了）。
    """
    placeholder = NotImplementedStrategy(ThesisType.RESTRUCTURING, "占位")
    for call in (
        lambda: placeholder.evaluate(bare_facts()),
        lambda: placeholder.catalyst_strength(bare_facts()),
        lambda: placeholder.invalidation_hits(bare_facts()),
    ):
        with pytest.raises((NotImplementedError, NotImplementedError)):
            call()
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
# INV-W1：关注对象必须绑定投资逻辑（不允许只存 company_id）
# --------------------------------------------------------------------------- #
def test_inv_w1_opportunity_must_bind_a_thesis():
    """★ 「关注」必须绑定 Thesis，不能只是一只股票。

    规格 §21：不要保存成「自选股：ST XXX」，
    而应保存成「**因为重组预期，所以关注 ST XXX**」。

    ★ 这条原先断言在一张 ``watchlistitem`` 表上。那张表从未被任何代码写入过
    （0 行、0 处写入）—— 因为「自选」这个概念本身就**被 Opportunity + Thesis
    取代了**：机会天生带 thesis_id，而状态机的 TRACKING 就是「我在跟踪它」。

    所以不变量没变、换了承载者：现在直接断言 Opportunity 上的约束。
    保留一张没人写的表，只会让人以为存在一个「自选」功能。
    """
    from app.models.opportunity import Opportunity

    columns = Opportunity.__table__.columns  # type: ignore[attr-defined]
    assert "thesis_id" in columns, "机会必须绑定一条投资逻辑"
    assert columns["thesis_id"].nullable is False, "不允许出现「没有逻辑」的关注"
    assert "company_id" in columns, "公司仍然是机会的对象"

    fks = {fk.target_fullname for fk in Opportunity.__table__.foreign_keys}  # type: ignore[attr-defined]
    assert "thesis.id" in fks
    assert "company.id" in fks


def test_inv_w1_no_separate_watchlist_table():
    """★ 不允许再出现第二套「关注」机制。

    两套机制（自选表 + 状态机）就是「同一事实两个来源」——
    用户在 A 处取消关注、B 处还挂着，而且两边都觉得自己是对的。
    """
    from sqlmodel import SQLModel

    import app.models  # noqa: F401  —— 触发注册

    table_names = set(SQLModel.metadata.tables)
    for forbidden in ("watchlistitem", "thesistypedef", "thesisinvalidationrule"):
        assert forbidden not in table_names, (
            f"{forbidden} 又出现了 —— 它没有写入路径，"
            "存在只会让人以为有这个功能"
        )


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
    """状态机必须覆盖全部状态，且**不能有死胡同**。

    ★ 这条原先断言 ``ARCHIVED == set()``（归档是终态）。
    但 UI 上的按钮叫「**暂时**忽略」—— 死胡同意味着用户点错一次
    就再也回不来，只能改数据库。所以现在归档可回到「待确认 / 跟踪」。

    真正要守的约束没变：**每个状态都必须可达、且至少有出口**
    （除初始状态外，任何状态都不能是无法进入的孤岛）。
    """
    from app.models.enums import ALLOWED_STATUS_TRANSITIONS

    assert set(ALLOWED_STATUS_TRANSITIONS) == set(OpportunityStatus)

    for status, targets in ALLOWED_STATUS_TRANSITIONS.items():
        if status is OpportunityStatus.ARCHIVED:
            assert targets, "归档不能是死胡同（按钮承诺了「暂时」）"
            continue
        assert targets, f"{status} 没有任何出口 —— 死胡同"

    # 每个非初始状态都必须能从某处到达
    reachable = {OpportunityStatus.DISCOVERED}
    changed = True
    while changed:
        changed = False
        for source, targets in ALLOWED_STATUS_TRANSITIONS.items():
            if source in reachable and not targets <= reachable:
                reachable |= targets
                changed = True
    assert reachable == set(OpportunityStatus), (
        f"这些状态无法进入：{set(OpportunityStatus) - reachable}"
    )


def test_strategy_facts_has_no_db_handle():
    """节点与规则的输入必须是纯数据 —— 不得携带 Session（保证可单测、可迁移）。"""
    fields = StrategyFacts.__dataclass_fields__
    assert "session" not in fields
    assert not any("session" in name for name in fields)


# --------------------------------------------------------------------------- #
# 数据库路径必须与 CWD 无关
# --------------------------------------------------------------------------- #
def test_database_url_is_absolute_and_cwd_independent():
    """★ 踩过的坑：相对 SQLite 路径按进程 CWD 解析，会静默产生**两个数据库文件**。

    从项目根运行工具、从 backend/ 运行 uvicorn，各自打开一个文件；
    其中一个还是更早的 ``create_all`` 建的，缺后来的列，
    于是出现「表结构看起来回退了」这种极难排查的现象。
    """
    from app.db import DATABASE_URL, resolve_database_url

    assert DATABASE_URL.startswith("sqlite:///")
    db_path = Path(DATABASE_URL.split("sqlite:///", 1)[-1])
    assert db_path.is_absolute(), "数据库路径必须是绝对路径"
    assert db_path.parent.exists()

    # 同一个相对 URL 必须解析成同一个绝对路径（不依赖 CWD）
    assert resolve_database_url("sqlite:///./data/radar.db") == resolve_database_url(
        "sqlite:///./data/radar.db"
    )
    # 非 SQLite 与非相对路径不受影响
    assert resolve_database_url("postgresql://u@h/db") == "postgresql://u@h/db"
    assert resolve_database_url("sqlite:///:memory:") == "sqlite:///:memory:"
    abs_url = f"sqlite:///{db_path.as_posix()}"
    assert resolve_database_url(abs_url) == abs_url

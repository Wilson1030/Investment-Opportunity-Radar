"""pytest 全局装置。

**必须在导入任何 app 模块之前设置环境变量** —— ``app.config.settings`` 是
导入时创建的缓存单例，一旦先导入就改不动了。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DB = BACKEND_DIR / "data" / "cache" / "test_radar.db"
TEST_DB.parent.mkdir(parents=True, exist_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["LLM_MODE"] = "dev"
os.environ["INGEST_DRY_RUN"] = "true"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["STORE_ANNOUNCEMENT_FULLTEXT"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, select  # noqa: E402

import app.models  # noqa: F401,E402  —— 注册全部实体
from app.facts import (  # noqa: E402
    CompanyFacts,
    EventFact,
    FinancialFacts,
    MarketFacts,
    ShareholderFacts,
    StrategyFacts,
)
from app.models.enums import (  # noqa: E402
    AssertionKind,
    EventType,
    OpportunityStatus,
    ParseStatus,
    ReliabilityLevel,
    ScoreDimension,
    ScoreSource,
    SourceType,
    ThesisType,
)
from app.models.evidence import Evidence  # noqa: E402
from app.models.events import Event  # noqa: E402
from app.models.knowledge import Announcement, Company, Paragraph, Stock  # noqa: E402
from app.models.opportunity import (  # noqa: E402
    OpenQuestion,
    Opportunity,
    OpportunityScore,
    ScoreItem,
)
from app.models.profile import InvestmentProfile, Investor, ProfileThesisWeight  # noqa: E402
from app.models.thesis import Thesis  # noqa: E402

T0 = datetime(2026, 9, 8, 11, 32, tzinfo=timezone.utc)


@pytest.fixture(scope="session", autouse=True)
def _fresh_database():
    from app.db import engine as app_engine

    app_engine.dispose()
    for path in (TEST_DB, *(Path(str(TEST_DB) + s) for s in ("-wal", "-shm", "-journal"))):
        try:
            path.unlink(missing_ok=True)
        except PermissionError:  # Windows 上文件可能仍被占用
            pass
    yield
    app_engine.dispose()
    for path in (TEST_DB, *(Path(str(TEST_DB) + s) for s in ("-wal", "-shm", "-journal"))):
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            pass
    shutil.rmtree(BACKEND_DIR / "data" / "cache" / "dry_run", ignore_errors=True)


@pytest.fixture()
def engine():
    from app.db import engine as app_engine

    SQLModel.metadata.drop_all(app_engine)
    SQLModel.metadata.create_all(app_engine)
    return app_engine


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture()
def client(engine):
    from app.db import get_session
    from app.main import app

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 事实构造
# --------------------------------------------------------------------------- #
def canonical_facts() -> StrategyFacts:
    """docs/04 §8 的规范算例（规格 §17 的 ST XXX 卡片）。

    这是**唯一**的规范输入：``test_scoring.py`` 锁定它的输出为
    ``rule_score = 67.5875（显示 68）``、``risk_score = 40.75``、``match_score = 94``。
    """
    return StrategyFacts(
        company=CompanyFacts(id=88, name="ST XXX", code="600xxx", is_st=True),
        events=(
            EventFact(id=1001, event_type=EventType.RESTRUCTURING,
                      title="重大资产重组预案公告", event_time=T0,
                      evidence_level=ReliabilityLevel.A, amount_ratio=0.20,
                      counterparty_known=True, evidence_ids=(1001,)),
            EventFact(id=1002, event_type=EventType.CONTROL_CHANGE,
                      title="关于控股股东变更的公告", event_time=T0,
                      evidence_level=ReliabilityLevel.A, evidence_ids=(1002,)),
            EventFact(id=1004, event_type=EventType.ASSET_INJECTION,
                      title="关于资产注入的进展公告", event_time=T0,
                      evidence_level=ReliabilityLevel.A, evidence_ids=(1004,)),
        ),
        financials=FinancialFacts(loss_years=2, ocf_positive=True),
        shareholder=ShareholderFacts(controlling_shareholder_changed=True),
        market=MarketFacts(abnormal_volatility=True, social_buzz=True),
        open_question_count=6,
        has_unanswered_inquiry=True,
        has_history_failure=True,
        evidence_levels=(ReliabilityLevel.A,) * 3,
        newest_evidence_age_days=0.5,
    )


def bare_facts(**overrides) -> StrategyFacts:
    """最小事实（用于隔离测试单个维度）。"""
    base = StrategyFacts(
        company=CompanyFacts(id=1, name="测试公司", code="000001"),
        evidence_levels=(ReliabilityLevel.A,),
        newest_evidence_age_days=0.5,
    )
    return replace(base, **overrides) if overrides else base


@pytest.fixture()
def facts() -> StrategyFacts:
    return canonical_facts()


# --------------------------------------------------------------------------- #
# 落库种子数据（供 API 契约测试）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def seeded(session):
    """写入一套最小可用的完整数据：公司 → 公告 → 段落 → 证据 → 事件 → Thesis → 机会 → 评分。"""
    company = Company(name="ST XXX", is_st=True, industry="机械设备")
    session.add(company)
    session.commit()
    session.refresh(company)
    company_id = int(company.id or 0)
    session.add(Stock(company_id=company_id, code="600xxx", exchange="SSE", name="ST XXX"))
    session.commit()

    announcement = Announcement(
        company_id=company_id,
        document_id="ANN-2026-0908-001",
        title="重大资产重组预案公告",
        event_type=EventType.RESTRUCTURING,
        publication_time=T0,
        source_url="http://static.cninfo.com.cn/example.pdf",
        fulltext="……公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份……"
                 "……公司拟以发行股份方式购买 XX 资产……",
        parse_status=ParseStatus.OK,
        paragraph_count=2,
    )
    session.add(announcement)
    session.commit()
    session.refresh(announcement)
    announcement_id = int(announcement.id or 0)

    paragraph_1 = Paragraph(
        announcement_id=announcement_id, page=2, para_index=7,
        text="……公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份……",
        char_start=0, char_end=40,
    )
    paragraph_2 = Paragraph(
        announcement_id=announcement_id, page=2, para_index=8,
        text="……公司拟以发行股份方式购买 XX 资产……",
        char_start=40, char_end=70,
    )
    session.add_all([paragraph_1, paragraph_2])
    session.commit()
    session.refresh(paragraph_1)

    evidence = Evidence(
        source_type=SourceType.ANNOUNCEMENT,
        source_name="巨潮资讯",
        source_url="http://static.cninfo.com.cn/example.pdf",
        publication_time=T0,
        reliability_level=ReliabilityLevel.A,
        announcement_id=announcement_id,
        paragraph_id=paragraph_1.id,
        document_id="ANN-2026-0908-001",
        relevant_text="公司控股股东拟以协议转让方式向 XX 集团转让其所持全部股份",
        page=2,
        para_index=7,
        assertion_kind=AssertionKind.FACT,
        extracted_facts=["控股股东拟转让全部股份", "受让方为 XX 集团"],
    )
    session.add(evidence)
    session.commit()
    session.refresh(evidence)
    evidence_id = int(evidence.id or 0)

    event = Event(
        company_id=company_id,
        event_type=EventType.RESTRUCTURING,
        title="重大资产重组预案公告",
        summary="公司披露重组预案，同时控股股东发生变更。",
        event_time=T0,
        importance=0.91,
        certainty=0.88,
        source_type=SourceType.ANNOUNCEMENT,
        source_url="http://static.cninfo.com.cn/example.pdf",
        affected_thesis=[ThesisType.RESTRUCTURING],
        evidence_ids=[evidence_id],
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    thesis = Thesis(
        company_id=company_id,
        thesis_type=ThesisType.RESTRUCTURING,
        statement="公司处于 ST 状态，同时出现重大资产重组及控制权变化，因此存在潜在重组预期。",
        why_now_past="公司长期处于 ST 状态、连续 2 年亏损",
        why_now_recent="近期出现重大资产重组、控制权变化",
        why_now_this_week="最新事件：重大资产重组预案公告",
        why_now_conclusion="因此该公司进入「重组预期」机会池。",
        supporting_evidence_ids=[evidence_id],
        contradictory_evidence_ids=[],
        invalidating_event_types=[EventType.RESTRUCTURING],
    )
    session.add(thesis)
    session.commit()
    session.refresh(thesis)
    thesis_id = int(thesis.id or 0)

    investor = Investor(handle="owner", display_name="Owner")
    session.add(investor)
    session.commit()
    session.refresh(investor)

    profile = InvestmentProfile(investor_id=int(investor.id or 0), name="默认画像")
    session.add(profile)
    session.commit()
    session.refresh(profile)
    profile_id = int(profile.id or 0)

    session.add(ProfileThesisWeight(
        profile_id=profile_id, thesis_type=ThesisType.RESTRUCTURING, weight=0.40,
        source="manual",
    ))
    session.add(ProfileThesisWeight(
        profile_id=profile_id, thesis_type=ThesisType.MA_INTEGRATION, weight=0.25,
        source="manual",
    ))
    session.add(ProfileThesisWeight(
        profile_id=profile_id, thesis_type=ThesisType.TURNAROUND, weight=0.15,
        source="manual",
    ))

    opportunity = Opportunity(
        company_id=company_id,
        profile_id=profile_id,
        thesis_id=thesis_id,
        status=OpportunityStatus.PENDING_CONFIRMATION,
        match_score=94.0,
        rule_score=67.5875,
        risk_score=40.75,
        semantic_score=76.0,
        divergence=8.41,
        summary="该标的高度符合你的重组预期策略。但目前仍处于方案确认阶段，核心资产及交易对价尚未完全明确。",
        why_now=["发布重大资产重组预案公告", "控股股东近期发生变化"],
        supporting_evidence_ids=[evidence_id],
        contradictory_evidence_ids=[],
        uncertainties=["交易标的", "交易价格", "重组方案", "监管审核结果"],
        risks=["重组存在失败可能", "公司基本面较弱"],
        next_events_to_watch=["重组方案公告", "交易所问询回复", "资产评估结果"],
        score_version="rules-1.0+weights-restructuring-1.0",
        first_discovered_at=T0,
        last_updated_at=T0,
    )
    session.add(opportunity)
    session.commit()
    session.refresh(opportunity)
    opportunity_id = int(opportunity.id or 0)

    session.add_all([
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.THESIS_MATCH,
            raw_value=94.0, weight=0.30, weighted_value=28.20, source=ScoreSource.RULE,
        ),
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.EVENT_CATALYST,
            raw_value=90.0, weight=0.25, weighted_value=22.50, source=ScoreSource.RULE,
        ),
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.CATALYST_STRENGTH,
            raw_value=40.0, weight=0.10, weighted_value=4.00, source=ScoreSource.RULE,
        ),
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.CERTAINTY,
            raw_value=45.0, weight=0.10, weighted_value=4.50, source=ScoreSource.RULE,
        ),
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.FUNDAMENTALS,
            raw_value=50.0, weight=0.05, weighted_value=2.50, source=ScoreSource.RULE,
        ),
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.SHAREHOLDER_STRUCTURE,
            raw_value=90.0, weight=0.10, weighted_value=9.00, source=ScoreSource.RULE,
        ),
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.MARKET_ATTENTION,
            raw_value=60.0, weight=0.05, weighted_value=3.00, source=ScoreSource.RULE,
        ),
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.HISTORY_CASE,
            raw_value=0.0, weight=0.00, weighted_value=0.00, source=ScoreSource.RULE,
        ),
        OpportunityScore(
            opportunity_id=opportunity_id, dimension=ScoreDimension.RISK,
            raw_value=40.75, weight=-0.15, weighted_value=-6.1125, source=ScoreSource.RULE,
        ),
    ])
    session.add_all([
        ScoreItem(
            opportunity_id=opportunity_id, dimension=ScoreDimension.EVENT_CATALYST,
            delta=40, reason="存在 A 类公告直接对应核心事件类型",
            rule_id="R-GEN-EV-01", evidence_ids=[evidence_id],
        ),
        ScoreItem(
            opportunity_id=opportunity_id, dimension=ScoreDimension.RISK,
            delta=0.55, reason="事件失败可能性：中（0.55）｜公司历史上有同类事项失败记录",
            rule_id="R-GEN-RK-EVENT_FAILURE", evidence_ids=[],
        ),
    ])
    session.add_all([
        OpenQuestion(opportunity_id=opportunity_id, question="交易标的", status="open"),
        OpenQuestion(opportunity_id=opportunity_id, question="交易价格", status="open"),
        OpenQuestion(opportunity_id=opportunity_id, question="重组方案", status="open"),
        OpenQuestion(opportunity_id=opportunity_id, question="监管审核结果", status="open"),
        OpenQuestion(opportunity_id=opportunity_id, question="重大资产重组预案公告",
                     status="confirmed", confirmed_evidence_id=evidence_id),
    ])
    session.commit()

    return {
        "company_id": company_id,
        "announcement_id": announcement_id,
        "paragraph_id": int(paragraph_1.id or 0),
        "evidence_id": evidence_id,
        "event_id": int(event.id or 0),
        "thesis_id": thesis_id,
        "profile_id": profile_id,
        "opportunity_id": opportunity_id,
        "document_id": "ANN-2026-0908-001",
    }


@pytest.fixture()
def pipeline_outcome(session, engine):
    """跑一次 mock pipeline（session 只借用建表副作用，pipeline 用同一个测试库）。

    放在 conftest 而不是某个测试文件里：test_pipeline_e2e 与
    test_early_signals 都要用同一条端到端结果。
    """
    from app.pipeline.runner import PipelineOptions, run_pipeline

    del session
    # ★ 显式固定画像为「重组猎手」，让这条端到端测试**只测流水线机制**。
    #
    # 踩过的坑：默认画像改成「全景均衡」（覆盖 10 类策略）之后，
    # 同一批 mock 公司命中的策略变多、卡片数从 3 变成 7，
    # 而这里几条测试写死了 3 —— 它们本来是测「链路写全 / 幂等 / 可观测」的，
    # 却因为「实现了几个策略」而失败。机制测试不该耦合策略数量。
    from app.pipeline import profile_seed

    with Session(engine) as s:
        profile = profile_seed.get_or_create_default_profile(s, template=None)
        profile_seed.apply_template(s, profile, "重组猎手")
    return run_pipeline(PipelineOptions(source="mock", stage="full", limit=None))


@pytest.fixture()
def paragraph_lookup(session, seeded):
    """证据闸门用的段落查询闭包（读 DB）。"""

    def lookup(page: int, para_index: int) -> str | None:
        row = session.exec(
            select(Paragraph).where(
                Paragraph.announcement_id == seeded["announcement_id"],
                Paragraph.page == page,
                Paragraph.para_index == para_index,
            )
        ).first()
        return row.text if row else None

    return lookup


__all__ = [
    "BACKEND_DIR",
    "TEST_DB",
    "T0",
    "bare_facts",
    "canonical_facts",
]

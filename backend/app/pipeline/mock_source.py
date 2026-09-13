"""确定性离线数据源 —— 用于在没有网络、没有 LLM 的情况下验证整条链路。

**它不是 LLM，也不假装是 LLM。** 它做两件事：

1. ``seed()`` —— 造出结构真实的数据（公告 + 段落 + 财务 + 事件），写进库
2. ``synthesize_extraction()`` —— 用**规则**从公告标题与段落合成一份
   :class:`ExtractEventOutput`，形状与真实 LLM 输出完全一致

这样 `pipeline` 的「抽取 → 证据闸门 → Event → Thesis → 评分 → 机会卡」全链路
可以**被确定性地回归**（tests/test_pipeline_e2e.py），而真实数据路径仍走 LLM。

三家公司刻意覆盖三种结局::

    ST XXX (600xxx)  重组预案 + 控股权变更 + 资产注入 + 问询函 → 待确认（有卡片）
    公司 J (000jjj)  非 ST 业绩改善 + 剥离亏损子公司          → 不产卡片（门槛挡住）
    ST YYY (000yyy)  重组终止                                 → 逻辑失效 + 提醒
"""

from __future__ import annotations

import json

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.ai.schemas import EvidenceSliceModel, ExtractEventOutput, ExtractedFact
from app.engine import classifier
from app.ingest import parcel
from app.ingest.normalizer import upsert_announcement, upsert_company
from app.models.enums import CertaintyLevel, EventType, ParseStatus, ReliabilityLevel
from app.models.knowledge import Announcement, Company, FinancialMetric, FinancialPeriod, Stock

SOURCE_NAME = "mock"

#: ⚠ 时间戳必须相对于**当前时刻**，不能用硬编码的绝对日期。
#: 原因：时效衰减（docs/04 §5）是拿真实时钟算的（``newest_evidence_age_days``），
#: 若 mock 用固定日期，随着真实时间跨过 1 天/3 天边界，
#: ``EVENT_CATALYST`` 与 ``MARKET_ATTENTION`` 会自己变（实测 75→63.75、40→34），
#: 使回归测试的期望值随日历漂移 —— 这种测试比没有测试更糟。
def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MockAnnouncement:
    company_code: str
    company_name: str
    is_st: bool
    document_id: str
    title: str
    #: 小时数（而不是天数）：时效衰减的分档边界在 1/3/7/30 天，
    #: 若用整天数，处理耗时几秒就会把年龄推过边界（1.0000 → 1.0002 天），
    #: 使期望值在 1.00 与 0.85 之间跳变。用小时并让最新一条远离边界即可稳定。
    hours_ago: int
    text: str


MOCK_ANNOUNCEMENTS: tuple[MockAnnouncement, ...] = (
    MockAnnouncement(
        company_code="600xxx",
        company_name="ST XXX",
        is_st=True,
        document_id="MOCK-600xxx-01",
        title="关于重大资产重组预案暨控股股东变更的公告",
        hours_ago=52,
        text=(
            "证券代码：600xxx  证券简称：ST XXX  公告编号：2026-088\n"
            "关于重大资产重组预案暨控股股东变更的公告\n"
            "本公司及董事会全体成员保证信息披露内容的真实、准确、完整。\n"
            "一、本次交易概述\n"
            "公司拟以发行股份及支付现金方式购买 XX 新能源科技有限公司 100% 股权。"
            "截至本公告日，标的资产的审计、评估工作尚未完成，交易作价尚未最终确定。\n"
            "二、控股股东变更\n"
            "公司控股股东 XX 控股拟以协议转让方式向 YY 产业集团转让其所持公司全部股份，"
            "转让完成后公司实际控制人将发生变更。本次协议转让尚需取得有权国资主管部门批准。\n"
            "三、风险提示\n"
            "本次交易尚需提交公司股东大会审议并经监管机构审核，能否获得批准及最终获批时间均存在不确定性。"
            "公司 2024 年度、2025 年度连续两年亏损，2025 年末归属于上市公司股东的净资产为负。\n"
        ),
    ),
    MockAnnouncement(
        company_code="600xxx",
        company_name="ST XXX",
        is_st=True,
        document_id="MOCK-600xxx-02",
        title="关于收到交易所对重大资产重组事项问询函的公告",
        hours_ago=20,
        text=(
            "证券代码：600xxx  证券简称：ST XXX  公告编号：2026-091\n"
            "关于收到交易所对重大资产重组事项问询函的公告\n"
            "公司于近日收到证券交易所下发的《关于对公司重大资产重组事项的问询函》。\n"
            "问询函要求公司就标的资产估值的合理性、业绩承诺的可实现性以及交易完成后"
            "上市公司控制权稳定性等问题作出书面说明，并于收到问询函之日起十个交易日内对外披露回复。\n"
            "公司将按照问询函要求尽快组织回复并及时履行信息披露义务。\n"
        ),
    ),
    MockAnnouncement(
        company_code="600xxx",
        company_name="ST XXX",
        is_st=True,
        document_id="MOCK-600xxx-03",
        title="关于拟置入资产的进展公告",
        hours_ago=68,
        text=(
            "证券代码：600xxx  证券简称：ST XXX  公告编号：2026-086\n"
            "关于拟置入资产的进展公告\n"
            "公司本次拟置入资产为 XX 新能源科技有限公司 100% 股权，标的公司主营储能电池材料业务。\n"
            "截至目前，标的资产的审计、评估工作正在推进过程中，公司尚未与交易对方签署正式交易协议。\n"
            "本次资产注入事项尚存在不确定性，敬请投资者注意投资风险。\n"
        ),
    ),
    MockAnnouncement(
        company_code="600xxx",
        company_name="ST XXX",
        is_st=True,
        document_id="MOCK-600xxx-04",
        title="关于重大资产重组报告书（草案）的公告",
        hours_ago=6,
        text=(
            "证券代码：600xxx  证券简称：ST XXX  公告编号：2026-093\n"
            "关于重大资产重组报告书（草案）的公告\n"
            "公司本次重大资产重组报告书（草案）已经董事会审议通过，标的资产的审计、评估工作"
            "已基本完成，交易作价初步确定为人民币 12.6 亿元，最终作价以评估报告为准。\n"
            "本次交易尚需提交公司股东大会审议，并经监管机构审核通过后方可实施。\n"
        ),
    ),
    MockAnnouncement(
        company_code="000jjj",
        company_name="公司 J",
        is_st=False,
        document_id="MOCK-000jjj-01",
        title="2026 年半年度业绩预告",
        hours_ago=92,
        text=(
            "证券代码：000jjj  证券简称：公司 J  公告编号：2026-040\n"
            "2026 年半年度业绩预告\n"
            "一、本期业绩预计情况\n"
            "预计 2026 年半年度实现营业收入较上年同期增长约 18%，归属于上市公司股东的净利润"
            "较上年同期亏损收窄约 62%，经营活动产生的现金流量净额由负转正。\n"
            "二、业绩变动原因\n"
            "公司完成对亏损子公司股权的剥离，同时主营产品毛利率连续两个季度改善，"
            "行业需求出现恢复迹象。公司并非 ST 公司，也未筹划重大资产重组事项。\n"
        ),
    ),
    # ---- 早期苗头：让「⚑ 早期」这条链路在真实产出里能被看到 ----
    MockAnnouncement(
        company_code="000zzz",
        company_name="ST ZZZ",
        is_st=True,
        document_id="MOCK-000zzz-01",
        title="关于债权人申请对公司进行重整的公告",
        hours_ago=336,
        text=(
            "证券代码：000zzz  证券简称：ST ZZZ  公告编号：2026-031\n"
            "关于债权人申请对公司进行重整的公告\n"
            "公司于近日收到债权人 XX 商业银行的《重整申请书》，"
            "该债权人以公司不能清偿到期债务且明显缺乏清偿能力为由，"
            "向有管辖权的人民法院申请对公司进行重整。\n"
            "截至本公告日，法院尚未就是否受理该申请作出裁定。\n"
        ),
    ),
    MockAnnouncement(
        company_code="000zzz",
        company_name="ST ZZZ",
        is_st=True,
        document_id="MOCK-000zzz-02",
        title="关于法院裁定受理公司重整申请的公告",
        hours_ago=30,
        text=(
            "证券代码：000zzz  证券简称：ST ZZZ  公告编号：2026-036\n"
            "关于法院裁定受理公司重整申请的公告\n"
            "公司于 2026 年 9 月 10 日收到人民法院送达的《民事裁定书》，"
            "法院裁定受理债权人 XX 商业银行对公司的重整申请，并指定 XX 律师事务所担任管理人。\n"
            "公司股票自本公告披露之日起被实施退市风险警示。\n"
            "公司重整计划尚未制定，债权申报与重整投资人遴选工作尚在推进中，"
            "能否重整成功存在重大不确定性。\n"
        ),
    ),
    MockAnnouncement(
        company_code="000yyy",
        company_name="ST YYY",
        is_st=True,
        document_id="MOCK-000yyy-01",
        title="关于终止重大资产重组的公告",
        hours_ago=10,
        text=(
            "证券代码：000yyy  证券简称：ST YYY  公告编号：2026-055\n"
            "关于终止重大资产重组的公告\n"
            "公司于 2026 年 6 月披露了重大资产重组预案，拟以发行股份方式购买 ZZ 科技 100% 股权。\n"
            "经与交易对方友好协商，鉴于交易各方对交易方案的核心条款未能达成一致意见，"
            "公司决定终止本次重大资产重组事项。\n"
            "公司承诺自本公告披露之日起一个月内不再筹划重大资产重组事项。\n"
        ),
    ),
)

#: 每家公司要造的财务指标（period 顺序即时间顺序）
MOCK_FINANCIALS: dict[str, tuple[tuple[str, dict[str, tuple[float | None, float | None]]], ...]] = {
    "600xxx": (
        ("2025A", {"revenue": (18.4, -0.12), "net_profit": (-2.10, None),
                   "ocf": (0.30, -0.40), "gross_margin": (0.14, -0.03),
                   "debt_ratio": (0.71, 0.06)}),
        ("2026H1", {"revenue": (9.6, 0.08), "net_profit": (-0.95, None),
                    "ocf": (0.55, 0.35), "gross_margin": (0.16, 0.01),
                    "debt_ratio": (0.74, 0.03)}),
    ),
    "000jjj": (
        ("2025A", {"revenue": (30.0, -0.08), "net_profit": (-1.20, None),
                   "ocf": (-0.40, None), "gross_margin": (0.19, -0.02)}),
        ("2026H1", {"revenue": (18.0, 0.18), "net_profit": (-0.46, None),
                    "ocf": (0.80, 1.60), "gross_margin": (0.23, 0.04)}),
    ),
    "000zzz": (
        ("2025A", {"revenue": (12.0, -0.30), "net_profit": (-3.60, None),
                   "ocf": (-1.10, None), "gross_margin": (0.09, -0.06),
                   "debt_ratio": (0.92, 0.11)}),
        ("2026H1", {"revenue": (4.8, -0.34), "net_profit": (-2.20, None),
                    "ocf": (-0.70, None), "gross_margin": (0.07, -0.02),
                    "debt_ratio": (0.98, 0.06)}),
    ),
    "000yyy": (
        ("2026H1", {"revenue": (5.2, -0.22), "net_profit": (-1.80, None),
                    "ocf": (-0.20, None), "gross_margin": (0.11, -0.05)}),
    ),
}


# --------------------------------------------------------------------------- #
# 造数据
# --------------------------------------------------------------------------- #
def seed(session: Session, *, commit: bool = True) -> dict[str, int]:
    """把 mock 公司 / 公告 / 段落 / 财务写进库（幂等）。"""
    created: dict[str, int] = {"companies": 0, "announcements": 0, "periods": 0}

    for item in MOCK_ANNOUNCEMENTS:
        company = upsert_company(session, item.company_code, item.company_name,
                                 is_st=item.is_st)
        company_id = int(company.id or 0)

        parsed = parcel.parse_html(_to_html(item.text))
        outcome = upsert_announcement(
            session,
            company_id,
            _raw(item),
            parsed,
            commit=False,
        )
        if outcome.created:
            created["announcements"] += 1

    for code, periods in MOCK_FINANCIALS.items():
        company = session.exec(
            select(Company).join(Stock, Stock.company_id == Company.id).where(Stock.code == code)  # type: ignore[arg-type]
        ).first()
        if company is None:
            continue
        for period, metrics in periods:
            period_id = _upsert_period(session, int(company.id or 0), period)
            if period_id is None:
                continue
            created["periods"] += 1
            for metric, (value, yoy) in metrics.items():
                existing = session.exec(
                    select(FinancialMetric).where(
                        FinancialMetric.period_id == period_id,
                        FinancialMetric.metric == metric,
                    )
                ).first()
                if existing is not None:
                    continue
                session.add(FinancialMetric(
                    period_id=period_id, metric=metric, value=value, yoy=yoy,
                    is_anomaly=metric == "net_profit" and value is not None and value < 0,
                    anomaly_note=(
                        "亏损主要系计提资产减值与重组相关费用等一次性因素所致"
                        if metric == "net_profit" and code == "600xxx"
                        else None
                    ),
                ))

    created["companies"] = len(session.exec(select(Company)).all())
    if commit:
        session.commit()
    return created


def _to_html(text: str) -> str:
    body = "".join(f"<p>{line.strip()}</p>" for line in text.split("\n") if line.strip())
    return f"<html><body>{body}</body></html>"


def _raw(item: MockAnnouncement):
    from app.ingest.base import RawAnnouncement

    return RawAnnouncement(
        company_code=item.company_code,
        document_id=item.document_id,
        title=item.title,
        announcement_type=None,
        publication_time=_now() - timedelta(hours=item.hours_ago),
        url=f"http://mock.local/{item.document_id}.html",
        source=SOURCE_NAME,
    )


def _upsert_period(session: Session, company_id: int, period: str) -> int | None:
    existing = session.exec(
        select(FinancialPeriod).where(
            FinancialPeriod.company_id == company_id, FinancialPeriod.period == period
        )
    ).first()
    if existing is not None:
        return None  # 已存在则跳过（幂等）
    year = int(period[:4])
    report_type = "annual" if period.endswith("A") else "semi"
    row = FinancialPeriod(
        company_id=company_id,
        period=period,
        period_end=datetime(year, 12 if report_type == "annual" else 6,
                            30 if report_type == "semi" else 31, tzinfo=timezone.utc).date(),
        report_type=report_type,
        published_at=_now() - timedelta(days=30),
    )
    session.add(row)
    session.flush()
    return int(row.id or 0)


# --------------------------------------------------------------------------- #
# 规则合成抽取结果（形状与 LLM 输出一致）
# --------------------------------------------------------------------------- #
_IMPORTANCE: dict[EventType, float] = {
    EventType.RESTRUCTURING: 0.90,
    EventType.ASSET_INJECTION: 0.80,
    EventType.CONTROL_CHANGE: 0.85,
    EventType.REGULATORY_RISK: 0.62,
    EventType.EARNINGS_TURNAROUND: 0.74,
    EventType.M_AND_A: 0.78,
}
_THERAPY_HINTS: dict[EventType, tuple[str, ...]] = {
    EventType.RESTRUCTURING: ("restructuring", "turnaround"),
    EventType.ASSET_INJECTION: ("restructuring",),
    EventType.CONTROL_CHANGE: ("restructuring", "shareholder_action"),
    EventType.REGULATORY_RISK: ("restructuring",),
    EventType.EARNINGS_TURNAROUND: ("turnaround", "growth"),
    EventType.M_AND_A: ("ma_integration", "turnaround"),
}
#: 与事件类型相关的证据关键词（用于挑出真正支撑该事件的段落）
_EVIDENCE_KEYWORDS: dict[EventType, tuple[str, ...]] = {
    EventType.RESTRUCTURING: ("重组", "购买资产", "交易作价", "终止"),
    EventType.ASSET_INJECTION: ("置入资产", "资产注入", "标的资产"),
    EventType.CONTROL_CHANGE: ("控股股东", "实际控制人", "协议转让"),
    EventType.REGULATORY_RISK: ("问询函", "监管函", "异常波动", "风险提示"),
    EventType.EARNINGS_TURNAROUND: ("营业收入", "净利润", "现金流量", "毛利率", "业绩"),
    EventType.M_AND_A: ("购买", "收购", "股权"),
}
_DEFAULT_KEYWORDS = ("公司", "公告")


def synthesize_extraction(announcement: Announcement, paragraphs) -> ExtractEventOutput:
    """用规则从公告合成一份抽取结果（形状与 LLM 输出一致）。"""
    from app.models.enums import ThesisType

    event_type = (
        EventType(announcement.event_type)
        if announcement.event_type
        else classifier.classify_announcement(announcement.title)
    ) or EventType.OTHER

    keywords = _EVIDENCE_KEYWORDS.get(event_type, _DEFAULT_KEYWORDS)
    ranked = sorted(
        paragraphs,
        key=lambda p: (-sum(1 for kw in keywords if kw in p.text), p.para_index),
    )
    picked = [p for p in ranked if sum(1 for kw in keywords if kw in p.text) > 0][:3]
    if not picked:
        picked = [p for p in paragraphs if len(p.text) >= 20][:1]

    slices = [
        EvidenceSliceModel(page=int(p.page), para_index=int(p.para_index),
                           relevant_text=p.text.strip())
        for p in picked
    ]
    facts = [
        ExtractedFact(statement=p.text.strip()[:80], assertion_kind="fact") for p in picked
    ]

    terminated = any(kw in announcement.title for kw in ("终止", "失败", "撤回"))
    importance = _IMPORTANCE.get(event_type, 0.5)
    certainty = 0.55 if terminated else 0.85

    return ExtractEventOutput(
        event_type=event_type,
        title=announcement.title,
        summary="；".join(p.text.strip()[:60] for p in picked[:2]) or announcement.title,
        event_time=None,                      # 公告未写明 → 回退为发布时间并标注来源
        importance=importance,
        certainty=certainty,
        certainty_level=(
            CertaintyLevel.DISCLOSED if event_type is not EventType.REGULATORY_RISK
            else CertaintyLevel.PARTIALLY_DISCLOSED
        ),
        affected_thesis=[
            ThesisType(t) for t in _THERAPY_HINTS.get(event_type, ())
        ],
        extracted_facts=facts,
        evidence_slices=slices,
        not_mentioned=["交易价格", "资产评估结果", "监管审核结果"],
        counterparty_known=any("产业集团" in p.text or "科技" in p.text for p in picked),
        amount_ratio=0.0,
    )


def paragraph_objects(session: Session, announcement_id: int):
    from app.models.knowledge import Paragraph

    return list(
        session.exec(select(Paragraph).where(Paragraph.announcement_id == announcement_id)).all()
    )


def pending_announcements(session: Session, limit: int | None = None):
    """尚未抽取出事件的公告（``event_type`` 命中白名单但仍无对应 Event）。"""
    from app.models.events import Event

    rows = session.exec(
        select(Announcement).where(Announcement.event_type.is_not(None))  # type: ignore[union-attr]
    ).all()
    out = []
    for row in rows:
        exists = session.exec(
            select(Event).where(
                Event.company_id == row.company_id, Event.source_url == row.source_url
            )
        ).first()
        if exists is None:
            out.append(row)
    return out[:limit] if limit else out


__all__ = [
    "MOCK_ANNOUNCEMENTS",
    "MOCK_FINANCIALS",
    "SOURCE_NAME",
    "paragraph_objects",
    "pending_announcements",
    "seed",
    "synthesize_extraction",
]


# --------------------------------------------------------------------------- #
# 确定性 AI 分析响应（让 mock 源能覆盖「含 AI 落库」的完整链路）
# --------------------------------------------------------------------------- #
#: 三个分析节点在 prompt 里的**特征词**（用于分派，不依赖调用顺序）
_NODE_FINGERPRINTS: tuple[tuple[str, str], ...] = (
    ("contradictory_evidence", "hunt_risk"),
    ("semantic_score", "score_semantic"),
    ("next_events_to_watch", "analyze"),
)


def _node_of(prompt: str) -> str:
    for needle, node in _NODE_FINGERPRINTS:
        if needle in prompt:
            return node
    return "analyze"


def analysis_responder():
    """返回一个按 prompt 特征词分派的确定性 responder。

    **为什么需要**：AI 分析阶段接进 pipeline 后，mock 源若仍走真实 provider，
    单测就会去调 Ollama —— 既慢又不可复现（实测直接把测试跑超时）。
    mock 的意义是「整条链路可离线确定性回归」，包括 AI 结果**如何落库**。
    """

    def responder(prompt: str) -> str:
        node = _node_of(prompt)
        if node == "hunt_risk":
            return json.dumps({
                "contradictory_evidence": [{
                    "kind": "regulatory",
                    "description": "交易所已就本次事项发出问询函，回复内容尚未披露",
                    "evidence_ids": [],
                }],
                "risks": ["监管问询尚未回复", "方案存在终止可能"],
                "open_questions": ["交易标的", "监管审核结果"],
                "no_contradiction_statement": (
                    "已检查历史同类事项、监管问询、财务约束、质押与可交易性，"
                    "发现上述 2 项不确定因素"
                ),
                "confidence_assessment": "证据支持度中等，监管问询构成主要不确定性",
            }, ensure_ascii=False)
        if node == "score_semantic":
            return json.dumps({
                # 故意与规则分拉开 6.5 分：验证分歧记录路径（阈值 20 不触发）
                "semantic_score": 72.0,
                "factors": [{
                    "direction": "positive",
                    "factor": "重整投资人为产业方而非纯财务投资人",
                    "rationale": "产业投资人的参与通常意味着业务协同意愿",
                    "evidence_ids": [],
                }],
                "rules_already_covered": [
                    "事件类型命中", "公告数量", "ST 状态", "财务状况", "市场关注度",
                ],
            }, ensure_ascii=False)
        return json.dumps({
            "summary": (
                "公司已披露重整相关公告，事项仍处于推进阶段。"
                "目前交易方案与监管审核结果尚未明确，属于需要持续跟踪的机会。"
            ),
            "why_now": [
                "过去：公司长期经营承压",
                "最近：出现重整类公告",
                "本周：披露最新进展",
                "因此：进入关注池",
            ],
            "uncertainties": ["重整计划能否获批", "债权申报结果"],
            "next_events_to_watch": ["重整计划草案", "法院裁定"],
            "assertion_kinds": {"summary": "inference"},
        }, ensure_ascii=False)

    return responder


__all__ = [
    "MOCK_ANNOUNCEMENTS",
    "MOCK_FINANCIALS",
    "SOURCE_NAME",
    "analysis_responder",
    "paragraph_objects",
    "pending_announcements",
    "seed",
    "synthesize_extraction",
]

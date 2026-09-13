"""真实公告标题驱动的失效闭环 —— 集成测试（P1-2）。

## 为什么要单独一个文件

已有的失效闭环测试（``test_pipeline_e2e.py``）用的是 **mock 源**：
``ST YYY 重组终止 → 逻辑失效 + 提醒``。它证明了「迁移 + 提醒」这条链路通。

但它**证明不了真实标题会走到那条链路上** —— 而实测恰恰在真实标题上翻车：
  · 「控股股东债权人撤回破产重整申请」→ 判死（主体是控股股东）
  · 「停牌直至终止上市、实施换股吸收合并」→ 判死（合并正在**成功**）

所以本文件用**真实公告标题**跑完整链路：
    真实标题 → ``persist_extraction`` 落库（经证据闸门）→ ``build_opportunities``
    → 状态迁移 + 提醒

唯一的构造部分是「把这条真实标题挂到已种子的公司上」——
真实世界里那家公司的重整被驳回时，库里的卡片正应该是这个反应。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.ai.schemas import EvidenceSliceModel, ExtractEventOutput
from app.models.enums import EventType, OpportunityStatus, ThesisType
from app.models.events import Event
from app.models.knowledge import Announcement, Paragraph
from app.models.opportunity import Alert, Opportunity, OpportunityStatusLog
from app.pipeline import event_writer, facts_builder, opportunity_builder
from app.strategies.registry import STRATEGIES

#: 真实标题：*ST长药 2025-12-29 公告。全文确认是公司**自己**的重整申请被驳回
#: （「法院裁定不予受理……对公司的重整申请，决定依法终结公司预重整程序」）。
REAL_INVALIDATING_TITLE = "关于法院裁定不予受理重整申请暨子公司宣告破产的公告"

#: 真实标题：三安光电公告。主体是**控股股东**自己的破产重整，不是上市公司。
REAL_THIRD_PARTY_TITLE = "三安光电股份有限公司关于控股股东债权人撤回破产重整申请的公告"

T0 = datetime(2026, 9, 10, tzinfo=timezone.utc)
SLICE_TEXT = "公司收到法院裁定，决定依法终结公司预重整程序。"


def _write_real_title(session: Session, company_id: int, title: str) -> Event:  # noqa: D401
    """把一条真实标题写成公告 + 证据 + 事件（走 ``persist_extraction``，经证据闸门）。"""
    # document_id 必须唯一：同一个标题可能在同一个测试里被写两次
    # （例如「先制造失效、再让条件重新成立」），用哈希会撞 UNIQUE 约束。
    _write_real_title.seq += 1
    announcement = Announcement(
        company_id=company_id,
        document_id=f"REAL-{_write_real_title.seq:04d}",
        title=title,
        event_type=EventType.BANKRUPTCY_REORGANIZATION,
        publication_time=T0,
        source_url="http://static.cninfo.com.cn/real.pdf",
        fulltext=f"{title}。{SLICE_TEXT}",
        parse_status="ok",
        paragraph_count=1,
    )
    session.add(announcement)
    session.commit()
    session.refresh(announcement)
    announcement_id = int(announcement.id or 0)

    session.add(Paragraph(
        announcement_id=announcement_id, page=1, para_index=1,
        text=SLICE_TEXT, char_start=0, char_end=len(SLICE_TEXT),
    ))
    session.commit()

    extraction = ExtractEventOutput(
        event_type=EventType.BANKRUPTCY_REORGANIZATION,
        title=title,
        summary="法院裁定不予受理重整申请，并终结预重整程序。",
        event_time=T0,
        evidence_slices=[EvidenceSliceModel(
            page=1, para_index=1, relevant_text=SLICE_TEXT,
        )],
    )
    result = event_writer.persist_extraction(session, company_id, announcement, extraction)
    assert result.created, f"事件未落库：{result.skipped_reason}"

    event = session.exec(
        select(Event).where(Event.company_id == company_id, Event.title == title)
    ).first()
    assert event is not None, "事件写入后查不到"
    return event


# 单调递增计数器（在函数定义之后绑定，否则 NameError）
_write_real_title.seq = 0  # type: ignore[attr-defined]


def _rebuild(session: Session, seeded: dict) -> tuple:
    """按真实链路重建机会（与 runner 内部一致：先刷新事实，再生成机会）。"""
    facts = facts_builder.build_strategy_facts(session, seeded["company_id"])
    facts = event_writer.facts_after_write(session, seeded["company_id"], facts)
    _ = facts
    # 注意：第 4 个参数是**画像的策略权重**（{thesis_type: weight}），
    # 不是策略的**维度权重**（{ScoreDimension: weight}）—— 传错会让
    # profile_weight_ratio 变成 0，match_score 归零，机会被门槛直接挡掉。
    from app.pipeline import profile_seed

    weights = profile_seed.profile_weights(session, seeded["profile_id"])
    _ = STRATEGIES  # 保持导入以便未来按策略取默认权重
    return opportunity_builder.build_opportunities(
        session, seeded["company_id"], seeded["profile_id"], weights,
        dry_run=False, commit=True,
    )


def _invalidated(results: tuple) -> bool:
    target = next(r for r in results if r.thesis_type == ThesisType.RESTRUCTURING.value)
    return bool(target.invalidated)


# --------------------------------------------------------------------------- #
# 真实标题 → 必须判死
# --------------------------------------------------------------------------- #
def test_real_rejection_title_invalidates_card_and_raises_alert(session, seeded):
    """真实「不予受理重整申请」应把已有卡片迁到失效，并产生提醒。"""
    before = session.get(Opportunity, seeded["opportunity_id"])
    assert before is not None
    assert before.status == OpportunityStatus.PENDING_CONFIRMATION

    event = _write_real_title(session, seeded["company_id"], REAL_INVALIDATING_TITLE)
    assert event.is_invalidating, "真实驳回标题应被标为失效事件"

    assert _invalidated(_rebuild(session, seeded)), "真实驳回标题未触发失效"

    after = session.get(Opportunity, seeded["opportunity_id"])
    assert after is not None
    assert after.status == OpportunityStatus.INVALIDATED.value, f"状态未迁移：{after.status}"

    alert = session.exec(
        select(Alert).where(Alert.opportunity_id == seeded["opportunity_id"])
    ).first()
    assert alert is not None, "失效必须产生提醒（规格 §23）"


def test_real_third_party_title_does_not_invalidate(session, seeded):
    """★ 反向守卫：主体是控股股东的撤回，**不得**判死上市公司。

    这条正是实测抓到的假阳性 —— 修好之后再也不能回来。
    """
    event = _write_real_title(session, seeded["company_id"], REAL_THIRD_PARTY_TITLE)
    assert not event.is_invalidating, "控股股东的破产司法程序不得标为失效事件"

    assert not _invalidated(_rebuild(session, seeded)), (
        "控股股东自己的重整被撤回，不该判死上市公司"
    )

    after = session.get(Opportunity, seeded["opportunity_id"])
    assert after is not None
    assert after.status != OpportunityStatus.INVALIDATED.value


# --------------------------------------------------------------------------- #
# 门槛不得拦住「已有卡片」的重新评估
# --------------------------------------------------------------------------- #
#: 让 match_score 掉到阈值（30）以下、但 coverage 仍在阈值（0.35）之上的画像权重。
#: match_score = 100 × (w_thesis / w_max) × coverage = 100 × 0.5 × 0.42 ≈ 21 < 30
WEAK_PROFILE_WEIGHTS = {"restructuring": 0.5, "turnaround": 1.0, "event_driven": 1.0}


def _rebuild_with(session, seeded: dict, weights: dict) -> tuple:
    from app.pipeline import profile_seed  # noqa: F401  仅用于对齐真实链路

    facts = facts_builder.build_strategy_facts(session, seeded["company_id"])
    facts = event_writer.facts_after_write(session, seeded["company_id"], facts)
    _ = facts
    return opportunity_builder.build_opportunities(
        session, seeded["company_id"], seeded["profile_id"], weights,
        dry_run=False, commit=True,
    )


def test_existing_card_is_reevaluated_even_below_threshold(session, seeded):
    """★ 已有卡片不得因门槛而被静默跳过。

    ``_build_one`` 是唯一做失效判定与状态迁移的地方。若门槛也拦住已有卡片，
    那么当它的分数掉到阈值下（而逻辑失效本身就会让分数掉下来），
    它就再也不会被重新评估 —— 死掉的苗头永远停在「待确认」。

    这里用「画像不关注重组」构造出低匹配度：match ≈ 21 < 30，coverage 仍 ≥ 0.35。
    """
    before = session.get(Opportunity, seeded["opportunity_id"])
    assert before is not None

    # 先确认这个权重组合确实会让**新卡**被门槛拦下（否则本测试没测到东西）
    fresh = opportunity_builder.build_opportunities(
        session, seeded["company_id"], seeded["profile_id"], WEAK_PROFILE_WEIGHTS,
        dry_run=True, commit=False,
    )
    target = next(r for r in fresh if r.thesis_type == ThesisType.RESTRUCTURING.value)
    assert target.match_score is not None and target.match_score < 30.0, (
        f"构造失败：match_score={target.match_score}，未低于阈值"
    )

    # ① 逻辑**未失效** + 分数低于阈值 → 尊重门槛（画像不关注就不更新）
    results = _rebuild_with(session, seeded, WEAK_PROFILE_WEIGHTS)
    target = next(r for r in results if r.thesis_type == ThesisType.RESTRUCTURING.value)
    assert "未建卡" in (target.reason or ""), (
        f"未失效的卡片应当尊重门槛：{target.reason}"
    )

    # ② 逻辑**已失效** + 同样的低分数 → 必须放行，否则死掉的苗头永远挂着
    _write_real_title(session, seeded["company_id"], REAL_INVALIDATING_TITLE)
    results = _rebuild_with(session, seeded, WEAK_PROFILE_WEIGHTS)
    target = next(r for r in results if r.thesis_type == ThesisType.RESTRUCTURING.value)
    assert "未建卡" not in (target.reason or ""), (
        f"已失效的卡片被门槛挡住了（闭环断裂）：{target.reason}"
    )
    assert target.invalidated, "已失效的卡片必须被判为失效"
    after = session.get(Opportunity, seeded["opportunity_id"])
    assert after is not None
    assert after.status == OpportunityStatus.INVALIDATED.value, (
        f"状态未迁移：{after.status}"
    )


def test_threshold_still_blocks_first_time_creation(session, seeded):
    """反向守卫：门槛对**首次建卡**必须仍然生效（不能为了修闭环把门槛拆了）。"""
    # 造一家全新的公司：没有任何卡片，只有一条低强度事件
    from datetime import datetime, timezone

    from app.models.knowledge import Company

    company = Company(name="门槛测试公司", is_st=True, industry="综合")
    session.add(company)
    session.commit()
    session.refresh(company)
    company_id = int(company.id or 0)

    _write_real_title(session, company_id, REAL_THIRD_PARTY_TITLE)

    results = opportunity_builder.build_opportunities(
        session, company_id, seeded["profile_id"], {"restructuring": 1.0},
        dry_run=True, commit=False,
    )
    target = next(r for r in results if r.thesis_type == ThesisType.RESTRUCTURING.value)
    assert not target.created
    # 要么覆盖不足、要么匹配不足 —— 总之不能建卡
    assert "未建卡" in (target.reason or ""), f"门槛未生效：{target.reason}"


# --------------------------------------------------------------------------- #
# 误判纠正（用户决定的方案：自动纠正 + 强制留痕 + 防抖动）
# --------------------------------------------------------------------------- #
def _invalidate_then_fix(session, seeded: dict) -> None:
    """先把卡片打成失效，再让那条事件**不再命中失效规则**。

    为什么不直接删事件：``Event`` 被 ``Evidence`` 等表外键引用，删除会
    触发 FK 约束失败（实测踩到）。而失效判定读的是 ``event.title``，
    所以改标题与「规则被修正」在判定层面等价，且不破坏引用完整性。
    """
    from sqlmodel import update

    _write_real_title(session, seeded["company_id"], REAL_INVALIDATING_TITLE)
    assert _invalidated(_rebuild(session, seeded)), "前置条件失败：没有判为失效"

    session.exec(
        update(Event)
        .where(Event.title == REAL_INVALIDATING_TITLE)
        .values(title="关于公司预重整事项的进展公告")
    )
    session.commit()
    assert not _invalidated(_rebuild(session, seeded)), "前置条件失败：改标题后仍判失效"

    # 上面那次 `_rebuild` 已经消耗掉一次判定，会把 recovery_streak 推到 1。
    # 为了让测试的前置状态明确（失效 + 计数从 0 开始），这里显式归零。
    card = session.get(Opportunity, seeded["opportunity_id"])
    assert card is not None
    card.recovery_streak = 0
    session.add(card)
    session.commit()


def test_false_invalidation_is_recovered_after_two_consistent_judgments(session, seeded):
    """★ 误判的失效必须能被系统纠正 —— 但要**连续 2 次**判定一致（防抖动）。

    实测背景：规则修正后东兴/信达两张卡的分数从 21 升到 43.5、
    阶段从「终止/失败」变成「完成」，状态却永远卡在 invalidated。
    卡片自相矛盾，而且系统无法自救。
    """
    from app.pipeline.opportunity_builder import RECOVERY_STREAK_REQUIRED

    assert RECOVERY_STREAK_REQUIRED == 2, "本测试锁定「连续 2 次」的防抖动语义"

    _invalidate_then_fix(session, seeded)
    card = session.get(Opportunity, seeded["opportunity_id"])
    assert card is not None and card.status == OpportunityStatus.INVALIDATED.value

    # 第 1 次：判定「失效条件不再成立」，但还不复活（防抖动）
    _rebuild(session, seeded)
    session.refresh(card)
    assert card.status == OpportunityStatus.INVALIDATED.value, "第 1 次就复活了 —— 防抖动失效"
    assert card.recovery_streak == 1

    # 第 2 次：连续一致 → 纠正回可跟踪状态
    _rebuild(session, seeded)
    session.refresh(card)
    assert card.status != OpportunityStatus.INVALIDATED.value, "连续 2 次一致仍未复活"
    assert card.recovery_streak == 0, "复活后计数应归零"

    # 纠错必须留痕：日志要说明「是原来的判定不成立」，而不是「新发现了什么」
    log = session.exec(
        select(OpportunityStatusLog)
        .where(OpportunityStatusLog.opportunity_id == seeded["opportunity_id"])
        .order_by(OpportunityStatusLog.changed_at.desc())  # type: ignore[attr-defined]
    ).first()
    assert log is not None
    assert log.from_status == OpportunityStatus.INVALIDATED.value
    assert "不再成立" in (log.reason or ""), f"纠错日志没说清原因：{log.reason}"
    assert log.score_before is not None and log.score_after is not None


def test_recovery_streak_resets_when_invalidation_holds_again(session, seeded):
    """失效条件又成立 → 计数归零（否则会攒出一个假的「连续一致」）。"""
    _invalidate_then_fix(session, seeded)
    _rebuild(session, seeded)
    card = session.get(Opportunity, seeded["opportunity_id"])
    assert card is not None and card.recovery_streak == 1

    # 失效条件重新成立：把标题改回会命中规则的版本。
    # 不能重新写一条同标题的公告 —— 事件有幂等去重（INV-EV2），
    # 同 (公司, 类型, 时间) 只会有一条事件。
    from sqlmodel import update

    session.exec(
        update(Event)
        .where(Event.title == "关于公司预重整事项的进展公告")
        .values(title=REAL_INVALIDATING_TITLE)
    )
    session.commit()
    _rebuild(session, seeded)
    session.refresh(card)
    assert card.recovery_streak == 0, "失效条件成立时计数未归零"
    assert card.status == OpportunityStatus.INVALIDATED.value


# --------------------------------------------------------------------------- #
# 「已完成、已停止交易」的标的：不进机会池（用户决定）
# --------------------------------------------------------------------------- #
#: 真实标题：东兴证券 / 信达证券的换股吸收合并公告（股票已停牌、将被换成合并方股票）
REAL_COMPLETED_TITLE = (
    "东兴证券股份有限公司关于公司A股股票连续停牌直至终止上市、"
    "实施换股吸收合并的提示性公告"
)


def test_completed_merger_company_does_not_get_a_card(session, seeded):
    """★ 已停止交易的标的**不建卡** —— 不存在「提前布局」的空间。

    公告与事件**全部保留**（可追溯），只是不进机会池。
    """
    from app.models.knowledge import Company

    company = Company(name="已完成合并测试公司", is_st=False, industry="证券")
    session.add(company)
    session.commit()
    session.refresh(company)
    company_id = int(company.id or 0)

    event = _write_real_title(session, company_id, REAL_COMPLETED_TITLE)
    assert not event.is_invalidating, "换股吸收合并不该被判为失效事件"

    from app.pipeline import profile_seed

    weights = profile_seed.profile_weights(session, seeded["profile_id"])
    results = opportunity_builder.build_opportunities(
        session, company_id, seeded["profile_id"], weights, dry_run=True, commit=False,
    )
    target = next(r for r in results if r.thesis_type == ThesisType.RESTRUCTURING.value)
    assert not target.created, "已停止交易的标的竟然建了卡"
    assert "停止交易" in (target.reason or ""), f"原因未说明：{target.reason}"

    # 事件必须仍然存在（可追溯）
    kept = session.exec(
        select(Event).where(Event.company_id == company_id, Event.title == REAL_COMPLETED_TITLE)
    ).first()
    assert kept is not None, "事件不该被删 —— 只在事件流留痕，不是抹掉痕迹"

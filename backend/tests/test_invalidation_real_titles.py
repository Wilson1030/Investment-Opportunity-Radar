"""真实数据跑出的失效判定假阳性 —— 回归测试（P1-2）。

本文件里的每一条都是**真实公告标题**，来自实跑
``--searchkey`` 定向扫描（终止重整 / 撤回重整 / 不予受理 / 终止上市）。
不是构造的样例 —— 构造样例不会告诉你在真实标题分布下规则会怎么错。

实测抓到的三类错判：

  1. **主体不对称**：``subject_is_third_party`` 只用在正向信号上
     （C1 / 催化阶梯），没用在失效判定上
     → 控股股东自己的重整被撤回，会把上市公司的卡片判死（三安光电）
  2. **词袋式主体判定**：标题里出现「子公司」就整条判为第三方，
     而它在「暨…」并列从句里，主句讲的是公司自己（*ST长药）
  3. **「终止上市」≠「终止重组」**：换股吸收合并里的「终止上市」
     是合并**成功**的结果，却被 ``_TERMINATE_WORDS`` 的裸词「终止」命中，
     判死方向完全反了（东兴证券 / 信达证券）

守两件事：**该失效的必须失效**（否则死掉的苗头永远挂着），
**不该失效的绝不能失效**（否则把正在成功的逻辑判死）。
"""

from __future__ import annotations

import pytest

from app.engine import classifier
from app.facts import CompanyFacts, EventFact, StrategyFacts
from app.models.enums import EventType, ReliabilityLevel
from app.pipeline.event_writer import _mark_invalidating
from app.strategies.restructuring import invalidation as inv

#: (真实标题, 是否应判为「本公司逻辑失效」, 为什么)
REAL_TITLE_CASES: list[tuple[str, bool, str]] = [
    # ---- 不该失效 ----
    (
        "三安光电股份有限公司关于控股股东债权人撤回破产重整申请的公告",
        False,
        "主体是控股股东自己的破产司法程序，不是上市公司的重组预期",
    ),
    (
        "东兴证券股份有限公司关于公司A股股票连续停牌直至终止上市、"
        "实施换股吸收合并的提示性公告",
        False,
        "换股吸收合并的「终止上市」是合并成功的结果，不是交易失败",
    ),
    (
        "关于公司股票存在可能因股价低于面值被终止上市的风险提示公告",
        False,
        "讲的是上市地位风险，且只是「可能」——该预警不该判死",
    ),
    (
        "关于法院决定对公司进行预重整的公告",
        False,
        "正向进展不得被误判为失效",
    ),
    (
        "关于法院裁定受理全资子公司破产重整的公告",
        False,
        "子公司重整不是母公司的逻辑失效（也不该给母公司建卡）",
    ),
    # ---- 必须失效 ----
    (
        "关于法院裁定不予受理重整申请暨子公司宣告破产的公告",
        True,
        "全文确认是「法院裁定不予受理……对公司的重整申请，决定依法终结公司预重整程序」"
        "—— 标题里的「子公司」在「暨…」并列从句里，主句是公司自己",
    ),
    (
        "关于终止筹划重大资产重组的公告",
        True,
        "交易真的终止了",
    ),
    (
        "关于终止换股吸收合并事项的公告",
        True,
        "合并真的黄了 —— 豁免必须能被推翻",
    ),
]


def _facts(title: str, event_type: EventType = EventType.RESTRUCTURING) -> StrategyFacts:
    return StrategyFacts(
        company=CompanyFacts(id=1),
        events=(EventFact(
            id=1, event_type=event_type, title=title,
            evidence_level=ReliabilityLevel.A,
        ),),
    )


@pytest.mark.parametrize("title,expected,why", REAL_TITLE_CASES)
def test_event_layer_matches_real_expectation(title, expected, why):
    """事件层的粗粒度标记（展示用）也必须对。"""
    assert _mark_invalidating(EventType.RESTRUCTURING, title) is expected, f"{title}（{why}）"


@pytest.mark.parametrize("title,expected,why", REAL_TITLE_CASES)
def test_opportunity_layer_matches_real_expectation(title, expected, why):
    """机会层是**权威判定**（``should_invalidate`` 直接决定状态迁移）。"""
    got = inv.should_invalidate(inv.detect(_facts(title)))
    assert got is expected, f"{title}（{why}）"


def test_both_layers_agree_on_every_real_title():
    """★ 两层判定必须一致 —— 不对称正是最初那个 bug 的形态。

    曾经：第三方事件被排除在正向信号之外（C1 / 阶梯），
    却没被排除在失效之外 —— 于是「不给加分，但能判死」。
    """
    for title, _, why in REAL_TITLE_CASES:
        event_layer = _mark_invalidating(EventType.RESTRUCTURING, title)
        opportunity_layer = inv.should_invalidate(inv.detect(_facts(title)))
        assert event_layer is opportunity_layer, f"两层判定不一致：{title}（{why}）"


def test_delisting_risk_is_a_warning_not_a_terminal():
    """退市**风险**该提醒、不该判死（规格 §23 的 severity 分级）。

    只有 ``warning`` 级别入 Alert；``terminal`` / ``severe`` 才迁移状态。
    """
    facts = _facts("关于公司股票存在可能因股价低于面值被终止上市的风险提示公告")
    hits = inv.detect(facts)
    assert inv.warning_hits(hits), "退市风险提示应当产生预警"
    assert not inv.should_invalidate(hits), "仅仅是「可能被终止上市」不该判死"


def test_bankruptcy_applications_rejected_for_the_company_itself_still_invalidate():
    """反向守卫：真实的驳回 / 撤回**必须**仍然判失效。

    修假阳性最容易的错法是「把规则调松到什么都不判」——
    那会退回「死掉的苗头永远挂着」的老问题。
    """
    for title in (
        "关于法院不予受理公司重整申请的公告",
        "关于债权人撤回对公司破产重整申请的公告",
        "关于法院裁定终止公司重整程序的公告",
        "关于公司重整计划未获法院批准的公告",
    ):
        assert inv.should_invalidate(inv.detect(_facts(title))), f"漏判：{title}"


# --------------------------------------------------------------------------- #
# 主体判定的**位置**语义
# --------------------------------------------------------------------------- #
POSITION_CASES: list[tuple[str, bool, str]] = [
    # 第三方是主句主语（在破产谓语之前）→ 第三方
    ("关于法院裁定受理全资子公司破产重整的公告", True, "子公司在前"),
    ("关于孙公司破产重整事项的进展公告", True, "孙公司在前"),
    ("关于公司原控股股东破产重整进展的公告", True, "前控股股东在前"),
    ("关于原相对控股子公司重整事项进展暨完成股权变更的公告", True, "子公司，无破产谓语"),
    # 第三方在并列从句里、主句是本公司 → 不是第三方
    (
        "关于法院裁定不予受理重整申请暨子公司宣告破产的公告",
        False,
        "主句是公司自己的重整申请被驳回，「暨子公司…」是并列从句",
    ),
    # 并集标记
    ("关于法院决定对公司及全资子公司启动预重整的公告", False, "含「公司及」"),
    # 控股股东：谓语不是它自己的破产司法程序
    ("关于控股股东筹划重大事项停牌的公告", False, "现控股股东筹划，通常涉及上市公司"),
    ("龙元建设关于控股股东筹划控制权变更终止的公告", False, "与破产司法程序无关"),
]


@pytest.mark.parametrize("title,expected,why", POSITION_CASES)
def test_subject_detection_is_positional(title, expected, why):
    """主体判定看的是**位置**（第三方是否为主句主语），不是「含不含关键词」。"""
    assert classifier.subject_is_third_party(title) is expected, f"{title}（{why}）"

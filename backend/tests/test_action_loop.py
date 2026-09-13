"""操作闭环测试 —— 每个按钮都必须有**可见且持久**的效果。

## 为什么需要这个文件

用户的原话：「像什么加入观察这些逻辑没有取消，有的按钮是摆设的」。

实测确认了三类问题，每一条都是「界面承诺了、系统不做」：

  1. **「暂时忽略」点了没反应** —— 只写一条 UserAction，状态一动不动
  2. **「将用于优化你的画像」是假承诺** —— `UserAction` 没有任何消费方，
     自动学习从未实现
  3. **「暂时」忽略之后无法恢复** —— `ARCHIVED` 是死胡同（`set()`），
     点错一次只能改数据库
  4. **详情页一个操作都没有** —— 只能到 TRACKING，归档 / 逻辑成立 /
     观察 / 恢复全都没有入口

假承诺比没有提示更糟：用户会以为系统在按他的偏好学习。
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.models.enums import OpportunityStatus, UserActionKind
from app.models.opportunity import Opportunity
from app.models.profile import InvestmentProfile, ProfileThesisWeight
from app.models.thesis import Thesis
from app.pipeline.auto_learn import (
    MAX_STEP,
    MAX_WEIGHT,
    MIN_WEIGHT,
    apply_user_action,
)


def _weights(session: Session, profile_id: int) -> dict[str, float]:
    rows = session.exec(
        select(ProfileThesisWeight).where(ProfileThesisWeight.profile_id == profile_id)
    ).all()
    return {str(r.thesis_type): float(r.weight) for r in rows}


def _thesis_type(session: Session, opportunity_id: int) -> str:
    opportunity = session.get(Opportunity, opportunity_id)
    thesis = session.get(Thesis, int(opportunity.thesis_id))  # type: ignore[union-attr]
    return str(thesis.thesis_type)


# --------------------------------------------------------------------------- #
# ★ 「暂时忽略」必须真的生效
# --------------------------------------------------------------------------- #
def test_ignoring_actually_archives_the_card(client, session, seeded):
    """★ 原先 `ignored` 只写一条日志、状态一动不动 —— 点了跟没点一样。

    现在它必须真的归档（并且因为归档可撤销，所以「暂时」是成立的）。
    """
    before = session.get(Opportunity, seeded["opportunity_id"])
    assert before is not None and before.status == OpportunityStatus.PENDING_CONFIRMATION.value

    response = client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "ignored"},
    ).json()["data"]

    assert response["status_changed"] is True
    assert response["new_status"] == OpportunityStatus.ARCHIVED.value
    assert "状态已更新" in response["message"]

    session.expire_all()
    after = session.get(Opportunity, seeded["opportunity_id"])
    assert after is not None
    assert after.status == OpportunityStatus.ARCHIVED.value, "点「暂时忽略」后状态没变"


def test_ignored_card_disappears_from_todays_opportunities(client, session, seeded):
    """归档后不再出现在「今日机会」—— 否则「忽略」在界面上毫无效果。"""
    cards_before = client.get("/api/radar").json()["data"]["today"]["cards"]
    assert any(c["id"] == seeded["opportunity_id"] for c in cards_before)

    client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "ignored"},
    )
    cards_after = client.get("/api/radar").json()["data"]["today"]["cards"]
    assert not any(c["id"] == seeded["opportunity_id"] for c in cards_after), (
        "归档的卡仍然出现在今日机会里"
    )


def test_archived_can_be_restored(client, session, seeded):
    """★ 「暂时」是承诺：归档必须能撤销。

    原先 ``ARCHIVED: set()`` 是死胡同 —— 用户点错一次就回不来。
    """
    client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "ignored"},
    )
    session.expire_all()
    assert session.get(Opportunity, seeded["opportunity_id"]).status == "archived"  # type: ignore[union-attr]

    restored = client.patch(
        f"/api/opportunities/{seeded['opportunity_id']}/status",
        json={"to_status": "tracking", "reason": "撤销忽略"},
    )
    assert restored.status_code == 200, restored.text
    session.expire_all()
    assert session.get(Opportunity, seeded["opportunity_id"]).status == "tracking"  # type: ignore[union-attr]


def test_repeated_action_is_not_an_error(client, seeded):
    """重复点击同一个操作不该报错（但也要如实说「已经在这个状态」）。"""
    first = client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "confirmed"},
    ).json()["data"]
    assert first["status_changed"] is True

    second = client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "confirmed"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["status_changed"] is False
    assert "无需重复" in second.json()["data"]["message"]


def test_illegal_action_reports_allowed_targets(client, session, seeded):
    """非法操作必须报错**并告诉用户哪些是合法的** —— 而不是一个沉默的 4xx。"""
    opportunity = session.get(Opportunity, seeded["opportunity_id"])
    assert opportunity is not None
    opportunity.status = OpportunityStatus.THESIS_CONFIRMED.value
    session.add(opportunity)
    session.commit()

    # thesis_confirmed 状态不允许回到 pending_confirmation
    response = client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "ignored"},
    )
    # ignored → archived，在 thesis_confirmed 下是非法的
    assert response.status_code >= 400
    detail = response.json().get("error", {}).get("detail", {})
    assert detail.get("allowed_targets"), "报错没有告诉用户合法目标"


# --------------------------------------------------------------------------- #
# ★ 自动学习：把「将用于优化你的画像」从假承诺变成事实
# --------------------------------------------------------------------------- #
def test_confirming_raises_the_strategy_weight(session, seeded):
    """★ 这条原先**完全不存在** —— UserAction 没有任何消费方。"""
    profile = session.exec(select(InvestmentProfile)).first()
    assert profile is not None
    profile_id = int(profile.id or 0)
    thesis_type = _thesis_type(session, seeded["opportunity_id"])
    before = _weights(session, profile_id)[thesis_type]

    outcome = apply_user_action(
        session, profile_id, UserActionKind.CONFIRMED,
        session.get(Opportunity, seeded["opportunity_id"]),
        commit=True,
    )

    assert outcome is not None, "确认关注没有产生任何学习"
    assert outcome["after"] > outcome["before"], "确认关注应当上调权重"
    assert outcome["thesis_type"] == thesis_type
    assert outcome["reason"], "调整必须有可读的理由（否则用户无法判断系统是否跑偏）"
    assert _weights(session, profile_id)[thesis_type] == pytest.approx(outcome["after"])


def test_ignoring_lowers_the_strategy_weight(session, seeded):
    profile = session.exec(select(InvestmentProfile)).first()
    profile_id = int(profile.id or 0)
    thesis_type = _thesis_type(session, seeded["opportunity_id"])
    before = _weights(session, profile_id)[thesis_type]

    outcome = apply_user_action(
        session, profile_id, UserActionKind.IGNORED,
        session.get(Opportunity, seeded["opportunity_id"]), commit=True,
    )
    assert outcome is not None and outcome["after"] < before


def test_viewing_does_not_change_anything(session, seeded):
    """★ 看 ≠ 表态。把「浏览」当成偏好是过度解读。"""
    profile = session.exec(select(InvestmentProfile)).first()
    outcome = apply_user_action(
        session, int(profile.id or 0), UserActionKind.VIEWED,  # type: ignore[union-attr]
        session.get(Opportunity, seeded["opportunity_id"]), commit=True,
    )
    assert outcome is None, "查看证据不该调整画像权重"


def test_auto_learn_disabled_means_no_change(session, seeded):
    """关掉自动学习就一点都不学。"""
    profile = session.exec(select(InvestmentProfile)).first()
    assert profile is not None
    profile.auto_learn_enabled = False
    session.add(profile)
    session.commit()

    outcome = apply_user_action(
        session, int(profile.id or 0), UserActionKind.CONFIRMED,
        session.get(Opportunity, seeded["opportunity_id"]), commit=True,
    )
    assert outcome is None


def test_locked_weight_is_never_auto_adjusted(session, seeded):
    """★ 锁定项永不自动调整 —— 这是规格 M1-06「你可以随时手动覆盖」的机制保证。

    没有它，用户的显式设置会被下一次点击悄悄改掉。
    """
    profile = session.exec(select(InvestmentProfile)).first()
    assert profile is not None
    profile_id = int(profile.id or 0)
    thesis_type = _thesis_type(session, seeded["opportunity_id"])
    profile.locked_weights = [thesis_type]
    session.add(profile)
    session.commit()

    before = _weights(session, profile_id)[thesis_type]
    outcome = apply_user_action(
        session, profile_id, UserActionKind.CONFIRMED,
        session.get(Opportunity, seeded["opportunity_id"]), commit=True,
    )
    assert outcome is None, "锁定的权重被自动调整了"
    assert _weights(session, profile_id)[thesis_type] == pytest.approx(before)


def test_weight_never_falls_below_the_floor(session, seeded):
    """★ 连点「忽略」不能把权重压到 0。

    权重 0 意味着雷达**从此不再为这个策略评任何机会** ——
    而用户本意只是「这一条不感兴趣」，不是「永远别看这个策略」。
    """
    profile = session.exec(select(InvestmentProfile)).first()
    assert profile is not None
    profile_id = int(profile.id or 0)
    thesis_type = _thesis_type(session, seeded["opportunity_id"])
    opportunity = session.get(Opportunity, seeded["opportunity_id"])

    for _ in range(30):
        apply_user_action(session, profile_id, UserActionKind.IGNORED, opportunity, commit=True)

    final = _weights(session, profile_id)[thesis_type]
    assert final >= MIN_WEIGHT - 1e-9, f"权重被压到 {final}，低于下限 {MIN_WEIGHT}"
    assert final > 0, "权重归零会让该策略永远出不了卡"


def test_single_learning_step_is_capped(session, seeded):
    """单次调整有上限，防止权重剧烈跳变。"""
    profile = session.exec(select(InvestmentProfile)).first()
    profile_id = int(profile.id or 0)
    thesis_type = _thesis_type(session, seeded["opportunity_id"])
    before = _weights(session, profile_id)[thesis_type]

    outcome = apply_user_action(
        session, profile_id, UserActionKind.CONFIRMED,
        session.get(Opportunity, seeded["opportunity_id"]), commit=True,
    )
    assert outcome is not None
    assert abs(outcome["delta"]) <= MAX_STEP + 1e-9
    assert outcome["after"] <= MAX_WEIGHT + 1e-9
    assert outcome["before"] == pytest.approx(before)


def test_action_response_reports_what_changed(client, seeded):
    """★ 接口必须回答「实际发生了什么」。

    只刷新列表的话，状态没变时看起来像按钮没反应 ——
    用户就会认为它是摆设。
    """
    data = client.post(
        f"/api/opportunities/{seeded['opportunity_id']}/actions",
        json={"action": "confirmed"},
    ).json()["data"]

    assert set(data) >= {"action", "new_status", "status_changed", "learned", "message"}
    assert data["status_changed"] is True
    assert data["learned"] is not None, "自动学习开着时应当报告权重变化"
    assert "画像权重已调整" in data["message"]
    # 数字精度要和 learned 里的一致，不能四舍五入成另一个数
    assert f"{data['learned']['after']:.3f}" in data["message"]


# --------------------------------------------------------------------------- #
# 状态机与前端的一致性
# --------------------------------------------------------------------------- #
def test_health_exposes_the_status_machine(client):
    """★ 状态机由后端提供，前端不再复制一份。

    踩过的坑：前端操作栏自己写了一张合法性表。复制一旦与后端漂移，
    用户点下去只会得到 4xx，而界面还一直显示那个按钮 ——
    又是一颗「摆设按钮」。
    """
    machine = client.get("/api/health").json()["data"]["status_machine"]
    from app.models.enums import ALLOWED_STATUS_TRANSITIONS

    assert set(machine) == {s.value for s in OpportunityStatus}
    for status, targets in ALLOWED_STATUS_TRANSITIONS.items():
        assert set(machine[status.value]) == {t.value for t in targets}

    # 归档不是死胡同（前端靠这条渲染「恢复跟踪」）
    assert machine["archived"], "归档没有出口 —— 前端无法提供恢复按钮"

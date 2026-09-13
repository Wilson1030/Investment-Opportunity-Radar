"""反馈闭环：把用户操作真正用于调整画像权重（M1-06）。

## 为什么必须实现它

API 的提示语一直写着「已记录，将用于优化你的画像（你可以随时手动覆盖，M1-06）」
—— 但**没有任何代码消费过 ``UserAction``**。
那是一条**假承诺**：界面上承诺了一件事，系统从不做。
假承诺比没有提示更糟 —— 用户会以为系统在学他的偏好。

## 学什么、怎么学（全部确定性、可解释）

每条操作映射到一个**权重微调**：

    确认关注 / 加入跟踪   →  该策略权重 × (1 + STEP)
    暂时忽略             →  该策略权重 × (1 - STEP)
    查看证据             →  不调整（看≠表态，把浏览当成偏好是过度解读）

三条硬约束：

1. **尊重 ``auto_learn_enabled``**：关掉就一点都不学（包括不记录）
2. **尊重 ``locked_weights``**：锁定项**永不**被自动调整。
   这是规格 §M1-06 的原话「你可以随时手动覆盖」的机制保证 ——
   没有它，用户的显式设置会被下一次点击悄悄改掉
3. **单次调整有上限**（``MAX_STEP``），且权重有下限（``MIN_WEIGHT``）：
   否则连点几次「忽略」会把一个策略归零，雷达从此不再看它 ——
   而用户本意可能只是「这条不感兴趣」

## 为什么不用「累计计数 → 批量学习」

那样需要引入一个后台任务与状态表，且用户看不到「为什么我的权重变了」。
现在每次操作**立即**产生一个可解释的调整，并通过 API 返回给界面显示：
「画像权重已调整（重组预期 0.40 → 0.46）」。可解释性优先于算法精巧。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.enums import UserActionKind
from app.models.profile import InvestmentProfile, ProfileThesisWeight
from app.models.thesis import Thesis

#: 单次操作的权重调整幅度（相对）
STEP = 0.15

#: 单次调整的绝对上限（防止权重剧烈跳变）
MAX_STEP = 0.08

#: 权重下限：不把任何策略归零
#:
#: ★ 为什么必须有：连点几次「忽略」会把该策略权重压到 0，
#: 而权重 0 意味着**雷达从此不再为它评任何机会** ——
#: 用户本意只是「这一条不感兴趣」，不是「永远别看这个策略」。
MIN_WEIGHT = 0.05

#: 权重上限
MAX_WEIGHT = 0.60

#: 不参与学习的操作（看 ≠ 表态）
_NEUTRAL_ACTIONS = {UserActionKind.VIEWED}


@dataclass(frozen=True)
class LearningOutcome:
    thesis_type: str
    display_name: str
    before: float
    after: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "thesis_type": self.thesis_type,
            "display_name": self.display_name,
            "before": round(self.before, 4),
            "after": round(self.after, 4),
            "delta": round(self.after - self.before, 4),
            "reason": self.reason,
        }


def _direction(action: UserActionKind) -> int:
    if action in (UserActionKind.CONFIRMED, UserActionKind.TRACKED):
        return +1
    if action is UserActionKind.IGNORED:
        return -1
    return 0


def apply_user_action(
    session: Session,
    profile_id: int,
    action: UserActionKind,
    opportunity,
    *,
    commit: bool = False,
) -> dict | None:
    """把一次操作用于调整画像权重，返回调整摘要（未调整时返回 ``None``）。

    返回 ``dict`` 而不是 ``bool``：界面要能显示「调整了哪个策略、从多少到多少」，
    否则这件事对用户就是不可见的（不可见的学习等于没学）。
    """
    direction = _direction(action)
    if direction == 0 or action in _NEUTRAL_ACTIONS:
        return None

    profile = session.get(InvestmentProfile, profile_id)
    if profile is None or not profile.auto_learn_enabled:
        return None

    thesis = session.get(Thesis, int(opportunity.thesis_id))
    if thesis is None:
        return None
    thesis_type = str(thesis.thesis_type)

    # ★ 锁定项永不自动调整（规格 M1-06「你可以随时手动覆盖」的机制保证）
    if thesis_type in set(profile.locked_weights or []):
        return None

    row = session.exec(
        select(ProfileThesisWeight).where(
            ProfileThesisWeight.profile_id == profile_id,
            ProfileThesisWeight.thesis_type == thesis.thesis_type,
        )
    ).first()
    if row is None:
        return None

    before = float(row.weight)
    if direction > 0:
        delta = min(before * STEP, MAX_STEP)
        after = min(before + delta, MAX_WEIGHT)
    else:
        delta = min(before * STEP, MAX_STEP)
        after = max(before - delta, MIN_WEIGHT)

    if abs(after - before) < 1e-6:
        return None

    row.weight = round(after, 4)
    session.add(row)
    if commit:
        session.commit()

    from app.strategies.registry import get_def
    from app.models.enums import ThesisType

    outcome = LearningOutcome(
        thesis_type=thesis_type,
        display_name=get_def(ThesisType(thesis_type)).display_name,
        before=before,
        after=row.weight,
        reason=(
            "你确认关注了该策略下的机会 → 权重上调"
            if direction > 0
            else f"你忽略了该策略下的机会 → 权重下调（不低于 {MIN_WEIGHT:.2f}）"
        ),
    )
    return outcome.to_dict()


def recent_learning(session: Session, profile_id: int, *, limit: int = 8) -> list[dict]:
    """最近的画像调整记录（供画像页展示「它在学什么」）。

    ★ 为什么要暴露这个：自动学习如果对用户不可见，他就无法判断
    「系统是不是在把我带偏」——而权重直接决定他看到什么。
    所以每次调整都留下可读的理由，用户能据此决定是否锁定权重。
    """
    from app.models.audit import UserAction

    rows = session.exec(
        select(UserAction)
        .order_by(UserAction.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    out: list[dict] = []
    for row in rows:
        if row.action in {a.value for a in _NEUTRAL_ACTIONS}:
            continue
        out.append({
            "action": row.action,
            "opportunity_id": row.opportunity_id,
            "created_at": row.created_at.isoformat(),
            "effect": (
                "上调该策略权重" if row.action in {"confirmed", "tracked"}
                else "下调该策略权重"
            ),
        })
    return out


__all__ = [
    "MAX_STEP",
    "MAX_WEIGHT",
    "MIN_WEIGHT",
    "STEP",
    "LearningOutcome",
    "apply_user_action",
    "recent_learning",
]

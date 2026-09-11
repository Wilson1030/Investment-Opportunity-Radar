"""默认画像的创建与模板套用（MVP 单用户，D02）。

放在 pipeline 层而不是 API 层：pipeline 需要画像权重来算匹配度，
而 API 只是它的一个调用方 —— 依赖方向应该是 ``api → pipeline → engine``。
:mod:`app.api.deps` 对本模块做转发，避免两份实现漂移。
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.models.profile import InvestmentProfile, Investor, ProfileThesisWeight
from app.models.enums import ThesisType
from app.strategies import STRATEGY_TEMPLATES

#: MVP 固定单用户
DEFAULT_HANDLE = "owner"
#: 默认套用「重组猎手」模板 —— 与第一个实现的策略一致（D13）
DEFAULT_TEMPLATE = "重组猎手"


def get_or_create_default_profile(
    session: Session, template: str | None = DEFAULT_TEMPLATE
) -> InvestmentProfile:
    investor = session.exec(select(Investor).where(Investor.handle == DEFAULT_HANDLE)).first()
    if investor is None:
        investor = Investor(handle=DEFAULT_HANDLE, display_name="Owner")
        session.add(investor)
        session.commit()
        session.refresh(investor)

    profile = session.exec(
        select(InvestmentProfile).where(InvestmentProfile.investor_id == investor.id)
    ).first()
    if profile is None:
        profile = InvestmentProfile(investor_id=int(investor.id or 0), name="默认画像")
        session.add(profile)
        session.commit()
        session.refresh(profile)
        if template:
            apply_template(session, profile, template)
    return profile


def apply_template(session: Session, profile: InvestmentProfile, template_name: str) -> None:
    weights = STRATEGY_TEMPLATES.get(template_name)
    if weights is None:
        raise KeyError(f"未知模板：{template_name}")
    for thesis_type, weight in weights.items():
        existing = session.exec(
            select(ProfileThesisWeight).where(
                ProfileThesisWeight.profile_id == profile.id,
                ProfileThesisWeight.thesis_type == thesis_type,
            )
        ).first()
        if existing is None:
            session.add(ProfileThesisWeight(
                profile_id=int(profile.id or 0),
                thesis_type=thesis_type,
                weight=weight,
                source=f"template:{template_name}",
            ))
        else:
            existing.weight = weight
            existing.source = f"template:{template_name}"
            session.add(existing)
    session.commit()


def profile_weights(session: Session, profile_id: int) -> dict[str, float]:
    rows = session.exec(
        select(ProfileThesisWeight).where(ProfileThesisWeight.profile_id == profile_id)
    ).all()
    return {str(row.thesis_type): float(row.weight) for row in rows}


def lock_weight(session: Session, profile: InvestmentProfile, thesis_type: ThesisType) -> None:
    locked = set(profile.locked_weights)
    locked.add(thesis_type)
    profile.locked_weights = sorted(locked, key=lambda t: t.value)
    session.add(profile)
    session.commit()


__all__ = [
    "DEFAULT_HANDLE",
    "DEFAULT_TEMPLATE",
    "apply_template",
    "get_or_create_default_profile",
    "lock_weight",
    "profile_weights",
]

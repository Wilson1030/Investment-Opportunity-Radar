"""API 依赖与基础查询辅助。"""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, select

from app.db import get_session  # noqa: F401 - 供路由直接复用
from app.models.knowledge import Company, Stock
from app.models.profile import InvestmentProfile, Investor, ProfileThesisWeight
from app.strategies import STRATEGY_TEMPLATES

#: MVP 单用户（D02：本地单用户，表结构预留 user_id 但不做鉴权）
DEFAULT_HANDLE = "owner"


def get_or_create_default_profile(session: Session) -> InvestmentProfile:
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
        # 首次创建时套用「重组猎手」模板（与第一个实现的策略一致，D13）
        apply_template(session, profile, "重组猎手")
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


def weight_ratio(weights: dict[str, float], thesis_type: str) -> float:
    """``w_thesis / w_max``（docs/04 §4.1）。归一化后取相对权重。"""
    if not weights:
        return 0.0
    positive = {k: v for k, v in weights.items() if v and v > 0}
    if not positive:
        return 0.0
    maximum = max(positive.values())
    current = weights.get(thesis_type, 0.0)
    if not maximum or current <= 0:
        return 0.0
    return min(1.0, current / maximum)


def company_index(session: Session, company_ids: list[int]) -> tuple[dict, dict]:
    if not company_ids:
        return {}, {}
    companies = session.exec(select(Company).where(Company.id.in_(company_ids))).all()  # type: ignore[attr-defined]
    stocks = session.exec(select(Stock).where(Stock.company_id.in_(company_ids))).all()  # type: ignore[attr-defined]
    return (
        {int(c.id or 0): c for c in companies},
        {int(s.company_id): s for s in stocks},
    )


__all__ = [
    "DEFAULT_HANDLE",
    "apply_template",
    "company_index",
    "get_or_create_default_profile",
    "get_session",
    "profile_weights",
    "weight_ratio",
]

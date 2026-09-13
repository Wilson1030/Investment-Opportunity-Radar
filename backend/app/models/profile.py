"""投资者画像层实体（docs/02 §4.1 ~ §4.2，规格 §4）。

规格 §4.1 明确：不使用「保守 / 稳健 / 激进」三档分类 —— 本项目需要的是
**投资逻辑偏好（Investment Thesis Preference）**。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.enums import ThesisType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Investor(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    handle: str = Field(unique=True, index=True)  # MVP 固定 "owner"（D02：本地单用户）
    display_name: str = "Owner"
    created_at: datetime = Field(default_factory=_utcnow)


class InvestmentProfile(SQLModel, table=True):
    """投资画像。

    INV-P1  ``locked_weights`` 中的 thesis_type，自动学习权重不得变更（M1-06）。
    """

    id: int | None = Field(default=None, primary_key=True)
    investor_id: int = Field(foreign_key="investor.id", index=True)
    name: str = "默认画像"

    horizon: str | None = None                    # short / mid / long
    markets: list[str] = Field(default_factory=lambda: ["A股"], sa_column=Column(JSON))
    industry_prefs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    exclusions: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # 风险承受能力是次要字段，**不作为主要分类依据**（规格 §4.1）
    risk_tolerance: str | None = None

    #: 是否把「早期苗头」纳入关注范围（预重整 / 重整申请 / 法院受理 /
    #: 筹划停牌 / 意向协议）。用户明确要求「提前布局」时开启；
    #: 不想看低确定性信号时可以关掉 —— 这直接改变排序结果（§58 原则 6）。
    accept_early_signals: bool = True

    # 反馈闭环（规格 §36）
    auto_learn_enabled: bool = True
    locked_weights: list[ThesisType] = Field(default_factory=list, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ProfileThesisWeight(SQLModel, table=True):
    """画像 × 策略权重（规格 §4.3）。

    INV-PW1  权重之和不必为 1；**读取时归一化**。
    """

    __table_args__ = (
        UniqueConstraint("profile_id", "thesis_type", name="uq_profile_thesis_weight"),
    )

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="investmentprofile.id", index=True)
    thesis_type: ThesisType = Field(index=True)
    weight: float = 0.0
    source: str = "manual"                        # manual / template:<name> / learned
    updated_at: datetime = Field(default_factory=_utcnow)



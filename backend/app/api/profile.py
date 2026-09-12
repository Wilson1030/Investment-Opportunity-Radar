"""画像与策略（docs/05 §6）。

规格 §4.1：**不使用「保守 / 稳健 / 激进」三档分类** —— 本项目需要的是
投资逻辑偏好（Investment Thesis Preference），且每个倾向可独立设权重。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api import serializers
from app.api.deps import (
    apply_template,
    get_or_create_default_profile,
    get_session,
    profile_weights,
)
from app.api.envelope import RuleViolation, ok
from app.models.enums import EventType, ThesisType
from app.models.profile import ProfileThesisWeight
from app.strategies import (
    IMPLEMENTATION_ORDER,
    STRATEGIES,
    STRATEGY_TEMPLATES,
    get_def,
    implementation_status,
)

router = APIRouter(tags=["profile"])


class ProfileUpdate(BaseModel):
    name: str | None = None
    #: 是否把「早期苗头」（预重整 / 重整申请 / 法院受理 / 筹划停牌 / 意向协议）
    #: 纳入关注范围。用户要「提前布局」时开启；不想看低确定性信号时关闭。
    accept_early_signals: bool | None = None
    horizon: str | None = None
    markets: list[str] | None = None
    industry_prefs: list[str] | None = None
    exclusions: list[str] | None = None
    auto_learn_enabled: bool | None = None
    locked_weights: list[ThesisType] | None = None


class WeightItem(BaseModel):
    thesis_type: ThesisType
    weight: float = Field(ge=0.0, le=1.0)
    locked: bool | None = None
    #: ``manual`` = 用户自己改（永远允许，用户可以推翻自己的锁定）；
    #: ``learned`` = 反馈闭环的自动学习结果（锁定项必须被拒，INV-P1）
    source: str = "manual"


class WeightsUpdate(BaseModel):
    weights: list[WeightItem] | None = None
    template: str | None = None


class ParseNLRequest(BaseModel):
    text: str


# --------------------------------------------------------------------------- #
# 画像
# --------------------------------------------------------------------------- #
@router.get("/profile")
def read_profile(session: Session = Depends(get_session)) -> dict:
    profile = get_or_create_default_profile(session)
    return ok({
        "id": profile.id,
        "name": profile.name,
        "horizon": profile.horizon,
        "markets": profile.markets,
        "industry_prefs": profile.industry_prefs,
        "exclusions": profile.exclusions,
        "auto_learn_enabled": profile.auto_learn_enabled,
        "accept_early_signals": profile.accept_early_signals,
        "locked_weights": [str(t) for t in profile.locked_weights],
        "available_templates": list(STRATEGY_TEMPLATES),
    })


@router.put("/profile")
def update_profile(request: ProfileUpdate, session: Session = Depends(get_session)) -> dict:
    profile = get_or_create_default_profile(session)
    for name in ("name", "horizon", "markets", "industry_prefs", "exclusions",
                 "auto_learn_enabled", "locked_weights", "accept_early_signals"):
        value = getattr(request, name)
        if value is not None:
            setattr(profile, name, value)
    profile.updated_at = datetime.now(timezone.utc)
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return ok({
        "id": profile.id,
        "name": profile.name,
        "locked_weights": [str(t) for t in profile.locked_weights],
        "auto_learn_enabled": profile.auto_learn_enabled,
        "normalized_note": "权重之和不必为 100%，系统读取时归一化（INV-PW1）",
    })


# --------------------------------------------------------------------------- #
# 权重
# --------------------------------------------------------------------------- #
@router.get("/profile/weights")
def read_weights(session: Session = Depends(get_session)) -> dict:
    profile = get_or_create_default_profile(session)
    weights = profile_weights(session, int(profile.id or 0))
    locked = {str(t) for t in profile.locked_weights}
    status_map = implementation_status()

    return ok({
        "weights": [
            {
                "thesis_type": code.value,
                "display_name": get_def(code).display_name,
                "weight": weights.get(code.value, 0.0),
                "status": status_map[code].value,
                "locked": code.value in locked,
            }
            for code in IMPLEMENTATION_ORDER
        ],
        # ★ M1-06：AI 学习结果只是「建议」，锁定项不得被自动覆盖
        "ai_suggested": [],
        "normalized_note": "权重之和不必为 100%，系统读取时归一化（INV-PW1）",
    })


@router.put("/profile/weights")
def update_weights(request: WeightsUpdate, session: Session = Depends(get_session)) -> dict:
    profile = get_or_create_default_profile(session)

    if request.template:
        if request.template not in STRATEGY_TEMPLATES:
            raise RuleViolation(
                f"未知模板：{request.template}",
                {"available": list(STRATEGY_TEMPLATES)},
            )
        apply_template(session, profile, request.template)
        return ok({
            "applied_template": request.template,
            "weights": profile_weights(session, int(profile.id or 0)),
        })

    if not request.weights:
        raise RuleViolation("必须提供 weights 或 template")

    locked = {str(t) for t in profile.locked_weights}

    for item in request.weights:
        key = item.thesis_type.value

        # 显式解锁优先于锁定校验：用户可以在同一次请求里先解锁再改
        if item.locked is False:
            locked.discard(key)

        # ★ INV-P1：只有「自动学习」会被锁定拦住；
        #   用户手动修改永远允许（M1-06 要求必须允许用户覆盖 AI 学习结果）
        if item.source == "learned" and key in locked:
            raise RuleViolation(
                f"INV-P1：{key} 已被用户锁定，自动学习不得覆盖",
                {"locked": sorted(locked)},
            )

        row = session.exec(
            select(ProfileThesisWeight).where(
                ProfileThesisWeight.profile_id == profile.id,
                ProfileThesisWeight.thesis_type == item.thesis_type,
            )
        ).first()
        if row is None:
            session.add(ProfileThesisWeight(
                profile_id=int(profile.id or 0),
                thesis_type=item.thesis_type,
                weight=item.weight,
                source=item.source,
            ))
        else:
            row.weight = item.weight
            row.source = item.source
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)

        if item.locked is True:
            locked.add(key)

    profile.locked_weights = sorted((ThesisType(v) for v in locked), key=lambda x: x.value)
    session.add(profile)
    session.commit()

    return ok({
        "weights": profile_weights(session, int(profile.id or 0)),
        "locked_weights": [str(t) for t in profile.locked_weights],
    })


# --------------------------------------------------------------------------- #
# 策略注册表
# --------------------------------------------------------------------------- #
@router.get("/strategies")
def list_strategies() -> dict:
    """策略注册表 —— 设计全量、实现逐个（D13）。"""
    status_map = implementation_status()
    data = []
    for code in IMPLEMENTATION_ORDER:
        definition = STRATEGIES[code]
        payload = serializers.strategy_detail(definition)
        payload["status"] = status_map[code].value
        data.append(payload)
    return ok(data)


# --------------------------------------------------------------------------- #
# 自然语言策略（M1-04 / 规格 §6）—— P1
# --------------------------------------------------------------------------- #
_MARKET_PATTERNS: dict[str, tuple[str, ...]] = {
    "A股": ("a股", "沪深", "上证", "深证", "创业板", "科创板", "北交所"),
}
_TAG_PATTERNS: dict[str, tuple[str, ...]] = {
    "ST": ("st",),
    "*ST": ("*st", "退市风险"),
}
_FINANCIAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "连续亏损": ("连续亏损", "持续亏损", "亏损"),
    "现金流改善": ("现金流", "现金流转正"),
    "毛利率改善": ("毛利率", "毛利改善"),
    "低估值": ("低估值", "估值低", "低估"),
}
_EVENT_PATTERNS: dict[EventType, tuple[str, ...]] = {
    EventType.RESTRUCTURING: ("重组", "借壳", "重大资产重组"),
    EventType.ASSET_INJECTION: ("资产注入", "注入资产", "资产置换"),
    EventType.CONTROL_CHANGE: ("控制权", "控股股东变化", "实际控制人", "大股东变化", "股权转让"),
    EventType.BANKRUPTCY_REORGANIZATION: ("破产重整", "重整"),
    EventType.M_AND_A: ("并购", "收购"),
    EventType.SHAREHOLDER_BUY: ("增持",),
    EventType.BUYBACK: ("回购",),
    EventType.EARNINGS_TURNAROUND: ("业绩拐点", "业绩改善", "业绩反转"),
    EventType.DIVIDEND_POLICY: ("分红", "股息", "高股息"),
    EventType.POLICY_CATALYST: ("政策",),
    EventType.MAJOR_CONTRACT: ("订单", "合同"),
    EventType.NEW_PRODUCT: ("新产品", "技术突破"),
}

#: 关键词 → 事件类型 → 策略的**输入解析**（人工可审）。
#: 注意：这只用于把用户的话翻译成结构化条件，**不参与机会判定** ——
#: 机会是否成立由策略规则决定（INV-C1 / 规格 §5.6 示例 J）。
_EVENT_TO_THESIS: dict[EventType, ThesisType] = {
    EventType.RESTRUCTURING: ThesisType.RESTRUCTURING,
    EventType.ASSET_INJECTION: ThesisType.RESTRUCTURING,
    EventType.BANKRUPTCY_REORGANIZATION: ThesisType.RESTRUCTURING,
    EventType.CONTROL_CHANGE: ThesisType.RESTRUCTURING,
    EventType.M_AND_A: ThesisType.MA_INTEGRATION,
    EventType.SHAREHOLDER_BUY: ThesisType.SHAREHOLDER_ACTION,
    EventType.BUYBACK: ThesisType.SHAREHOLDER_ACTION,
    EventType.EARNINGS_TURNAROUND: ThesisType.TURNAROUND,
    EventType.DIVIDEND_POLICY: ThesisType.VALUE,
    EventType.POLICY_CATALYST: ThesisType.POLICY,
    EventType.MAJOR_CONTRACT: ThesisType.EVENT_DRIVEN,
    EventType.NEW_PRODUCT: ThesisType.PRODUCT,
}


@router.post("/strategies/parse-nl")
def parse_natural_language(request: ParseNLRequest) -> dict:
    """自然语言 → 结构化策略。

    ★ 必须**先复述并向用户确认**再执行扫描（M1-04），
    因此响应中 ``requires_confirmation`` 恒为 ``true``。
    """
    text = (request.text or "").strip()
    if not text:
        raise RuleViolation("text 不能为空")
    lowered = text.lower()

    market = [m for m, keys in _MARKET_PATTERNS.items() if any(k in lowered for k in keys)]
    stock_tags = [t for t, keys in _TAG_PATTERNS.items() if any(k in lowered for k in keys)]
    financial = [f for f, keys in _FINANCIAL_PATTERNS.items() if any(k in lowered for k in keys)]
    events = [e for e, keys in _EVENT_PATTERNS.items() if any(k in lowered for k in keys)]

    thesis_hits: list[ThesisType] = []
    for event in events:
        target = _EVENT_TO_THESIS.get(event)
        if target and target not in thesis_hits:
            thesis_hits.append(target)
    if not thesis_hits:
        thesis_hits = [ThesisType.EVENT_DRIVEN]

    template = _match_template(thesis_hits)
    if template:
        weights: dict[str, float] = {
            k.value: v for k, v in STRATEGY_TEMPLATES[template].items()
        }
    else:
        share = round(1.0 / len(thesis_hits), 2)
        weights = {t.value: share for t in thesis_hits}

    unresolved: list[str] = []
    if not market:
        market = ["A股"]
        unresolved.append("未指定市场范围（默认 A 股）")
    if not events:
        unresolved.append("未识别到明确的事件类型，请补充你关注的具体事件")

    parts: list[str] = ["我理解你的投资策略为：寻找"]
    if stock_tags:
        parts.append("、".join(stock_tags) + " 属性的")
    if financial:
        parts.append("、".join(financial) + "，且")
    if events:
        readable = "、".join(serializers.EVENT_LABELS.get(e, e.value) for e in events)
        parts.append(f"近期出现{readable}迹象的")
    parts.append(f"{'、'.join(market)}公司。")

    return ok({
        "interpretation": "".join(parts),
        "structured": {
            "market": market,
            "stock_tags": stock_tags,
            "financial_conditions": financial,
            "events": [e.value for e in events],
            "weights": weights,
            "matched_template": template,
        },
        "unresolved": unresolved,
        "requires_confirmation": True,
        "note": "这是规则层的初步解析；请确认后再执行扫描（M1-04）。",
    })


def _match_template(thesis_hits: list[ThesisType]) -> str | None:
    best: tuple[str, int] | None = None
    for name, weights in STRATEGY_TEMPLATES.items():
        overlap = len(set(thesis_hits) & set(weights))
        if overlap and (best is None or overlap > best[1]):
            best = (name, overlap)
    return best[0] if best else None


__all__ = ["router"]

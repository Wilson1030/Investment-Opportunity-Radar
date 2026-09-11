"""mock 示例加载器（docs/07 §4.2）。

把 ``data/mock/example_*.json`` 转成 :class:`StrategyFacts`，
让「事件 → Thesis → 匹配 → 评分」链路可以在**不采集真实数据、不调用 LLM** 的前提下被回归验证。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings
from app.facts import (
    CompanyFacts,
    EventFact,
    FinancialFacts,
    MarketFacts,
    ShareholderFacts,
    StrategyFacts,
)
from app.models.enums import EventType, ReliabilityLevel

MOCK_DIR = settings.mock_dir


@lru_cache(maxsize=1)
def index() -> dict:
    path = MOCK_DIR / "index.json"
    if not path.exists():
        raise FileNotFoundError(
            f"未找到 mock 索引：{path}。请先运行 python tools/build_mock_data.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def available() -> tuple[str, ...]:
    return tuple(entry["file"].removesuffix(".json") for entry in index()["examples"])


def load_raw(name: str) -> dict:
    path = MOCK_DIR / f"{name.removesuffix('.json')}.json"
    if not path.exists():
        raise FileNotFoundError(f"未找到 mock 示例：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def build_facts(payload: dict) -> StrategyFacts:
    """字典 → :class:`StrategyFacts`（与真实 pipeline 使用同一份事实结构）。"""
    company = payload.get("company", {})
    events = tuple(
        EventFact(
            id=1000 + i,
            event_type=EventType(item["event_type"]),
            title=item.get("title", ""),
            summary=item.get("summary", ""),
            event_time=_dt(item.get("event_time")),
            importance=item.get("importance", 0.5),
            certainty=item.get("certainty", 0.5),
            evidence_level=ReliabilityLevel(item["evidence_level"])
            if item.get("evidence_level")
            else None,
            amount_ratio=item.get("amount_ratio", 0.0),
            counterparty_known=item.get("counterparty_known", False),
        )
        for i, item in enumerate(payload.get("events", []))
    )

    financials = FinancialFacts(**{
        k: v for k, v in payload.get("financials", {}).items()
        if k in FinancialFacts.__dataclass_fields__
    })
    shareholder = ShareholderFacts(**{
        k: v for k, v in payload.get("shareholder", {}).items()
        if k in ShareholderFacts.__dataclass_fields__
    })
    market = MarketFacts(**{
        k: v for k, v in payload.get("market", {}).items()
        if k in MarketFacts.__dataclass_fields__
    })

    return StrategyFacts(
        company=CompanyFacts(
            id=abs(hash(company.get("code", "x"))) % 10_000,
            name=company.get("name", ""),
            code=company.get("code", ""),
            is_st=company.get("is_st", False),
            industry=company.get("industry"),
            industry_chain=tuple(company.get("industry_chain", [])),
        ),
        events=events,
        financials=financials,
        shareholder=shareholder,
        market=market,
        open_question_count=payload.get("open_question_count", 0),
        has_unanswered_inquiry=payload.get("has_unanswered_inquiry", False),
        has_history_failure=payload.get("has_history_failure", False),
        has_conflicting_media=payload.get("has_conflicting_media", False),
        has_late_stage_pending_approval=payload.get("has_late_stage_pending_approval", False),
        evidence_levels=tuple(
            ReliabilityLevel(v) for v in payload.get("evidence_levels", [])
        ),
        newest_evidence_age_days=payload.get("newest_evidence_age_days", 0.0),
        is_halted=payload.get("is_halted", False),
        valuation_percentile=payload.get("valuation_percentile"),
    )


def load(name: str) -> tuple[dict, StrategyFacts]:
    payload = load_raw(name)
    return payload, build_facts(payload)


def profile_weight_ratio(payload: dict, thesis_type: str) -> float:
    """按 mock 里的 ``profile_weights`` 算出 ``w_thesis / w_max``（docs/04 §4.1）。"""
    weights: dict[str, float] = payload.get("profile_weights", {})
    positive = {k: v for k, v in weights.items() if v and v > 0}
    if not positive:
        return 0.0
    maximum = max(positive.values())
    current = weights.get(thesis_type, 0.0)
    return min(1.0, current / maximum) if maximum and current > 0 else 0.0


def overrides(payload: dict) -> dict[str, Any]:
    """返回该示例声明的门控覆盖（供实现顺序门控的策略使用）。"""
    return {
        "core_condition_satisfaction": payload.get("core_condition_satisfaction", {}),
        "coverage_cap": payload.get("coverage_cap"),
    }


__all__ = [
    "MOCK_DIR",
    "available",
    "build_facts",
    "index",
    "load",
    "load_raw",
    "overrides",
    "profile_weight_ratio",
]

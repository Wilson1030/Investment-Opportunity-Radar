"""策略实现共享层 —— 10 类策略共用的判定机制。

## 为什么要抽这一层

10 类策略的差异只在**条件与叙事**，而下面这些机制**完全一样**：

  · 证据等级 → 满足度的映射（A 类 1.00 / B 类 0.85 / 仅媒体 0.50 …）
  · 主体错位排除（子公司 / 控股股东的经营变化不是母公司的逻辑）
  · 失效规则的标题匹配（``title_contains`` / ``title_all_of`` / ``title_none_of``）
  · 无条件规则（只有 ``event_type`` 的规则，如「出现减持」）
  · 催化阶梯的「失效优先」短路
  · 待确认事项的确认依据匹配
  · Thesis / Why Now 的四段结构

如果每个策略各写一份，就一定漂移 —— 这不是猜测，是本项目已经踩过两次的坑：

  · 催化阶梯与失效规则各维护一份终止关键词 → 漂移
  · ``subject_is_third_party`` 只用在正向信号上、没用在失效上 →
    控股股东自己的重整被撤回，把上市公司的卡片判死

所以这里的原则是：**机制在共享层，差异在 spec。**

## 各模块

``evidence``     证据等级、证据 ID、主体过滤
``matcher``      失效规则的单条匹配（唯一的实现）
``invalidation`` 泛化的失效检测（驱动 registry 的规则）
``questions``    泛化的待确认事项
``narrative``    泛化的 Thesis / Why Now
``spec``         ``StrategySpec`` + ``RuleBasedStrategy``（把上面组装起来）
"""

from app.strategies.common.evidence import (
    best_level,
    evidence_ids_of,
    own_subject_events,
    satisfaction_for_level,
)
from app.strategies.common.invalidation import detect as detect_invalidation
from app.strategies.common.matcher import match_rule
from app.strategies.common.narrative import Narrative, build_statement, why_now
from app.strategies.common.questions import evaluate_questions, open_questions
from app.strategies.common.spec import RuleBasedStrategy, StrategySpec

__all__ = [
    "Narrative",
    "RuleBasedStrategy",
    "StrategySpec",
    "best_level",
    "build_statement",
    "detect_invalidation",
    "evaluate_questions",
    "evidence_ids_of",
    "match_rule",
    "open_questions",
    "own_subject_events",
    "satisfaction_for_level",
    "why_now",
]

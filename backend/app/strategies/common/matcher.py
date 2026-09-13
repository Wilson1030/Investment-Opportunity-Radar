"""失效规则的**单条匹配** —— 全项目唯一的实现。

## 为什么必须唯一

踩过的坑（两次，都是真事故）：

1. 催化阶梯与失效规则**各维护一份终止关键词** → 漂移。
   结果「法院不予受理重整申请」被判为失效（对），
   却仍拿着 15 分的催化强度（错）—— 卡片自相矛盾。

2. **主体判定不对称**：``subject_is_third_party`` 只用在正向信号上，
   失效判定没调用。于是「控股股东债权人撤回破产重整申请」
   不给上市公司加分（对），却足以把它的卡片判死（错）。

所以本模块是 ``title_contains`` / ``title_all_of`` / ``title_none_of`` /
无条件规则 / 金额阈值 / 主体守卫 / 完成语义守卫的**唯一执行点**。
``restructuring`` 与 ``turnaround`` 的 ``invalidation.py`` 也调用它。
"""

from __future__ import annotations

from app.engine import classifier
from app.facts import EventFact


def match_rule(event: EventFact, definition) -> tuple[bool, str]:
    """一条失效规则是否命中某个事件，返回 ``(是否命中, 原因)``。

    判定顺序（顺序本身有意义）：

    1. ``event_type`` 必须一致（规则按类型精确匹配）
    2. **主体守卫**：子公司 / 控股股东自己的破产司法程序不是母公司的逻辑
    3. **完成语义守卫**：「终止上市 + 换股吸收合并」是完成，不是失败
    4. ``title_none_of``：命中任一排除词即不成立
    5. ``title_all_of``：必须同时包含全部关键词（应对「宣告公司破产」这类插词）
    6. ``title_contains``：标题关键词命中
    7. ``amount_ratio_gt``：金额占比阈值
    8. 都未定义 → 仅按事件类型匹配（如「出现减持」）
    """
    if event.event_type != definition.event_type:
        return False, ""

    title = event.title or ""

    # 2. 主体守卫 —— 与正向信号用同一套判定，杜绝不对称
    if classifier.subject_is_third_party(title):
        return False, ""

    # 3. 完成语义守卫
    if classifier.is_completion_driven_delisting(title):
        return False, ""

    # 4. 排除词优先
    none_of = getattr(definition, "title_none_of", ())
    if none_of and any(kw in title for kw in none_of):
        return False, ""

    # 5. AND 语义
    all_of = getattr(definition, "title_all_of", ())
    if all_of and not all(kw in title for kw in all_of):
        return False, ""

    # 6. 标题关键词
    if definition.title_contains:
        hit_kw = [kw for kw in definition.title_contains if kw in title]
        if not hit_kw:
            return False, ""
        return True, f"标题包含 {'/'.join(hit_kw)}"

    # 7. 金额阈值
    if definition.amount_ratio_gt is not None:
        if event.amount_ratio > definition.amount_ratio_gt:
            return True, (
                f"金额占比 {event.amount_ratio:.2f} > {definition.amount_ratio_gt:.2f}"
            )
        return False, ""

    # 8. 仅按事件类型
    return True, "事件类型匹配"


__all__ = ["match_rule"]

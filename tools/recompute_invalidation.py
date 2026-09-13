"""重算事件表上的 ``is_invalidating`` 标记。

## 为什么需要这个工具

``Event.is_invalidating`` 是在**写事件的那一刻**由规则算出来并落库的。
但失效规则会演进（实测就修过：第三方主体、终止上市语义、不予受理覆盖…），
于是历史事件上留下的是**旧规则的结论** ——
报告里会继续显示「8 条失效」，其中 7 条已经不成立了。

规则改动后应当能重算，而不是只能「清库重跑」（那会丢掉全部 LLM 缓存）。

## 注意

这只是**展示用**的粗粒度标记。权威判定在机会层
（``strategy.invalidation_hits(facts)`` + ``should_invalidate``），
每次生成机会时都会重算，所以本工具**不改机会状态** ——
改状态必须走状态机（``guard`` + ``OpportunityStatusLog``），不能由脚本直改。

用法::

    python tools/recompute_invalidation.py            # 只报告差异（默认）
    python tools/recompute_invalidation.py --apply    # 写回数据库
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.events import Event  # noqa: E402
from app.models.knowledge import Company  # noqa: E402
from app.pipeline.event_writer import _mark_invalidating  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="重算 Event.is_invalidating")
    parser.add_argument("--apply", action="store_true", help="写回数据库（默认只报告）")
    args = parser.parse_args()

    with Session(engine) as session:
        names = {int(c.id or 0): c.name for c in session.exec(select(Company)).all()}
        events = list(session.exec(select(Event)).all())

        changed: list[tuple[str, str, bool, bool]] = []
        for event in events:
            recomputed = _mark_invalidating(event.event_type, event.title or "")
            if bool(event.is_invalidating) != recomputed:
                changed.append((
                    names.get(int(event.company_id), "?"),
                    event.title or "",
                    bool(event.is_invalidating),
                    recomputed,
                ))
                if args.apply:
                    event.is_invalidating = recomputed
                    session.add(event)

        total_before = sum(1 for e in events if e.is_invalidating)
        if args.apply:
            session.commit()

    print(f"事件总数 {len(events)}；按当前规则重算后失效标记应改为 {total_before - len([c for c in changed if c[2] and not c[3]])} 条")
    if not changed:
        print("无需修改 —— 库里的标记与当前规则一致")
        return 0

    print(f"\n差异 {len(changed)} 条：")
    for company, title, before, after in changed:
        arrow = "失效 → 保留" if before and not after else "保留 → 失效"
        print(f"  [{arrow}] {company[:10]:12s} {title[:56]}")

    if args.apply:
        print(f"\n已写回 {len(changed)} 条。")
        print("注：机会状态未改动 —— 状态迁移必须走状态机，下次生成机会时会自动重算。")
    else:
        print("\n（未写库；加 --apply 生效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

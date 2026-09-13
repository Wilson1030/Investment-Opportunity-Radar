"""把 ``llmnoderun`` 从业务库搬到缓存库（一次性迁移，幂等）。

## 为什么需要

LLM 节点缓存原先存在**业务库**的 ``llmnoderun`` 表里，于是
``tools/reset_db.py`` 删业务库时会连带把缓存全丢掉 ——
而缓存的价值恰恰是「清库重跑不用重新调用模型」。

现在缓存改到独立的 ``data/llm_cache.db``（见 ``app/db.py`` 的 ``cache_engine``）。
但已经跑过 pipeline 的人，他的缓存还在业务库里 —— 直接切过去等于白丢，
所以提供这个搬运工具。

## 安全性

  · **幂等**：按 ``(node_name, input_hash, prompt_version)`` 唯一键跳过已存在的行
  · **不删源**：业务库里的旧表原样保留（等下次 ``reset_db`` 自然清掉）
  · 搬完打印统计，能核对条数

用法::

    python tools/migrate_llm_cache.py            # 只报告差异
    python tools/migrate_llm_cache.py --apply    # 执行搬运
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from sqlmodel import Session, select  # noqa: E402

from app.db import DATABASE_URL, cache_engine, engine, ensure_cache_tables  # noqa: E402
from app.models.audit import LlmNodeRun  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="把 LLM 缓存从业务库搬到缓存库")
    parser.add_argument("--apply", action="store_true", help="执行搬运（默认只报告）")
    args = parser.parse_args()

    ensure_cache_tables()

    if engine.url == cache_engine.url:
        print("业务库与缓存库是同一个文件（LLM_CACHE_URL 未配置）—— 无需搬运。")
        return 0

    with Session(engine) as src:
        try:
            legacy = list(src.exec(select(LlmNodeRun)).all())
        except Exception as exc:  # noqa: BLE001 - 旧库可能根本没有这张表
            print(f"业务库里读不到 llmnoderun（{exc}）—— 无需搬运。")
            return 0

    with Session(cache_engine) as dst:
        existing = {
            (row.node_name, row.input_hash, row.prompt_version)
            for row in dst.exec(select(LlmNodeRun)).all()
        }
        pending = [
            row for row in legacy
            if (row.node_name, row.input_hash, row.prompt_version) not in existing
        ]

    print(f"业务库 llmnoderun：{len(legacy)} 行")
    print(f"缓存库已有：{len(existing)} 行")
    print(f"待搬运：{len(pending)} 行")

    if not pending:
        print("已完成同步。")
        return 0

    if not args.apply:
        print("\n（未搬运；加 --apply 生效）")
        print(f"业务库：{DATABASE_URL}")
        print(f"缓存库：{cache_engine.url}")
        return 0

    with Session(cache_engine) as dst:
        for row in pending:
            dst.add(LlmNodeRun(
                node_name=row.node_name, input_hash=row.input_hash,
                prompt_version=row.prompt_version, provider=row.provider,
                model=row.model, layer=row.layer, status=row.status,
                attempt=row.attempt, prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens, latency_ms=row.latency_ms,
                output_json=row.output_json, raw_output=row.raw_output,
                error=row.error, created_at=row.created_at,
            ))
        dst.commit()

    with Session(cache_engine) as dst:
        total = len(list(dst.exec(select(LlmNodeRun)).all()))
    print(f"\n已搬运 {len(pending)} 行；缓存库现有 {total} 行。")
    print("业务库里的旧表未改动（下次 reset_db 会自然清掉）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

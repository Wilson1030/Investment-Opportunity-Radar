"""采集命令行入口 —— 委托给 :mod:`app.pipeline.runner`。

**只保留一个编排实现**：早期版本在这里也有一份采集逻辑，两处会漂移。
现在这里只负责参数解析与帮助文本，真正的流水线在 pipeline/runner.py。

用法::

    python -m app.ingest                          # mock 源（离线、确定性）
    python -m app.ingest --source cninfo --limit 5  # 真实公告 dry-run（前 3 条调 LLM）
    python -m app.ingest --source cninfo --live     # 真写库（需先通过预热，docs/07 §6）
"""

from __future__ import annotations

from app.pipeline.runner import STAGE_CHOICES, PipelineOptions, main, run_pipeline

#: 兼容旧签名（调度器与测试使用）
def run_ingest(stage: str = "incremental", *, dry_run: bool | None = None,
               limit: int | None = None, scope_name: str | None = None):
    from app.config import settings

    return run_pipeline(PipelineOptions(
        stage=stage,
        dry_run=settings.ingest_dry_run if dry_run is None else dry_run,
        limit=limit,
        scope=scope_name or settings.ingest_scope,
        source="mock",
    )).report


__all__ = ["STAGE_CHOICES", "PipelineOptions", "main", "run_ingest", "run_pipeline"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

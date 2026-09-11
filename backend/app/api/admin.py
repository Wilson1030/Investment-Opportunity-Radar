"""管理与可观测（docs/05 §8，M2-13）。

**失败不隐藏**：``/admin/runs/{id}/errors`` 逐条列出失败项，
``/admin/llm-runs`` 暴露 ``schema_failure_rate`` —— 这是判断本机 4B 模型
能否胜任抽取任务的直接依据（风险 R3，docs/07 §5.2）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import get_session
from app.api.envelope import NotFound, ok, ok_list
from app.engine.funnel import QualityMetrics
from app.models.audit import IngestRun, LlmNodeRun
from app.models.enums import IngestStage, NodeRunStatus

router = APIRouter(tags=["admin"])


class IngestRequest(BaseModel):
    stage: IngestStage = IngestStage.INCREMENTAL
    scope: str | None = None
    limit: int | None = 5
    live: bool = False


@router.post("/admin/ingest")
def trigger_ingest(request: IngestRequest) -> dict:
    """手动触发采集（``SCHEDULER_ENABLED=false`` 时的唯一入口）。

    骨架期同步执行并直接返回报告 —— 底层任务队列化属于 P2
    （docs/07 §9），这里不做假的「已受理」响应。
    """
    from app.ingest.cli import run_ingest

    report = run_ingest(
        stage=str(request.stage),
        dry_run=not request.live,
        limit=request.limit,
        scope_name=request.scope,
    )
    return ok(
        report.to_dict(),
        meta={"note": "骨架期同步执行；任务队列化见 docs/07 §9（P2）"},
    )


@router.get("/admin/runs")
def list_runs(limit: int = 20, session: Session = Depends(get_session)) -> dict:
    rows = session.exec(
        select(IngestRun).order_by(IngestRun.started_at.desc()).limit(limit)  # type: ignore[attr-defined]
    ).all()
    return ok([
        {
            "id": row.id,
            "adapter": row.adapter,
            "scope": row.scope,
            "stage": row.stage,
            "dry_run": row.dry_run,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "items_found": row.items_found,
            "items_new": row.items_new,
            "items_skipped": row.items_skipped,
            "items_failed": row.items_failed,
            "parse_failed": row.parse_failed,
            "parse_needs_ocr": row.parse_needs_ocr,
            "parse_failure_rate": row.parse_failure_rate,
            "field_missing_rate": row.field_missing_rate,
            "funnel": row.funnel,
            "quality": row.quality,
            "error_summary": row.error_summary,
        }
        for row in rows
    ])


@router.get("/admin/runs/{run_id}/errors")
def run_errors(run_id: int, session: Session = Depends(get_session)) -> dict:
    row = session.get(IngestRun, run_id)
    if row is None:
        raise NotFound("ingest_run", run_id)
    return ok(row.errors)


@router.get("/admin/llm-runs")
def llm_runs(
    node: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = 50,
    session: Session = Depends(get_session),
) -> dict:
    statement = select(LlmNodeRun)
    if node:
        statement = statement.where(LlmNodeRun.node_name == node)
    if status:
        statement = statement.where(LlmNodeRun.status == NodeRunStatus(status))
    rows = session.exec(
        statement.order_by(LlmNodeRun.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    ).all()

    total = len(rows)
    schema_errors = sum(1 for r in rows if r.status is NodeRunStatus.SCHEMA_ERROR)
    cached = sum(1 for r in rows if r.status is NodeRunStatus.CACHED)
    metrics = QualityMetrics(
        llm_schema_failure_rate=(schema_errors / max(1, total - cached)),
        llm_cached_rate=(cached / max(1, total)),
    )

    return ok_list(
        [
            {
                "id": r.id,
                "node_name": r.node_name,
                "model": r.model,
                "provider": r.provider,
                "prompt_version": r.prompt_version,
                "status": r.status.value,
                "attempt": r.attempt,
                "latency_ms": r.latency_ms,
                "input_hash": r.input_hash[:12],
                "error": r.error,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        meta={
            "schema_failure_rate": round(metrics.llm_schema_failure_rate, 4),
            "cached_rate": round(metrics.llm_cached_rate, 4),
            "extractor_verdict": metrics.extractor_verdict,
            "hallucination_signal": metrics.hallucination_signal,
            "note": (
                "schema_failure_rate 是判断本机模型能否胜任抽取任务的直接依据（R3）；"
                "evidence 拒收原因见 pipeline 报告的 quality.evidence_rejected_reasons"
            ),
        },
    )


__all__ = ["router"]

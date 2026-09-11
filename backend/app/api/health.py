"""``GET /api/health``（docs/05 §1）—— 含启动自检结果与退化告警。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.deps import get_session
from app.api.envelope import ok
from app.config import settings
from app.db import engine
from app.scheduler.jobs import describe as describe_scheduler
from app.strategies import implementation_status, validate_registry

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    return ok(_health_payload(session))


def _health_payload(session: Session) -> dict:
    db_status = "ok"
    try:
        from sqlmodel import text

        session.exec(text("SELECT 1"))  # type: ignore[call-overload]
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {type(exc).__name__}"

    provider_name, model, base_url, _ = settings.llm_for("extract")
    llm_reachable = _probe_ollama(base_url) if provider_name == "ollama" else None

    registry_problems = validate_registry()
    status_map = implementation_status()

    return {
        "status": "ok" if (db_status == "ok" and not registry_problems) else "degraded",
        "db": db_status,
        "engine": str(engine.url).split("///")[-1],
        "llm": {
            "mode": settings.llm_mode,
            "provider": provider_name,
            "model": model,
            "base_url": base_url,
            "reachable": llm_reachable,
        },
        "scheduler": describe_scheduler(),
        "strategies": {
            "implemented": [c.value for c, s in status_map.items() if s.value == "implemented"],
            "designed": [c.value for c, s in status_map.items() if s.value != "implemented"],
        },
        "registry_problems": registry_problems,
        "ingest": {
            "dry_run": settings.ingest_dry_run,
            "scope": settings.ingest_scope,
            "lookback_days": settings.ingest_lookback_days,
            "store_fulltext": settings.store_announcement_fulltext,
        },
    }


def _probe_ollama(base_url: str) -> bool | None:
    import httpx

    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{base_url.rstrip('/')}/api/tags")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


__all__ = ["router"]

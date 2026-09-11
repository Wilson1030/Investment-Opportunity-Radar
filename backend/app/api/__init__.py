"""API 层：聚合全部路由（docs/05-API契约）。

    health.py         /health（含自检与退化告警）
    radar.py          /radar（首页聚合）
    opportunities.py  /opportunities*（含可解释性核心接口 score-breakdown）
    events.py         /events /events/clusters /alerts*
    thesis.py         /thesis*（My Thesis）
    profile.py        /profile* /strategies*
    admin.py          /admin/*（采集触发与可观测）
"""

from fastapi import APIRouter

from app.api import admin, events, health, opportunities, profile, radar, thesis

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(radar.router)
api_router.include_router(opportunities.router)
api_router.include_router(events.router)
api_router.include_router(thesis.router)
api_router.include_router(profile.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]

"""FastAPI 应用装配（docs/05 契约 + docs/03 §9）。

启动自检（fail-fast，而不是静默跑着一个设计自相矛盾的系统）::

    1. 每个策略的失效条件非空（INV-TT1）
    2. implemented 策略必须有实现类（INV-TT2）
    3. 权重合计 = 0.95 且风险不在正向权重表内
    4. 所有风险触发键都有判别函数
    5. 交易日历可用性（不可用时显式告警，R5）
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.api.envelope import ApiError, api_error_handler, unhandled_error_handler
from app.config import settings
from app.db import init_db

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

#: 本地单用户（D02）：只允许本机前端访问
ALLOWED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Investment Opportunity Radar",
        description=(
            "以投资者画像为核心、以事件为入口、以投资 Thesis 为组织方式、"
            "以证据链为可信基础的投资机会发现系统。\n\n"
            "**本系统不提供投资建议，不承诺收益。**所有评分均为概率性研究线索。"
        ),
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(api_router)

    @app.on_event("startup")
    def _startup() -> None:
        _selfcheck()
        init_db()
        settings.ensure_dirs()

        scheduler = None
        from app.scheduler.jobs import build_scheduler

        scheduler = build_scheduler()
        if scheduler is not None:
            scheduler.start()
            logger.info("调度器已启动（dry_run=%s）", settings.ingest_dry_run)
        else:
            logger.info(
                "调度器未启用（SCHEDULER_ENABLED=false）。"
                "手动采集：python -m app.ingest --stage incremental"
            )
        app.state.scheduler = scheduler

    @app.on_event("shutdown")
    def _shutdown() -> None:
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": "Investment Opportunity Radar",
            "version": "0.1.0",
            "docs": "/docs",
            "api": "/api/health",
            "disclaimer": "本系统为投资研究与信息组织工具，不构成投资建议。",
        }

    return app


def _selfcheck() -> None:
    from app.strategies import validate_registry

    problems = validate_registry()
    if problems:
        for problem in problems:
            logger.error("注册表自检失败：%s", problem)
        raise RuntimeError(
            f"策略注册表自检未通过（{len(problems)} 项）。"
            "这些问题会让评分与排序失去一致性，必须先修复。"
        )
    logger.info("策略注册表自检通过（10 类策略设计完整，风险触发键齐备）")

    from app.scheduler.calendar import get_calendar

    calendar = get_calendar()
    if calendar.degraded and calendar.warning:
        logger.warning(calendar.warning)


app = create_app()


__all__ = ["ALLOWED_ORIGINS", "app", "create_app"]

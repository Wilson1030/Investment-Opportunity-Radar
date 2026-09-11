"""响应信封与错误处理（docs/05 §0.2 / §0.3）。

**免责声明是硬要求**：任何含分数的响应都必须带 ``disclaimer``（规格第 60 节第 21 条），
测试 ``test_api_contract.py`` 会断言这一点。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

#: 全站免责声明 —— 不允许被隐藏（规格 §39 / M11-02）
DISCLAIMER = (
    "评分为概率性研究线索的相对排序信号，不构成投资建议，不代表任何收益预期。"
    "系统只辅助发现、解释、验证与跟踪，不替用户决策。"
)


class ApiError(Exception):
    """业务错误 → 统一错误信封。"""

    def __init__(self, status_code: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail or {}


class NotFound(ApiError):
    def __init__(self, entity: str, entity_id: Any) -> None:
        super().__init__(
            404,
            f"{entity.upper()}_NOT_FOUND",
            f"{entity} 不存在",
            {f"{entity.lower()}_id": entity_id},
        )


class InvalidStatusTransition(ApiError):
    def __init__(self, from_status: str | None, to_status: str, allowed: list[str]) -> None:
        super().__init__(
            409,
            "INVALID_STATUS_TRANSITION",
            f"非法状态迁移：{from_status} → {to_status}",
            {"from": from_status, "to": to_status, "allowed": allowed},
        )


class RuleViolation(ApiError):
    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(422, "RULE_VIOLATION", message, detail)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ok(data: Any, meta: dict | None = None) -> dict:
    payload = {"data": data, "meta": {"generated_at": _now()}}
    if meta:
        payload["meta"].update(meta)
    return payload


def ok_list(data: list, page: dict | None = None, meta: dict | None = None) -> dict:
    return {
        "data": data,
        "page": page or {"limit": len(data), "offset": 0, "total": len(data), "has_more": False},
        "meta": {"generated_at": _now(), **(meta or {})},
    }


def error_body(code: str, message: str, detail: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail or {}}}


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message, exc.detail),
    )


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_body("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"),
    )


__all__ = [
    "DISCLAIMER",
    "ApiError",
    "InvalidStatusTransition",
    "NotFound",
    "RuleViolation",
    "api_error_handler",
    "error_body",
    "ok",
    "ok_list",
    "unhandled_error_handler",
]

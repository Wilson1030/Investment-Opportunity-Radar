"""数据库引擎与会话。

SQLite 起步（D04）。单进程调度，开启 WAL 以降低「采集写 + API 读」的锁冲突（R7）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import PROJECT_ROOT, settings

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=_connect_args)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """SQLite 调优：外键约束必须显式开启，否则 FK 形同虚设（INV-W1 依赖它）。"""
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _ensure_sqlite_parent() -> None:
    url = settings.database_url
    if not url.startswith("sqlite"):
        return
    raw = url.split("sqlite:///", 1)[-1]
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / raw.lstrip("./")
    path.parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    """建表（骨架期用 create_all；正式环境用 ``alembic upgrade head``）。"""
    _ensure_sqlite_parent()
    import app.models  # noqa: F401  —— 触发全部实体注册

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI 依赖。"""
    with Session(engine) as session:
        yield session

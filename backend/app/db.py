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

def resolve_database_url(url: str) -> str:
    """把**相对** SQLite 路径解析为绝对路径。

    ★ 踩过的坑：``sqlite:///./data/radar.db`` 是相对路径，SQLAlchemy 按**进程 CWD**
    解析。于是从项目根运行工具、从 ``backend/`` 运行 uvicorn，各自打开了一个
    不同的数据库文件 —— 数据被静默劈成两份，还会出现「表结构看起来回退了」
    （因为其中一个文件是更早的 ``create_all`` 建的，缺后来的列）。

    现在统一相对于 ``PROJECT_ROOT`` 解析，与 CWD 无关。
    """
    if not url.startswith("sqlite"):
        return url
    _, _, raw = url.partition("sqlite:///")
    if not raw or raw.startswith(":memory:"):
        return url
    path = Path(raw)
    if path.is_absolute():
        return url
    return f"sqlite:///{(PROJECT_ROOT / raw.lstrip('./')).as_posix()}"


DATABASE_URL = resolve_database_url(settings.database_url)

#: SQLite 忙等超时（秒）。默认只有 5 秒 —— 而节点缓存会用自己的连接写
#: ``llm_node_run``，若主 session 正持有写事务就会直接抛
#: ``database is locked``（实测整条 pipeline 因此中断）。
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0

_connect_args = (
    {"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_SECONDS}
    if DATABASE_URL.startswith("sqlite")
    else {}
)
engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """SQLite 调优：外键约束必须显式开启，否则 FK 形同虚设（INV-W1 依赖它）。"""
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # 与 connect_args 的 timeout 一致：写锁被占用时先等，而不是立刻失败
    cursor.execute(f"PRAGMA busy_timeout={int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)}")
    cursor.close()


def _ensure_sqlite_parent() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    Path(DATABASE_URL.split("sqlite:///", 1)[-1]).parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    """建表（骨架期用 create_all；正式环境用 ``alembic upgrade head``）。"""
    _ensure_sqlite_parent()
    import app.models  # noqa: F401  —— 触发全部实体注册

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI 依赖。"""
    with Session(engine) as session:
        yield session

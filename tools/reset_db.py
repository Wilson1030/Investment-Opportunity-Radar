"""安全重置本地数据库（避免手工删文件的坑）。

**为什么需要这个脚本**：SQLite 开了 WAL，未 checkpoint 的改动只存在于 ``-wal`` 里。
如果只删 ``radar.db`` 却留下 ``-wal``／``-shm``，或反过来先删 ``-wal``，
会出现「表结构看起来回退了」这种极难排查的现象。

正确顺序：先让所有连接释放 → 三个文件一起删 → ``alembic upgrade head``。

用法::

    python tools/reset_db.py            # 停手前会先问一句
    python tools/reset_db.py --yes      # 直接重置
    python tools/reset_db.py --yes --seed   # 重置后跑 mock pipeline 造一组数据
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

#: ★ 数据库文件必须**问应用**（app.db.DATABASE_URL），不能自己拼路径。
#: 踩过的坑：这里曾经硬编码 BACKEND / "data" / "radar.db"，
#: 而应用解析出来的是 PROJECT_ROOT / "data" / "radar.db" ——
#: 于是「清库」清的是另一个文件，真正的库里旧数据还在，
#: 表现为「重置之后 mock 数据还在」，极难排查。
sys.path.insert(0, str(BACKEND))
from app.db import DATABASE_URL  # noqa: E402

if not DATABASE_URL.startswith("sqlite"):
    raise SystemExit("本工具只用于重置本地 SQLite 库")

DB = Path(DATABASE_URL.split("sqlite:///", 1)[-1])
SIDECARS = [Path(str(DB) + s) for s in ("-wal", "-shm", "-journal")]


def _python() -> str:
    return sys.executable


def _run(args: list[str], cwd: Path) -> int:
    print(f"$ {' '.join(args)}")
    return subprocess.call(args, cwd=str(cwd))


def main() -> int:
    parser = argparse.ArgumentParser(description="安全重置 SQLite 数据库")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    parser.add_argument("--seed", action="store_true", help="重置后跑 mock pipeline 造数")
    args = parser.parse_args()

    if not args.yes:
        answer = input(f"将删除 {DB.name}（及其 -wal/-shm）并重建表结构。继续？[y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            print("已取消")
            return 1

    # 1) 释放连接（这一步不能省：Windows 上文件被占用就删不掉）
    _run([_python(), "-c", "from app.db import engine; engine.dispose()"], BACKEND)

    # 2) 三个文件一起删
    removed: list[str] = []
    for path in [DB, *SIDECARS]:
        if path.exists():
            try:
                path.unlink()
                removed.append(path.name)
            except PermissionError:
                print(
                    f"⚠ {path.name} 被占用 —— 请先停掉 uvicorn / 其它 python 进程再试。\n"
                    "  Windows 查占用端口：netstat -ano | findstr :8000"
                )
                return 2
    print(f"已删除：{removed or '（无文件）'}")

    # 3) 重建（两个迁移一起应用）
    if _run([_python(), "-m", "alembic", "upgrade", "head"], BACKEND) != 0:
        return 3

    # 4) 可选：造一组 mock 数据
    if args.seed:
        if _run([_python(), "-X", "utf8", "-m", "app.ingest", "--source", "mock"], BACKEND) != 0:
            return 4

    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

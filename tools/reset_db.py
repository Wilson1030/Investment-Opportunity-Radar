"""安全重置本地数据库（避免手工删文件的坑）。

**为什么需要这个脚本**：SQLite 开了 WAL，未 checkpoint 的改动只存在于 ``-wal`` 里。
如果只删 ``radar.db`` 却留下 ``-wal``／``-shm``，或反过来先删 ``-wal``，
会出现「表结构看起来回退了」这种极难排查的现象。

正确顺序：先让所有连接释放 → 三个文件一起删 → ``alembic upgrade head``。

用法::

    python tools/reset_db.py                  # 停手前会先问一句
    python tools/reset_db.py --yes            # 直接重置
    python tools/reset_db.py --yes --seed     # 重置后跑 mock pipeline 造一组数据
    python tools/reset_db.py --yes --stop     # 重置前先停掉占用数据库的服务

★ **服务在跑时本工具会失败**（Windows 上文件被占用就删不掉）。
踩过的坑：失败信息被重定向吞掉后，看起来像「重置成功了」，
而实际库里旧数据还在 —— 之后的验证全部建立在旧库上。
所以现在会**主动探测占用者**，并在没有 ``--stop`` 时给出可执行的指令。
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


SERVICE_PORTS = (8000, 5173)


def _python() -> str:
    return sys.executable


def _port_listeners(port: int) -> list[int]:
    """监听该端口的进程 PID（Windows 用 netstat，POSIX 用 lsof）。"""
    import re
    import subprocess as sp

    try:
        if sys.platform == "win32":
            out = sp.run(["netstat", "-ano"], capture_output=True, text=True,
                         encoding="utf-8", errors="ignore").stdout
            pids = set()
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line.upper():
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pids.add(int(parts[-1]))
            return sorted(pids)
        out = sp.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True,
                     encoding="utf-8", errors="ignore").stdout
        return sorted(int(x) for x in re.findall(r"\d+", out))
    except Exception:  # noqa: BLE001 - 探测失败不该阻塞流程
        return []


def _stop_services() -> None:
    """停掉占用数据库的服务（后端 / 前端）。"""
    import subprocess as sp

    for port in SERVICE_PORTS:
        for pid in _port_listeners(port):
            print(f"  停止占用 :{port} 的进程 {pid}")
            if sys.platform == "win32":
                sp.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, text=True)
            else:
                sp.run(["kill", "-9", str(pid)], capture_output=True, text=True)


def _run(args: list[str], cwd: Path) -> int:
    print(f"$ {' '.join(args)}")
    return subprocess.call(args, cwd=str(cwd))


def main() -> int:
    parser = argparse.ArgumentParser(description="安全重置 SQLite 数据库")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    parser.add_argument("--seed", action="store_true", help="重置后跑 mock pipeline 造数")
    parser.add_argument("--stop", action="store_true",
                        help="重置前先停掉占用数据库的服务（后端 8000 / 前端 5173）")
    args = parser.parse_args()

    # ★ 先探测占用者。服务在跑时删除必然失败，早失败比删一半好。
    busy: list[tuple[int, int]] = [
        (port, pid) for port in SERVICE_PORTS for pid in _port_listeners(port)
    ]
    if busy:
        print("检测到正在运行的服务：")
        for port, pid in busy:
            print(f"  :{port}  PID {pid}")
        if args.stop:
            _stop_services()
            import time
            time.sleep(2)
        else:
            print(
                "\n数据库文件被这些进程占用，Windows 上无法删除 —— 重置会失败。\n"
                "  加 --stop 让本工具先停掉它们：\n"
                "    python tools/reset_db.py --yes --stop\n"
                "  或手动停：taskkill /F /PID <PID>"
            )
            return 2

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
    # ★ 校验：文件真的没了才算删成功。
    #   踩过的坑：占用导致 unlink 静默失败时，后续步骤照跑，
    #   而库里旧数据还在 —— 之后的验证全部建立在旧库上。
    leftovers = [p.name for p in [DB, *SIDECARS] if p.exists()]
    if leftovers:
        print(
            f"\n⚠ 这些文件仍存在（删除失败）：{leftovers}\n"
            "  多半是还有进程占用。加 --stop 重试，或手动结束进程后重试。"
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

"""一键启动：后端 API + 前端界面，并在退出时一起关掉。

## 为什么要有它

原本要开两个终端分别跑 uvicorn 与 vite，还要记住各自的端口与工作目录。
而且**后台进程跨 shell 调用可能被杀**（踩过的坑：分开两次 bash 调用启动，
第二次调用结束时第一个进程也没了）—— 所以启动与等待必须在同一个进程里完成。

## 用法

    python tools/start_all.py            # 启动，Ctrl+C 一起停
    python tools/start_all.py --no-front # 只要后端（调试 API 时用）

启动前会做几项**前置检查**，而不是让你对着报错猜：

  · Python 解释器对不对（是否装了 fastapi / sqlmodel）
  · 前端依赖是否装过（``node_modules``）
  · 端口是否被占用（占用时给出查占用的命令，而不是静默失败）
  · 数据库文件是否存在（不存在则提示先跑 ``reset_db``）
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"

BACKEND_PORT = 8000
FRONTEND_PORT = 5173


def _fail(message: str, hint: str = "") -> None:
    print(f"\n✗ {message}")
    if hint:
        print(f"  {hint}")
    raise SystemExit(1)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _check_python() -> None:
    try:
        import fastapi  # noqa: F401
        import sqlmodel  # noqa: F401
    except ImportError as exc:
        _fail(
            f"当前 Python 缺少依赖（{exc.name}）",
            f"当前解释器：{sys.executable}\n"
            "  请在**已激活项目 Python 环境**的终端里运行本脚本：\n"
            "    python tools/start_all.py\n"
            '  或先装依赖：cd backend && pip install -e "."（采集层再装 ".[ingest]"）',
        )


def _check_database() -> None:
    db = DATA / "radar.db"
    if not db.exists():
        _fail(
            f"数据库不存在：{db}",
            "先初始化：python tools/reset_db.py --yes"
            "（加 --seed 可顺便造一组离线样例数据）",
        )
    print(f"  数据库    {db.name}（{db.stat().st_size // 1024} KB）")


def _check_ports(with_frontend: bool) -> None:
    ports = [(BACKEND_PORT, "后端")] + ([(FRONTEND_PORT, "前端")] if with_frontend else [])
    for port, label in ports:
        if _port_in_use(port):
            _fail(
                f"{label}端口 {port} 已被占用",
                f"Windows 查占用：netstat -ano | findstr :{port}\n"
                "  结束占用：taskkill /F /PID <PID>",
            )
    print(f"  端口      {', '.join(str(p) for p, _ in ports)} 均可用")


def _npm_command() -> str:
    """解析 npm 可执行文件。

    ★ Windows 上 npm 是 ``npm.CMD`` 批处理 shim，``Popen(["npm", ...])``
    会直接抛 ``FileNotFoundError``（实测踩到：一键启动在 Windows 上
    后端起来了、前端立刻失败）。``shutil.which`` 会返回真正的 ``.CMD`` 路径。
    """
    npm = shutil.which("npm")
    if npm is None:
        _fail("找不到 npm", "请先安装 Node.js（https://nodejs.org）")
    return npm


def _check_frontend() -> None:
    if not (FRONTEND / "node_modules").exists():
        _fail(
            "前端依赖未安装（缺 node_modules）",
            "在 frontend/ 下执行：npm install",
        )
    _npm_command()
    print("  前端依赖  已安装")


def _wait_http(url: str, timeout: float) -> bool:
    """轮询直到 HTTP 有响应或超时（不依赖 requests，用标准库）。"""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="一键启动投资机会雷达")
    parser.add_argument("--no-front", action="store_true", help="只启动后端")
    args = parser.parse_args()
    with_frontend = not args.no_front

    print("投资机会雷达 · 启动检查")
    _check_python()
    _check_database()
    _check_ports(with_frontend)
    if with_frontend:
        _check_frontend()
    print()

    log_dir = DATA / "cache"
    log_dir.mkdir(parents=True, exist_ok=True)

    processes: list[tuple[str, subprocess.Popen]] = []

    def _spawn(label: str, cmd: list[str], cwd: Path, log_name: str):
        log_path = log_dir / log_name
        handle = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - 子进程生命周期内保持打开
        process = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=handle, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        processes.append((label, process))
        print(f"  启动{label}… 日志：{log_path.relative_to(ROOT)}")

    try:
        _spawn(
            "后端",
            [sys.executable, "-u", "-X", "utf8", "-m", "uvicorn",
             "app.main:app", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)],
            BACKEND, "_uvicorn.log",
        )
        if with_frontend:
            # 用解析后的 npm 路径（Windows 上是 npm.CMD）
            _spawn("前端", [_npm_command(), "run", "dev"], FRONTEND, "_vite.log")

        print()
        if _wait_http(f"http://127.0.0.1:{BACKEND_PORT}/api/health", timeout=30):
            print(f"✓ 后端就绪    http://127.0.0.1:{BACKEND_PORT}/docs")
        else:
            _fail("后端启动超时", f"看日志：{log_dir / '_uvicorn.log'}")

        if with_frontend:
            if _wait_http(f"http://127.0.0.1:{FRONTEND_PORT}/", timeout=40):
                print(f"✓ 前端就绪    http://127.0.0.1:{FRONTEND_PORT}")
            else:
                _fail("前端启动超时", f"看日志：{log_dir / '_vite.log'}")

        print("\n按 Ctrl+C 停止全部服务。\n")
        while True:
            for label, process in processes:
                if process.poll() is not None:
                    _fail(
                        f"{label}进程已退出（返回码 {process.returncode}）",
                        f"看日志：{log_dir}",
                    )
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n正在停止…")
        return 0
    finally:
        for label, process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                print(f"  已停止{label}")
        print("完成。")


if __name__ == "__main__":
    # 收到 SIGTERM 时也走清理路径（例如被脚本或 IDE 停止）
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    raise SystemExit(main())

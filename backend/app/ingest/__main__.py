"""``python -m app.ingest`` 的入口（委托给 pipeline.runner 的 CLI）。"""

from app.pipeline.runner import main

if __name__ == "__main__":
    raise SystemExit(main())

"""配置：``.env`` → ``Settings``。

设计要点（对应 docs/00-决策记录 D07 / D09 / D11 / D12）::

    LLM_MODE=dev        → 抽取层与分析层都走本机 Ollama（零 API 成本）
    LLM_MODE=cloud      → 走云端 API
    LLM_MODE=hybrid     → 抽取层本地、分析层云端

provider 全部预留，切换只改 ``.env``，不改任何业务代码。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py → backend/ → 项目根
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 运行模式 ----------
    llm_mode: str = "dev"                       # dev | cloud | hybrid
    ingest_dry_run: bool = True                 # dry-run 预热期默认开启（D12）

    # ---------- 抽取层（量大、任务简单、要求 JSON 稳定）----------
    extract_provider: str = "ollama"
    extract_model: str = "qwen3:4b"
    extract_base_url: str = "http://127.0.0.1:11434"
    extract_api_key: str = ""

    # ---------- 分析层（量小、需要推理）----------
    analyze_provider: str = "ollama"
    analyze_model: str = "qwen3:4b"
    analyze_base_url: str = "http://127.0.0.1:11434"
    analyze_api_key: str = ""

    # ---------- 备用云端 provider ----------
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # ---------- 数据库 ----------
    database_url: str = "sqlite:///./data/radar.db"

    # ---------- 采集范围（D11：改这一行即可放宽到全市场）----------
    ingest_scope: str = "st_and_risk_warning"   # st_and_risk_warning | all_a_shares
    ingest_lookback_days: int = 90

    # ---------- 调度（D12：交易日感知三段式）----------
    scheduler_enabled: bool = False
    timezone: str = "Asia/Shanghai"
    schedule_premarket: str = "08:30"
    schedule_intraday_minutes: int = 30
    schedule_postmarket: str = "15:30"

    # ---------- 行为开关 ----------
    store_announcement_fulltext: bool = True
    enable_ocr: bool = False

    # ---------- 路径 ----------
    data_dir: Path = Field(default=PROJECT_ROOT / "data")

    # ---------- LLM 调用参数 ----------
    llm_timeout_seconds: float = 300.0
    #: 单条公告送入 LLM 的最大字符数（真实公告可达数万字，必须截断）
    llm_max_input_chars: int = 6000
    #: 截断时优先保留多少条「关键词命中」的段落
    llm_max_paragraphs: int = 14
    #: ★ 单次生成的**最大输出 token 数**。
    #:
    #: 踩过的坑：Ollama 默认不限制输出长度。模型一旦进入**重复生成循环**，
    #: 就会一直输出到上下文上限 —— 实测有一次单条跑了 21 分钟仍未结束，
    #: 而且此时 `/api/ps` 会报告模型未加载（极难排查）。
    #: 这解释了为什么延迟在 73s ~ 450s+ 之间剧烈波动：不是输入大小的问题，
    #: 而是**有些调用陷入了循环**。
    #: 抽取任务的正常输出约 400 token。900 给 2.2x 余量，
    #: 同时把最坏情况从「无上限」压到约 2 分钟（按 ~7 tok/s 估算）。
    #: 注意：**这不是提速手段，而是防跑飞的安全阀** —— 正常调用不会触到它。
    llm_max_output_tokens: int = 900
    llm_max_attempts: int = 3
    llm_temperature: float = 0.0

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def mock_dir(self) -> Path:
        return self.data_dir / "mock"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.cache_dir, self.raw_dir, self.mock_dir):
            d.mkdir(parents=True, exist_ok=True)

    def llm_for(self, layer: str) -> tuple[str, str, str, str]:
        """返回 ``(provider, model, base_url, api_key)``。

        ``layer`` 取 ``"extract"`` 或 ``"analyze"``。
        ``LLM_MODE=hybrid`` 时抽取层强制本地、分析层强制云端。
        """
        if layer == "extract":
            provider, model = self.extract_provider, self.extract_model
            base, key = self.extract_base_url, self.extract_api_key
        elif layer == "analyze":
            provider, model = self.analyze_provider, self.analyze_model
            base, key = self.analyze_base_url, self.analyze_api_key
        else:  # pragma: no cover - 调用方约束
            raise ValueError(f"unknown layer: {layer!r}")

        if self.llm_mode == "dev":
            # 开发期一律走本机 Ollama（D07）
            return (
                os.getenv("RADAR_DEV_PROVIDER", "ollama"),
                os.getenv("RADAR_DEV_MODEL", "qwen3:4b"),
                os.getenv("RADAR_DEV_BASE_URL", "http://127.0.0.1:11434"),
                "",
            )
        if self.llm_mode == "hybrid" and layer == "analyze":
            provider, model = self.analyze_provider, self.analyze_model
            base, key = self.analyze_base_url, self.analyze_api_key
        return provider, model, base, key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

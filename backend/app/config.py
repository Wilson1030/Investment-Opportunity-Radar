"""配置：``.env`` → ``Settings``。

设计要点（对应 docs/00-决策记录 D07 / D09 / D11 / D12）::

    LLM_MODE=dev        → 抽取层与分析层都走本机 Ollama（零 API 成本）
    LLM_MODE=cloud      → 走云端 API
    LLM_MODE=hybrid     → 抽取层本地、分析层云端

provider 全部预留，切换只改 ``.env``，不改任何业务代码。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from typing import ClassVar

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
    #: cninfo 全文检索关键词（留空 = 扫全市场；填则只扫命中该词的公告）
    ingest_searchkey: str = ""

    # ---------- 调度（D12：交易日感知三段式）----------
    scheduler_enabled: bool = False
    timezone: str = "Asia/Shanghai"
    schedule_premarket: str = "08:30"
    schedule_intraday_minutes: int = 30
    schedule_postmarket: str = "15:30"

    # ---------- 行为开关 ----------
    #: 是否对入池机会跑 AI 分析（反证检索 / 研究叙事 / 语义分）
    with_ai_analysis: bool = True
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
    #: 单次生成的**最大输出 token 数**（防重复生成循环的安全阀）。
    #:
    #: 踩过两次坑，两次都值得记：
    #:   1. **不设上限** → 模型重复生成时一路输出到上下文上限，单条可跑 21 分钟
    #:   2. **上限设太小（900）** → 正常输出被**截断**，JSON 不完整，
    #:      实测 5/5 抽取全部 schema 失败（`schema_failure_rate = 1.0`）
    #:
    #: 2500 的依据：实测正常输出 286~593 token，但 `relevant_text` 现在引用的是
    #: 完整段落（段落合并修复后变长了），输出可达 1000+。
    #: 2500 既留足余量，又把最坏情况压在约 6 分钟（按 ~7 tok/s）。
    llm_max_output_tokens: int = 2500
    #: 是否关闭抽取层的「思考模式」。
    #:
    #: ★★ 这是一个**性能 bug 的根因**：qwen3 是**思考型模型**，推理 token 也计入
    #: `num_predict`。实测加上输出上限后 `raw_output` 长度为 **0**、
    #: `done_reason = length` —— 2500 token 全被「思考」吃掉，轮不到输出 JSON，
    #: 表现为「抽取未通过 Schema 校验」（5/5 全失败），极易误判为模型能力问题。
    #:
    #: 抽取是**结构化转换**，不是推理任务 —— 关掉思考既修掉这个 bug，
    #: 又省下此前一直在白付的推理 token 成本（预期显著提速）。
    llm_disable_thinking: bool = True
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

    #: 本机 Ollama 的默认端点与模型（LLM_MODE=dev 且未显式配置时使用）
    #: 必须是 ClassVar —— 否则 pydantic 会把它当成模型字段并要求类型注解
    DEFAULT_LOCAL_PROVIDER: ClassVar[str] = "ollama"
    DEFAULT_LOCAL_MODEL: ClassVar[str] = "qwen3:4b"
    DEFAULT_LOCAL_BASE_URL: ClassVar[str] = "http://127.0.0.1:11434"

    def llm_for(self, layer: str) -> tuple[str, str, str, str]:
        """返回 ``(provider, model, base_url, api_key)``。

        ``layer`` 取 ``"extract"`` 或 ``"analyze"``。

        语义（对 GitHub 用户很重要）::

            LLM_MODE=dev     未显式配置 provider 时 → 本机 Ollama（零成本、内容不出本机）
                             但**若用户显式写了** EXTRACT_PROVIDER=deepseek，就尊重用户配置
            LLM_MODE=cloud   一律用各层自己的配置
            LLM_MODE=hybrid  抽取层用本地（成本、隐私），分析层用配置里的（质量）

        ★ 踩过的坑：早期实现里 ``dev`` 会**无条件覆盖**成 Ollama，
        于是用户按文档设了 ``EXTRACT_PROVIDER=deepseek`` 却仍然走本机 ——
        属于「配置被静默忽略」，极难排查。
        """
        if layer == "extract":
            provider, model = self.extract_provider, self.extract_model
            base, key = self.extract_base_url, self.extract_api_key
        elif layer == "analyze":
            provider, model = self.analyze_provider, self.analyze_model
            base, key = self.analyze_base_url, self.analyze_api_key
        else:  # pragma: no cover - 调用方约束
            raise ValueError(f"unknown layer: {layer!r}")

        explicit = bool(provider) and provider != self.DEFAULT_LOCAL_PROVIDER
        if explicit:
            # 用户显式选了别的 provider → 尊重（dev / cloud / hybrid 都一样）
            return provider, model, base, key

        if self.llm_mode in {"dev", "hybrid"}:
            # 未显式配置 → 本机 Ollama
            return (
                self.DEFAULT_LOCAL_PROVIDER,
                model or self.DEFAULT_LOCAL_MODEL,
                base or self.DEFAULT_LOCAL_BASE_URL,
                "",
            )
        return provider, model, base, key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

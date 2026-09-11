"""漏斗编排与计数（规格 §27 / docs/03 §3、§8）。

漏斗必须**可观测**，否则无法回答「为什么今天只有 3 张卡」。

    原始数据 → 候选池 ~300 → 规则预筛 → 事件抽取 → Thesis 归类 → 深度分析 → 展示 5~10
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class FunnelCounters:
    """六级漏斗计数（写入 ``IngestRun.funnel``）。"""

    candidates: int = 0                # Stage 1 候选池公司数
    announcements_fetched: int = 0      # 抓到的公告条数（候选池内）
    announcements_skipped: int = 0      # 幂等跳过的条数
    passed_prefilter: int = 0           # 通过关键词白名单，进入 LLM
    events_extracted: int = 0           # LLM 成功抽出并落库的事件数
    thesis_candidates: int = 0          # 命中至少一个策略的机会候选数
    deep_analyzed: int = 0              # 进入 LLM 深度分析的机会数
    cards: int = 0                      # 最终展示的机会卡数

    def to_dict(self) -> dict:
        return asdict(self)

    def record(self, stage: str, value: int) -> None:
        if not hasattr(self, stage):
            raise KeyError(f"未知漏斗阶段：{stage}")
        setattr(self, stage, value)

    def increment(self, stage: str, delta: int = 1) -> None:
        self.record(stage, getattr(self, stage) + delta)

    @property
    def stages(self) -> tuple[tuple[str, int], ...]:
        return tuple(asdict(self).items())

    def drop_at(self) -> str | None:
        """返回掉得最狠的一级 —— 直接回答「今天为什么只有 N 张卡」。"""
        stages = self.stages
        worst: tuple[str, float] | None = None
        for (name_a, value_a), (name_b, value_b) in zip(stages, stages[1:]):
            if value_a <= 0:
                continue
            kept = value_b / value_a
            if worst is None or kept < worst[1]:
                worst = (f"{name_a} → {name_b}", kept)
        return worst[0] if worst else None


@dataclass
class QualityMetrics:
    """数据质量指标（写入 ``IngestRun.quality``，对应风险 R2 / R3）。"""

    parse_failure_rate: float = 0.0
    field_missing_rate: float = 0.0
    llm_schema_failure_rate: float = 0.0
    llm_cached_rate: float = 0.0
    evidence_rejected: int = 0
    evidence_rejected_reasons: dict[str, int] = field(default_factory=dict)
    needs_ocr: int = 0

    #: R3 判据阈值（docs/07 §5.2）
    SCHEMA_FAILURE_OK = 0.05
    SCHEMA_FAILURE_TOLERABLE = 0.20

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def extractor_verdict(self) -> str:
        """本机模型能否胜任抽取任务（docs/07 §5.2 的三段判定）。"""
        rate = self.llm_schema_failure_rate
        if rate < self.SCHEMA_FAILURE_OK:
            return "可继续使用本地模型（零 API 成本）"
        if rate < self.SCHEMA_FAILURE_TOLERABLE:
            return "需要优化 prompt（补 JSON Schema 示例 + few-shot）后再观察"
        return "应切换云端抽取（改 EXTRACT_PROVIDER，两行 .env）"

    @property
    def hallucination_signal(self) -> str | None:
        """最危险的信号：证据被大规模拒收（产生「看似合理但无法核对」的证据）。"""
        reasons = self.evidence_rejected_reasons
        if reasons.get("文本不一致", 0) > 0:
            return "⚠ 存在「文本不一致」拒收 —— 模型在改写原文，属严重问题，须换模型或改 prompt"
        if reasons.get("段落不存在", 0) > 0:
            return "⚠ 存在「段落不存在」拒收 —— 模型在编造页码，属严重问题"
        if self.evidence_rejected > 0 and reasons.get("片段过短", 0) == self.evidence_rejected:
            return "仅在片段长度上被拒，风险可控"
        if self.evidence_rejected > 0:
            return "存在证据拒收，需逐条归因"
        return None


@dataclass
class PipelineReport:
    """一次 pipeline 运行的可观测输出（docs/03 §8）。"""

    run_id: int | None = None
    scope: str = "st_and_risk_warning"
    dry_run: bool = True
    stage_name: str = "incremental"
    funnel: FunnelCounters = field(default_factory=FunnelCounters)
    quality: QualityMetrics = field(default_factory=QualityMetrics)
    errors: list[dict] = field(default_factory=list)
    total_ms: int = 0
    llm_ms: int = 0

    def add_error(self, stage: str, error: str, **extra) -> None:
        self.errors.append({"stage": stage, "error": error, **extra})

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "scope": self.scope,
            "dry_run": self.dry_run,
            "stage": self.stage_name,
            "funnel": self.funnel.to_dict(),
            "quality": self.quality.to_dict(),
            "timing": {"total_ms": self.total_ms, "llm_ms": self.llm_ms},
            "errors": self.errors,
            "hint": self.funnel.drop_at(),
        }


__all__ = ["FunnelCounters", "PipelineReport", "QualityMetrics"]

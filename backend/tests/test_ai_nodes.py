"""AI 节点链路：禁用词 / Schema 校验重试 / 缓存 / 语义分边界。

用 ``ScriptedProvider`` 驱动，**不需要启动 Ollama、不花一分钱**，
让「节点 + 校验 + 重试 + 缓存 + 审计」这条链路可以被完整验证。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.ai.cache import InMemoryNodeCache, compute_input_hash
from app.ai.nodes import (
    ALL_NODES,
    ANALYZE,
    ANALYZE_NODES,
    CLASSIFY_THESIS,
    EXTRACT_EVENT,
    EXTRACT_NODES,
    HUNT_RISK,
    NODE_BY_NAME,
    SCORE_SEMANTIC,
)
from app.ai.nodes.score_semantic import RULE_COVERED_FACTORS
from app.ai.provider import ScriptedProvider, build_provider
from app.ai.runner import NodeRunner, extract_json
from app.engine import guard
from app.models.enums import NodeRunStatus, ThesisType

T0 = datetime(2026, 9, 8, 11, 32, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 节点契约
# --------------------------------------------------------------------------- #
def test_five_nodes_with_declared_layers():
    assert len(ALL_NODES) == 5
    assert {n.name for n in ALL_NODES} == {
        "extract_event", "classify_thesis", "hunt_risk", "analyze", "score_semantic",
    }
    assert {n.name for n in EXTRACT_NODES} == {"extract_event", "classify_thesis"}
    assert {n.name for n in ANALYZE_NODES} == {"hunt_risk", "analyze", "score_semantic"}
    assert NODE_BY_NAME["extract_event"] is EXTRACT_EVENT


@pytest.mark.parametrize("node", ALL_NODES, ids=lambda n: n.name)
def test_node_prompts_state_the_hard_constraints(node):
    system = node.system_prompt()
    assert "只输出一个 JSON 对象" in system
    assert "一定会上涨" in system, "必须显式列出禁用词"
    assert "不得改写" in system or "逐字片段" in system, "必须有证据原文约束"
    assert node.prompt_version


def test_hunt_risk_prompt_requires_contradictory_field():
    system = HUNT_RISK.system_prompt()
    assert "contradictory_evidence" in system
    assert "必须存在" in system
    assert "no_contradiction_statement" in system


def test_analyze_prompt_requires_why_now_structure():
    system = ANALYZE.system_prompt()
    assert "过去" in system and "最近" in system and "本周" in system


def test_score_semantic_prompt_lists_rule_covered_factors():
    """★ 防止同一因素被计两次：prompt 里必须明确列出规则已算过什么。"""
    system = SCORE_SEMANTIC.system_prompt()
    for factor in RULE_COVERED_FACTORS:
        assert factor in system, f"prompt 未声明规则已覆盖：{factor}"
    assert "rules_already_covered" in system
    assert "{covered}" not in system, "占位符必须已渲染"


# --------------------------------------------------------------------------- #
# JSON 提取
# --------------------------------------------------------------------------- #
def test_extract_json_handles_fences_and_noise():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('好的，结果如下：\n{"a": 1}\n以上。') == {"a": 1}
    assert extract_json('{"a": {"b": 2}}') == {"a": {"b": 2}}


def test_extract_json_does_not_repair_broken_output():
    """不做「修复性猜测」—— 那会把不合规输出洗成看似合规。"""
    with pytest.raises(ValueError):
        extract_json('{"a": 1')
    with pytest.raises(ValueError):
        extract_json("")
    with pytest.raises(ValueError):
        extract_json("完全不是 JSON")


# --------------------------------------------------------------------------- #
# 禁用词（M11-02）
# --------------------------------------------------------------------------- #
def test_ban_words_detected_with_excerpt():
    report = guard.find_banned_words("该标的重组一定成功，现在值得买入。")
    assert report.hit
    assert "一定成功" in report.words
    assert "值得买" in report.words or "建议买入" in report.words
    assert report.excerpts


def test_allowed_phrasings_are_not_banned():
    text = (
        "存在重组预期。目前证据支持该判断，与该投资 Thesis 高度匹配，"
        "但交易标的尚未确认，值得进一步确认。若出现终止公告，则该 Thesis 可能失效。"
    )
    assert not guard.find_banned_words(text).hit


def test_runner_regenerates_on_banned_words():
    """命中禁用词 → 重新生成；仍命中则记为 banned_word 且不落库。"""
    provider = ScriptedProvider(responses=[
        "这只股票一定会上涨，值得买入。",
        "这只股票一定会上涨。",
        "这只股票一定会上涨。",
    ])
    runner = NodeRunner(
        provider=provider, cache=InMemoryNodeCache(), model="qwen3:4b",
        max_attempts=3, ban_word_retries=2,
    )
    payload = _extract_payload()
    result = runner.run(EXTRACT_EVENT, payload)
    assert result.status is NodeRunStatus.BANNED_WORD
    assert result.output is None
    assert result.banned_words or "禁用词" in (result.error or "")
    assert len(provider.calls) == 3


# --------------------------------------------------------------------------- #
# Schema 校验与重试
# --------------------------------------------------------------------------- #
def _extract_payload():
    from app.ai.schemas import AnnouncementInput, CompanyInput, ExtractEventInput, ParagraphInput

    return ExtractEventInput(
        company=CompanyInput(name="ST XXX", code="600xxx", is_st=True),
        announcement=AnnouncementInput(
            document_id="ANN-1", title="重大资产重组预案公告", publication_time=T0
        ),
        paragraphs=[ParagraphInput(page=2, para_index=7, text="公司拟以发行股份方式购买 XX 资产")],
    )


def _valid_extract_output() -> dict:
    return {
        "event_type": "RESTRUCTURING",
        "title": "重大资产重组预案公告",
        "summary": "公司披露重组预案，交易标的与对价尚未确定。",
        "event_time": None,
        "importance": 0.9,
        "certainty": 0.85,
        "certainty_level": "disclosed",
        "affected_thesis": ["restructuring"],
        "extracted_facts": [{"statement": "公司拟发行股份购买资产", "assertion_kind": "fact"}],
        "evidence_slices": [
            {"page": 2, "para_index": 7, "relevant_text": "公司拟以发行股份方式购买 XX 资产"}
        ],
        "not_mentioned": ["交易价格"],
        "counterparty_known": False,
        "amount_ratio": 0.0,
    }


def test_runner_recovers_from_schema_error_then_succeeds():
    """把校验错误回灌给模型 —— 这是让本机 4B 模型可用的关键手段。"""
    provider = ScriptedProvider(responses=[
        '{"event_type": "NOT_A_TYPE", "title": "x"}',
        json.dumps(_valid_extract_output(), ensure_ascii=False),
    ])
    runner = NodeRunner(provider=provider, cache=InMemoryNodeCache(), model="qwen3:4b")
    result = runner.run(EXTRACT_EVENT, _extract_payload())

    assert result.ok
    assert result.attempts == 2
    assert result.output is not None
    assert result.output.event_type.value == "RESTRUCTURING"
    # 第二次请求里必须带上错误反馈
    assert "不合规" in provider.calls[1][1]


def test_runner_gives_up_after_max_attempts_and_does_not_persist_output():
    provider = ScriptedProvider(default="这不是 JSON")
    runner = NodeRunner(
        provider=provider, cache=InMemoryNodeCache(), model="qwen3:4b", max_attempts=3
    )
    result = runner.run(EXTRACT_EVENT, _extract_payload())
    assert result.status is NodeRunStatus.SCHEMA_ERROR
    assert result.output is None
    assert len(provider.calls) == 3


# --------------------------------------------------------------------------- #
# 缓存（M11-06 / M11-07）
# --------------------------------------------------------------------------- #
def test_cache_hit_avoids_second_llm_call():
    provider = ScriptedProvider(responses=[json.dumps(_valid_extract_output(), ensure_ascii=False)])
    cache = InMemoryNodeCache()
    runner = NodeRunner(provider=provider, cache=cache, model="qwen3:4b")
    payload = _extract_payload()

    first = runner.run(EXTRACT_EVENT, payload)
    second = runner.run(EXTRACT_EVENT, payload)

    assert first.status is NodeRunStatus.OK
    assert second.status is NodeRunStatus.CACHED
    assert second.from_cache
    assert len(provider.calls) == 1, "缓存命中不得再调用 LLM（直接决定 API 账单）"


def test_cache_key_includes_prompt_version():
    """prompt_version 变更 → 缓存自然失效。"""
    provider = ScriptedProvider(responses=[
        json.dumps(_valid_extract_output(), ensure_ascii=False),
        json.dumps(_valid_extract_output(), ensure_ascii=False),
    ])
    cache = InMemoryNodeCache()
    runner = NodeRunner(provider=provider, cache=cache, model="qwen3:4b")
    payload = _extract_payload()

    runner.run(EXTRACT_EVENT, payload)

    class Bumped(EXTRACT_EVENT.__class__):  # type: ignore[misc]
        prompt_version = "v999"

    bumped = Bumped()
    bumped.name = EXTRACT_EVENT.name
    bumped.layer = EXTRACT_EVENT.layer
    runner.run(bumped, payload)
    assert len(provider.calls) == 2


def test_input_hash_is_stable_and_order_independent():
    a = compute_input_hash({"x": 1, "y": [1, 2]})
    b = compute_input_hash({"y": [1, 2], "x": 1})
    assert a == b
    assert a != compute_input_hash({"x": 2, "y": [1, 2]})


def test_runner_stats_exposes_schema_failure_rate():
    """这是判断本机 4B 模型能否胜任抽取任务的直接依据（R3）。"""
    provider = ScriptedProvider(responses=[
        json.dumps(_valid_extract_output(), ensure_ascii=False),
        "坏输出",
        "坏输出",
        "坏输出",
    ])
    runner = NodeRunner(
        provider=provider, cache=InMemoryNodeCache(), model="qwen3:4b", max_attempts=3
    )
    runner.run(EXTRACT_EVENT, _extract_payload())
    runner.run(EXTRACT_EVENT, _classify_payload())

    stats = runner.stats
    assert stats["total"] == 2
    assert stats["ok"] == 1
    assert stats["schema_error"] == 1
    assert stats["schema_failure_rate"] == pytest.approx(0.5)


def _classify_payload():
    from app.ai.schemas import ClassifyThesisInput, ExtractEventInput, ExtractEventOutput

    return ClassifyThesisInput(
        event=ExtractEventOutput(
            event_type="RESTRUCTURING", title="重组预案公告", summary="……"
        ),
        candidate_thesis_defs=[],
    )


# --------------------------------------------------------------------------- #
# provider 工厂
# --------------------------------------------------------------------------- #
def test_provider_factory_supports_all_reserved_providers():
    assert build_provider("ollama").name == "ollama"
    assert build_provider("deepseek", api_key="sk-x").name == "deepseek"
    assert build_provider("openai", api_key="sk-x").name == "openai"
    assert build_provider("kimi", api_key="sk-x").name == "kimi"
    assert build_provider("glm", api_key="sk-x").name == "glm"
    assert build_provider("scripted").name == "scripted"


def test_provider_factory_rejects_unknown():
    from app.ai.provider import LlmError

    with pytest.raises(LlmError):
        build_provider("some-new-llm")


def test_llm_mode_dev_routes_to_local_ollama():
    from app.config import settings

    provider, model, base_url, _ = settings.llm_for("analyze")
    assert settings.llm_mode == "dev"
    assert provider == "ollama"
    assert "127.0.0.1" in base_url
    assert model


def test_semantic_node_layer_is_analyze():
    assert SCORE_SEMANTIC.layer == "analyze"
    assert CLASSIFY_THESIS.layer == "extract"
    assert SCORE_SEMANTIC.Input.model_fields["thesis_type"].annotation is ThesisType

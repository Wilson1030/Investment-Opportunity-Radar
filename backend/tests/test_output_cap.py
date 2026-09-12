"""输出长度上限与「截断」的可诊断性。

**两次踩坑，两次都值得记：**

1. **不设上限** → 模型重复生成时一路输出到上下文上限。实测单条跑了 **21 分钟**
   仍未结束，而且此时 ``/api/ps`` 会报告「模型未加载」，极难排查。
   这解释了单条延迟在 73 s ~ 21 min 之间剧烈波动的全部异常。

2. **上限设太小（900）** → 正常输出被**截断**，JSON 不完整，
   实测 5/5 抽取全部 schema 失败（``schema_failure_rate = 1.0``）。

第 2 条的教训尤其重要：我当时只写了「上限确实传进了请求体」的单元测试
（用假 client 断言 payload），**从未验证「真实抽取仍然成功」** ——
于是把一个能用的功能改坏了。验证机制 ≠ 验证结果。
"""

from __future__ import annotations

from app.ai.provider import LlmRequest, LlmResponse, OllamaProvider, OpenAiCompatProvider
from app.config import settings


def test_output_cap_is_bounded_but_generous():
    """上限必须**有界**（防跑飞）且**够大**（不截断正常输出）。"""
    request = LlmRequest(prompt="x")
    assert request.max_output_tokens > 0, "必须有上限，否则模型循环时可跑到上下文上限"
    assert request.max_output_tokens == settings.llm_max_output_tokens
    # 实测正常输出 286~593 token；段落合并修复后 relevant_text 变长，可达 1000+
    assert 1500 <= request.max_output_tokens <= 8000, (
        f"上限 {request.max_output_tokens} 不可接受："
        "太小会截断 JSON（900 时实测 5/5 schema 失败），太大则失去防跑飞的意义"
    )


def test_truncated_is_distinguishable_from_bad_output():
    """★ 「被上限截断」与「模型输出错」处置完全不同，必须能区分。

    两者都表现为 schema 校验失败：
      · 截断  → 调大 ``llm_max_output_tokens``
      · 模型错 → 换模型 / 改 prompt

    只看 schema 错误会误判为「模型能力不行」。
    """
    assert LlmResponse(text="x", provider="p", model="m").truncated is False
    assert LlmResponse(text="x", provider="p", model="m", truncated=True).truncated is True


def _fake_httpx_client(response_json: dict, captured: dict):
    import httpx

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return response_json

    class _Client:
        def __init__(self, *args, **kwargs) -> None: ...

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    return httpx, _Client


def test_ollama_payload_and_done_reason_length():
    import httpx

    captured: dict = {}
    original, fake = _fake_httpx_client(
        {"message": {"content": '{"a":'}, "done_reason": "length"}, captured
    )
    httpx.Client = fake  # type: ignore[misc]
    try:
        response = OllamaProvider(base_url="http://x").complete(
            "qwen3:4b", LlmRequest(prompt="p", max_output_tokens=2500)
        )
    finally:
        httpx.Client = original  # type: ignore[misc]

    options = captured["json"]["options"]
    assert options["num_predict"] == 2500, "上限必须真的传进请求体"
    assert options["temperature"] == 0.0
    assert captured["json"]["format"] == "json"
    assert response.truncated is True, "done_reason=length 必须被识别为截断"


def test_ollama_normal_finish_is_not_truncated():
    import httpx

    captured: dict = {}
    original, fake = _fake_httpx_client(
        {"message": {"content": "{}"}, "done_reason": "stop"}, captured
    )
    httpx.Client = fake  # type: ignore[misc]
    try:
        response = OllamaProvider(base_url="http://x").complete(
            "qwen3:4b", LlmRequest(prompt="p")
        )
    finally:
        httpx.Client = original  # type: ignore[misc]

    assert response.truncated is False


def test_openai_payload_carries_max_tokens():
    import httpx

    captured: dict = {}
    original, fake = _fake_httpx_client(
        {"choices": [{"message": {"content": "{}"}}], "usage": {}}, captured
    )
    httpx.Client = fake  # type: ignore[misc]
    try:
        OpenAiCompatProvider(base_url="http://x", api_key="k").complete(
            "m", LlmRequest(prompt="p", max_output_tokens=2500)
        )
    finally:
        httpx.Client = original  # type: ignore[misc]

    assert captured["json"]["max_tokens"] == 2500


def test_runner_mentions_truncation_in_error():
    """截断导致的 schema 失败，错误信息必须**指向上限**而不是笼统的 schema 错误。"""
    import inspect

    from app.ai import runner as runner_module

    source = inspect.getsource(runner_module.NodeRunner.run)
    assert "truncated" in source, "runner 必须识别 truncated 并给出可诊断的错误"
    assert "llm_max_output_tokens" in source, "错误信息应指向要调的配置项"

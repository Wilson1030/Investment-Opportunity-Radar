"""真实 Ollama 冒烟测试（风险 R3 的判据工具，docs/07 §5.2）。

用途：在真实公告文本上跑一次 ``extract_event``，测量
**JSON 合规率 / 耗时 / 证据片段是否忠实** —— 这三项决定本机 4B 模型能否胜任抽取任务。

用法::

    conda activate radar
    cd backend
    python ../tools/smoke_ollama.py            # 默认 qwen3:4b
    python ../tools/smoke_ollama.py qwen3-finance:8b

注意：这是**只读诊断**，不写任何数据库。输出含一组人工核对提示。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "backend"))

from app.ai.cache import InMemoryNodeCache  # noqa: E402
from app.ai.nodes import EXTRACT_EVENT  # noqa: E402
from app.ai.provider import OllamaProvider  # noqa: E402
from app.ai.runner import NodeRunner  # noqa: E402
from app.ai.schemas import (  # noqa: E402
    AnnouncementInput,
    CompanyInput,
    ExtractEventInput,
    ParagraphInput,
)
from app.engine import classifier, guard  # noqa: E402

#: 一段合成的 ST 公司重组公告（结构与真实公告一致：证券代码 / 标题 / 分节 / 风险提示）
SAMPLE_TITLE = "关于重大资产重组预案暨控股股东变更的公告"
SAMPLE_TEXT = """
证券代码：600xxx  证券简称：ST XXX  公告编号：2026-088
关于重大资产重组预案暨控股股东变更的公告
本公司及董事会全体成员保证信息披露内容的真实、准确、完整，没有虚假记载、误导性陈述或重大遗漏。
一、本次交易概述
公司拟以发行股份及支付现金方式购买 XX 新能源科技有限公司 100% 股权。截至本公告日，
标的资产的审计、评估工作尚未完成，交易作价尚未最终确定。
二、控股股东变更
公司控股股东 XX 控股拟以协议转让方式向 YY 产业集团转让其所持公司全部股份，转让完成后
公司实际控制人将发生变更。本次协议转让尚需取得有权国资主管部门批准。
三、风险提示
本次交易尚需提交公司股东大会审议并经监管机构审核，能否获得批准及最终获批时间均存在不确定性。
公司 2024 年度、2025 年度连续两年亏损，2025 年末归属于上市公司股东的净资产为负。
"""


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3:4b"
    paragraphs = [line.strip() for line in SAMPLE_TEXT.strip().split("\n") if len(line.strip()) >= 12]

    payload = ExtractEventInput(
        company=CompanyInput(name="ST XXX", code="600xxx", is_st=True),
        announcement=AnnouncementInput(
            document_id="SMOKE-001",
            title=SAMPLE_TITLE,
            publication_time=datetime(2026, 9, 8, 11, 32, tzinfo=timezone.utc),
        ),
        paragraphs=[
            ParagraphInput(page=1, para_index=i + 1, text=text)
            for i, text in enumerate(paragraphs)
        ],
    )

    print(f"模型：{model} · provider：{OllamaProvider().base_url}")
    print(f"输入：{len(paragraphs)} 个段落 / {len(SAMPLE_TEXT)} 字符\n")

    # 规则层先跑一遍（用于对比「规则分类」与「LLM 归类」是否一致）
    rule_type = classifier.classify_announcement(SAMPLE_TITLE)
    print(f"[规则层] 关键词白名单归类：{rule_type.value if rule_type else None}")
    print(f"[规则层] 命中关键词：{json.dumps(classifier.matched_keywords(SAMPLE_TITLE), ensure_ascii=False)}")

    runner = NodeRunner(
        provider=OllamaProvider(),
        cache=InMemoryNodeCache(),
        model=model,
        max_attempts=3,
        timeout_seconds=240.0,
    )
    started = time.time()
    result = runner.run(EXTRACT_EVENT, payload)
    elapsed = time.time() - started

    print(f"\n[LLM 层] 耗时 {elapsed:.1f}s · 状态 {result.status.value} · 尝试 {result.attempts} 次")
    stats = runner.stats
    print(f"[LLM 层] 节点统计：{json.dumps(stats, ensure_ascii=False)}")

    if not result.ok:
        print(f"\n✗ 抽取失败：{result.error}")
        print("  → 判据（docs/07 §5.2）：schema_failure_rate ≥ 0.20 时应切换云端抽取")
        return 1

    output = result.output
    assert output is not None

    print("\n=== 抽取结果 ===")
    print(f"event_type      : {output.event_type.value}")
    print(f"规则层 vs LLM   : {rule_type.value if rule_type else None} vs {output.event_type.value} "
          f"{'（一致）' if rule_type and rule_type.value == output.event_type.value else '（不一致，需人工核对）'}")
    print(f"title           : {output.title}")
    print(f"importance      : {output.importance:.2f}   certainty: {output.certainty:.2f} "
          f"({output.certainty_level.value})")
    print(f"affected_thesis : {[t.value for t in output.affected_thesis]}")
    print(f"event_time      : {output.event_time}  ← 未披露时必须为 null（规格 §40）")
    print(f"not_mentioned   : {output.not_mentioned}")
    print(f"amount_ratio    : {output.amount_ratio:.2f}   counterparty_known: {output.counterparty_known}")

    print(f"\n=== 证据片段（{len(output.evidence_slices)} 条）—— 逐条过闸门 ===")
    lookup = {(p.page, p.para_index): p.text for p in payload.paragraphs}
    slices = [
        guard.EvidenceSlice(page=s.page, para_index=s.para_index, relevant_text=s.relevant_text)
        for s in output.evidence_slices
    ]
    decision = guard.gate_evidence_slices(slices, lambda pg, pi: lookup.get((pg, pi)))
    for item in decision.accepted:
        print(f"  ✓ ({item.page}, {item.para_index}) {item.relevant_text[:46]}…")
    for item, reason in decision.rejected:
        print(f"  ✗ ({item.page}, {item.para_index}) {reason}")

    print(f"\n闸门：接受 {len(decision.accepted)} / 拒绝 {len(decision.rejected)}")
    if decision.rejection_reasons:
        print(f"拒绝原因：{json.dumps(decision.rejection_reasons, ensure_ascii=False)}")

    print("\n=== 单条耗时外推（用于判断一晚能否跑完）===")
    per_call = elapsed / max(1, result.attempts)
    for count in (30, 50, 80):
        hours = per_call * count / 3600
        print(f"  {count:>3} 条 → {hours:.1f} 小时（串行，无缓存命中）")
    print("\n提示：抽取结果会被节点级缓存，重复文档不重跑；"
          "若外推超过 6 小时，按 docs/07 §5.1 降采样或切云端抽取。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

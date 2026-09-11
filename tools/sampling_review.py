"""人工抽样核对表生成器（docs/07 §5.3 / §6 的 `--live` 前置条件）。

**为什么需要它**：`--live` 之前的门槛是「人工抽样 20 条，抽取准确率 ≥ 80%」。
这件事只有人能判断，但材料该由机器准备 —— 本工具把
「公告标题 + 规则层判定 + LLM 判定 + 支撑证据的**原文片段**」整理成可打勾的核对表。

用法::

    # 默认：cninfo 全文检索「重大资产重组」，抽 15 条（约 15~18 分钟，dry-run 不写判断数据）
    python tools/sampling_review.py --n 15

    # 换检索词 / 换模型批次
    python tools/sampling_review.py --n 15 --searchkey "控股股东变更"
    python tools/sampling_review.py --n 3            # 先小样本验证工具本身

产出：``data/cache/sampling/review_<时间戳>.md``（人看）与 ``.json``（机器读）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.pipeline.runner import PipelineOptions, run_pipeline  # noqa: E402


def _fmt_types(items: list[str]) -> str:
    return "、".join(items) if items else "—"


def build_sheet(payload: dict, *, searchkey: str, model: str, seconds: float) -> str:
    extractions = payload.get("extractions", [])
    funnel = payload.get("funnel", {})
    quality = payload.get("quality", {})
    llm = payload.get("llm", {})

    agree = sum(1 for e in extractions if e.get("agreement"))
    total = len(extractions)
    agreement_rate = (agree / total * 100) if total else 0.0
    gate_passed = sum(1 for e in extractions if e.get("gate_passed"))
    rejected_total = sum(len(e.get("rejected", [])) for e in extractions)

    lines: list[str] = []
    lines.append("# 抽取质量人工抽样核对表")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append(f"- 模型：`{model}`（本机 Ollama）")
    lines.append(f"- 检索口径：cninfo 全文检索「{searchkey}」")
    lines.append(f"- 样本数：**{total}**（dry-run，未写判断类数据）")
    lines.append(f"- 耗时：{seconds / 60:.1f} 分钟")
    lines.append("")

    lines.append("## 汇总")
    lines.append("")
    lines.append("| 指标 | 值 | 判据 |")
    lines.append("|---|---|---|")
    lines.append(f"| 规则层与 LLM 判定**一致率** | {agree}/{total} = **{agreement_rate:.0f}%** | — |")
    lines.append(
        f"| 抽取后通过证据闸门 | {gate_passed}/{total} | 全部通过为佳 |"
    )
    lines.append(f"| 被拒证据片 | {rejected_total} 片 | 「文本不一致」出现即为严重问题 |")
    lines.append(f"| `llm_schema_failure_rate` | {llm.get('schema_failure_rate', 0):.2f} | < 0.05 优秀；> 0.20 换云端 |")
    lines.append(f"| `llm.calls` | {llm.get('calls', 0)} | 实际调用次数（缓存命中不计） |")
    lines.append(f"| 解析失败率 | {quality.get('parse_failure_rate', 0):.2f} | < 0.15 |")
    lines.append(f"| 模型判定 | {quality.get('extractor_verdict', '—')} | |")
    if quality.get("hallucination_signal"):
        lines.append(f"| ⚠ 幻觉信号 | {quality['hallucination_signal']} | **需立即处理** |")
    lines.append("")

    lines.append("## 怎么核对（3 步）")
    lines.append("")
    lines.append("1. 读**公告标题**，判断这份公告**实际属于**哪类事件")
    lines.append("2. 对比「规则层」与「LLM」两个判定 —— 不一致的优先看")
    lines.append("3. 在「你的判断」栏打勾；判错的写下**应该是什么**")
    lines.append("")
    lines.append(
        "> 判定标准：LLM 只被允许从 17 类事件里选一个。判断它对不对，看它选的那类"
        "**能不能从原文直接读出来** —— 而不是「靠推理说得通」。"
    )
    lines.append("")
    lines.append(
        "> 已知反例（上次实测发现）：**定向增发的问询函回复**被判成 `RESTRUCTURING`。"
        "定增不是重大资产重组。核对时请特别留意这类。"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    for index, item in enumerate(extractions, start=1):
        title = item.get("announcement_title", "")
        rule_type = item.get("rule_event_type", "")
        llm_type = item.get("event_type", "")
        agree_mark = "✓ 一致" if item.get("agreement") else "✗ **不一致**"

        lines.append(f"## {index:02d} · {item.get('company_code', '')}")
        lines.append("")
        lines.append(f"**公告**：{title}")
        lines.append("")
        lines.append(f"**规则层**：`{rule_type}`　　**LLM**：`{llm_type}`　　{agree_mark}")
        lines.append("")

        if not item.get("gate_passed"):
            lines.append("> ⚠ 证据全部被拒 → 该事件未落库（没有证据的判断不写进系统）")
            lines.append("")

        texts = item.get("accepted_texts", [])
        if texts:
            lines.append(f"**LLM 引用的原文**（{len(texts)} 片，均已通过子串校验）：")
            lines.append("")
            for slice_item in texts:
                text = slice_item.get("text", "")
                lines.append(
                    f"- `p{slice_item.get('page')}¶{slice_item.get('para_index')}` "
                    f"「{text[:160]}{'…' if len(text) > 160 else ''}」"
                )
            lines.append("")

        rejected = item.get("rejected", [])
        if rejected:
            lines.append(f"**被拒**（{len(rejected)} 片）：")
            lines.append("")
            for item_rejected in rejected:
                lines.append(f"- `{item_rejected.get('at')}` {item_rejected.get('reason')}")
            lines.append("")

        lines.append(f"**event_time 来源**：`{item.get('event_time_source')}`"
                     f"（`publication_time` = 公告未写明，回退为发布时间）")
        lines.append("")
        lines.append("**你的判断**：[ ] 正确　　[ ] 错误 → 应为 `__________`")
        lines.append("")
        lines.append("**备注**：")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## 结论（核对完填写）")
    lines.append("")
    lines.append("- 准确率：____ / %d = ____%%　（门槛 ≥ 80%%）" % total)
    lines.append("- 主要错误类型：")
    lines.append("- 结论：[ ] 可以 `--live`　　[ ] 先改 prompt 重抽　　[ ] 换云端模型")
    lines.append("")

    # 漏斗附在后面，便于判断「为什么样本这么少」
    lines.append("## 附：本次漏斗")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(funnel, ensure_ascii=False, indent=2))
    lines.append("```")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成抽取质量人工抽样核对表")
    parser.add_argument("--n", type=int, default=15, help="样本条数（每条约 60~70 秒）")
    parser.add_argument("--searchkey", default="重大资产重组", help="cninfo 全文检索词")
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--market-pages", type=int, default=20)
    parser.add_argument("--model", default="", help="模型名（仅用于记录到核对表）")
    args = parser.parse_args(argv)

    from app.config import settings

    model = args.model or settings.extract_model

    print("=" * 72)
    print("抽取质量人工抽样")
    print(f"  模型      : {model}")
    print(f"  检索口径  : cninfo 全文检索「{args.searchkey}」")
    print(f"  样本数    : {args.n} 条（预计 {args.n * 70 / 60:.0f} 分钟）")
    print(f"  模式      : dry-run（不写判断类数据）")
    print("=" * 72)
    print()

    started = time.time()
    outcome = run_pipeline(PipelineOptions(
        source="cninfo",
        pool="market",
        dry_run=True,
        searchkey=args.searchkey,
        lookback_days=args.lookback_days,
        market_pages=args.market_pages,
        limit=args.n,
        llm_limit=args.n,
    ))
    elapsed = time.time() - started

    payload = outcome.to_dict()
    extractions = payload.get("extractions", [])
    if not extraction_ok(extractions):
        print("\n⚠ 没有拿到任何抽取结果，无法生成核对表。")
        print(f"  漏斗：{json.dumps(payload.get('funnel', {}), ensure_ascii=False)}")
        print(f"  错误：{json.dumps(payload.get('errors', []), ensure_ascii=False)[:400]}")
        return 1

    target_dir = ROOT / "data" / "cache" / "sampling"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    sheet = build_sheet(payload, searchkey=args.searchkey, model=model, seconds=elapsed)
    md_path = target_dir / f"review_{stamp}.md"
    json_path = target_dir / f"review_{stamp}.json"
    md_path.write_text(sheet, encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    agree = sum(1 for e in extractions if e.get("agreement"))
    print()
    print("=" * 72)
    print(f"完成：{len(extractions)} 条样本，耗时 {elapsed / 60:.1f} 分钟")
    print(f"  规则层与 LLM 一致率 : {agree}/{len(extractions)} = {agree / len(extractions) * 100:.0f}%")
    print(f"  通过证据闸门        : {sum(1 for e in extractions if e.get('gate_passed'))}/{len(extractions)}")
    print(f"  schema_failure_rate : {payload.get('llm', {}).get('schema_failure_rate', 0):.2f}")
    print()
    print(f"核对表：{md_path}")
    print(f"原始数据：{json_path}")
    print()
    print("下一步：打开核对表逐条打勾 → 填结论 → 决定是否可以 --live")
    print("=" * 72)
    return 0


def extraction_ok(extractions: list[dict]) -> bool:
    return bool(extractions)


if __name__ == "__main__":
    raise SystemExit(main())

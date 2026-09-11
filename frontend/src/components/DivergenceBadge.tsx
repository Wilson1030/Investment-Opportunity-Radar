/**
 * 规则分与语义分的分歧徽标（D08）。
 *
 * 两个分数各看一个角度；**分歧本身就是研究信号**：
 * 分歧大意味着「有规则没表达的隐性因素」或「LLM 在编造」，两种情况都值得人工看一眼。
 */
export function DivergenceBadge({
  ruleScore,
  semanticScore,
  divergence,
  flagged,
}: {
  ruleScore: number | null
  semanticScore: number | null
  divergence: number | null
  flagged: boolean
}) {
  if (semanticScore === null || divergence === null) {
    return <span className="text-2xs text-faint">语义分待生成</span>
  }
  return (
    <span
      className={`chip ${flagged ? 'text-status-pending border-status-pending/50' : 'text-faint border-border'}`}
      title={
        flagged
          ? '规则分与语义分分歧较大，建议人工复核：可能规则未覆盖某些因素，也可能是模型在编造'
          : '规则分（主分，参与排序）与语义分（辅分，仅补充视角）的分歧在正常范围'
      }
    >
      <span className="num">语义分 {semanticScore.toFixed(0)}</span>
      <span className="text-faint">·</span>
      <span className="num">分歧 {divergence.toFixed(1)}</span>
      {flagged && <span>⚠ 建议复核</span>}
      <span className="text-faint">（规则分 {ruleScore?.toFixed(0) ?? '—'} 为主）</span>
    </span>
  )
}

export default DivergenceBadge

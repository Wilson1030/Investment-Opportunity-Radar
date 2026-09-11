import { useState } from 'react'
import type { ScoreBreakdown as Breakdown, ScoreDimension } from '../api/types'

/**
 * 评分的两级展开（规格 §13 + §14，docs/04 §7 的可解释性 UI 契约）。
 *
 * 第 1 级：维度分（条形图）
 * 第 2 级：逐项加减分，每项显示 rule_id 与绑定的证据
 *
 * **硬性要求**：
 *   - 风险维度必须显式标注方向（否则「风险 43」会被读反）
 *   - 不允许只显示总分而不提供拆解入口
 */
export function ScoreBreakdown({
  breakdown,
  onOpenEvidence,
}: {
  breakdown: Breakdown
  onOpenEvidence?: (evidenceIds: number[]) => void
}) {
  const [open, setOpen] = useState<string | null>('event_catalyst')

  if (breakdown.dimensions.length === 0) {
    return (
      <div className="panel p-3 text-xs text-status-pending">
        {breakdown.note ??
          '尚未生成评分拆解 —— 系统不会展示无法解释的分数（规格 §13）。请先运行 pipeline。'}
      </div>
    )
  }

  return (
    <div className="panel">
      <header className="flex items-baseline justify-between p-3">
        <div>
          <div className="label">规则分（主分 · 参与排序）</div>
          <div className="num text-3xl font-semibold text-text">
            {breakdown.rule_score?.toFixed(0) ?? '—'}
          </div>
        </div>
        <div className="text-right space-y-1">
          <div className="text-2xs text-faint">
            语义分（辅分） <span className="num text-muted">{breakdown.semantic_score?.toFixed(0) ?? '—'}</span>
          </div>
          <div className="text-2xs text-faint">
            分歧{' '}
            <span className={`num ${breakdown.divergence_flagged ? 'text-status-pending' : 'text-muted'}`}>
              {breakdown.divergence?.toFixed(2) ?? '—'}
            </span>
          </div>
          <div className="text-2xs text-faint">
            风险 <span className="num text-status-pending">{breakdown.risk_score?.toFixed(0) ?? '—'}</span>
          </div>
        </div>
      </header>

      {breakdown.divergence_flagged && (
        <div className="mx-3 mb-2 px-2 py-1 border border-status-pending/50 bg-status-pending/10 rounded-sm text-2xs text-status-pending">
          ⚠ 规则分与语义分分歧较大，建议人工复核：可能是规则未覆盖某些因素，也可能是模型在编造。
          语义分不参与排序。
        </div>
      )}

      <div className="hairline" />

      <div className="divide-y divide-border">
        {breakdown.dimensions.map((dimension) => (
          <DimensionRow
            key={dimension.dimension}
            dimension={dimension}
            expanded={open === dimension.dimension}
            onToggle={() => setOpen(open === dimension.dimension ? null : dimension.dimension)}
            onOpenEvidence={onOpenEvidence}
          />
        ))}
      </div>

      <footer className="hairline p-3 flex items-center justify-between text-2xs text-faint">
        <span>
          权重合计（正向）= <span className="num">0.95</span> · 风险最高扣 15 分 · 版本{' '}
          <span className="num">{breakdown.score_version ?? '—'}</span>
        </span>
        <span>分数是相对排序信号，不是概率</span>
      </footer>
    </div>
  )
}

function DimensionRow({
  dimension,
  expanded,
  onToggle,
  onOpenEvidence,
}: {
  dimension: ScoreDimension
  expanded: boolean
  onToggle: () => void
  onOpenEvidence?: (evidenceIds: number[]) => void
}) {
  const isRisk = dimension.direction === 'negative'
  const width = Math.min(100, Math.max(0, dimension.raw_value))

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-3 py-2 hover:bg-panel-2 transition-colors"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted w-24 shrink-0">{dimension.display_name}</span>
          <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
            <div
              className={`h-full ${isRisk ? 'bg-status-pending' : 'bg-accent/70'}`}
              style={{ width: `${width}%` }}
            />
          </div>
          <span className={`num text-xs w-12 text-right ${isRisk ? 'text-status-pending' : 'text-text'}`}>
            {dimension.raw_value.toFixed(0)}
          </span>
          <span className="num text-2xs text-faint w-20 text-right">
            ×{dimension.weight.toFixed(2)} = {dimension.weighted_value >= 0 ? '+' : ''}
            {dimension.weighted_value.toFixed(2)}
          </span>
          <span className="text-faint text-2xs w-4">{expanded ? '▾' : '▸'}</span>
        </div>
        {isRisk && dimension.direction_note && (
          <div className="mt-1 text-2xs text-status-pending/90 pl-24">{dimension.direction_note}</div>
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-3 pl-24 space-y-1">
          {dimension.items.map((item, index) => (
            <div key={`${item.rule_id}-${index}`} className="flex items-start gap-2 text-2xs">
              <span
                className={`num w-14 text-right shrink-0 ${
                  item.delta > 0 ? 'text-accent' : item.delta < 0 ? 'text-status-invalid' : 'text-faint'
                }`}
              >
                {item.delta > 0 ? '+' : ''}
                {Math.abs(item.delta) >= 1 ? item.delta.toFixed(0) : item.delta.toFixed(2)}
              </span>
              <span className="text-muted flex-1">{item.reason}</span>
              <span className="num text-faint shrink-0" title="对应的规则编号（可追溯到规则实现）">
                {item.rule_id}
              </span>
              {item.evidence_ids.length > 0 && (
                <button
                  type="button"
                  className="text-accent/80 hover:text-accent shrink-0"
                  onClick={(e) => {
                    e.stopPropagation()
                    onOpenEvidence?.(item.evidence_ids)
                  }}
                >
                  查看证据 →
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default ScoreBreakdown

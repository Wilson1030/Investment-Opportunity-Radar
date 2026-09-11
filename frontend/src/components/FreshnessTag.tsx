import type { Freshness } from '../api/types'

/** 信息新鲜度标记（规格 §41：New / Updated / Breaking / Stale） */
const META: Record<Freshness, { label: string; cls: string }> = {
  breaking: { label: 'BREAKING', cls: 'text-status-invalid border-status-invalid/50' },
  new: { label: 'NEW', cls: 'text-status-pending border-status-pending/50' },
  updated: { label: 'UPDATED', cls: 'text-accent border-accent/40' },
  stale: { label: 'STALE', cls: 'text-faint border-border' },
}

export function FreshnessTag({ freshness, relative }: { freshness: Freshness; relative?: string }) {
  const meta = META[freshness] ?? META.stale
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`chip ${meta.cls}`}>{meta.label}</span>
      {relative && <span className="text-2xs text-faint num">{relative}</span>}
    </span>
  )
}

export default FreshnessTag

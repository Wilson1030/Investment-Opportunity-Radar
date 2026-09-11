import type { OpportunityStatus } from '../api/types'

/** 状态徽标 —— 7 种生命周期状态（规格 §20） */
const STYLES: Record<OpportunityStatus, { label: string; cls: string; dot: string }> = {
  discovered: { label: '发现', cls: 'text-status-discovered border-status-discovered/40', dot: 'bg-status-discovered' },
  pending_confirmation: { label: '待确认', cls: 'text-status-pending border-status-pending/40', dot: 'bg-status-pending' },
  tracking: { label: '重点跟踪', cls: 'text-status-tracking border-status-tracking/40', dot: 'bg-status-tracking' },
  thesis_confirmed: { label: '逻辑成立', cls: 'text-status-confirmed border-status-confirmed/40', dot: 'bg-status-confirmed' },
  observing: { label: '观察', cls: 'text-status-observing border-status-observing/40', dot: 'bg-status-observing' },
  invalidated: { label: '逻辑失效', cls: 'text-status-invalid border-status-invalid/40', dot: 'bg-status-invalid' },
  archived: { label: '归档', cls: 'text-status-archived border-status-archived/40', dot: 'bg-status-archived' },
}

export function StatusBadge({ status, label }: { status: OpportunityStatus; label?: string }) {
  const style = STYLES[status] ?? STYLES.discovered
  return (
    <span className={`chip ${style.cls}`} title={`生命周期状态：${style.label}`}>
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${style.dot}`} />
      {label ?? style.label}
    </span>
  )
}

export default StatusBadge

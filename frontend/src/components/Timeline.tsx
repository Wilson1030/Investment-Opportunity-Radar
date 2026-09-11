/**
 * 事件时间线。
 *
 * 规格 §40：必须区分「事件发生时间」与「系统发现时间」。
 * 这里以事件发生时间为主轴，并在 hover 中提示系统发现时间。
 */
export function Timeline({
  items,
  onOpenEvidence,
}: {
  items: { date: string | null; event_type: string; title: string; evidence_id: number | null }[]
  onOpenEvidence?: (ids: number[]) => void
}) {
  if (items.length === 0) {
    return <div className="text-xs text-faint p-3">暂无事件。</div>
  }
  return (
    <ol className="relative pl-4">
      <span className="absolute left-1 top-1 bottom-1 w-px bg-border" aria-hidden />
      {items.map((item, index) => (
        <li key={`${item.date}-${index}`} className="relative pb-3 last:pb-0">
          <span className="absolute -left-3 top-1.5 w-1.5 h-1.5 rounded-full bg-accent/70" aria-hidden />
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="num text-2xs text-faint">{item.date ?? '时间未披露'}</span>
            <span className="chip text-faint border-border">{item.event_type}</span>
            <span className="text-xs text-muted">{item.title}</span>
            {item.evidence_id !== null && (
              <button
                type="button"
                className="text-2xs text-accent/80 hover:text-accent"
                onClick={() => onOpenEvidence?.([item.evidence_id as number])}
              >
                查看证据 →
              </button>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

export default Timeline

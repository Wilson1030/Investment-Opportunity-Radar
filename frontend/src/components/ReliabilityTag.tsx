import type { ReliabilityLevel } from '../api/types'

/**
 * 证据可信度 A~E（规格 §16）。
 *
 * 视觉上刻意让 A/B（可作为事实）与 C/D/E（只能作为「市场正在讨论什么」）区分明显，
 * 并且在 >= C 时显示「未经证实」提示 —— 这是 INV-E2 在 UI 上的呼应。
 */
const META: Record<ReliabilityLevel, { note: string; cls: string; solid: boolean }> = {
  A: { note: '公司正式公告 / 交易所披露', cls: 'text-reliability-a border-reliability-a/40', solid: true },
  B: { note: '公司财报 / 官方文件', cls: 'text-reliability-b border-reliability-b/40', solid: true },
  C: { note: '高可信媒体（未经公司确认）', cls: 'text-reliability-c border-reliability-c/40', solid: false },
  D: { note: '机构 / 研究观点', cls: 'text-reliability-d border-reliability-d/40', solid: false },
  E: { note: '社交媒体 / 市场讨论（不能作为事实）', cls: 'text-reliability-e border-reliability-e/40', solid: false },
}

export function ReliabilityTag({ level, showNote = false }: { level: ReliabilityLevel; showNote?: boolean }) {
  const meta = META[level]
  return (
    <span className={`chip ${meta.cls}`} title={meta.note}>
      <span className="font-bold">{level}</span>
      {showNote && <span className="text-muted">{meta.note}</span>}
    </span>
  )
}

export default ReliabilityTag

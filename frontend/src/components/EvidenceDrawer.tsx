import type { Evidence } from '../api/types'
import ReliabilityTag from './ReliabilityTag'

/**
 * 证据抽屉 —— 第 3 级展开：原文段落（docs/04 §7）。
 *
 * 展示 `relevant_text` + `page` / `para_index` 可定位到原文，
 * 并区分事实 / 推断 / 假设 / 市场讨论（规格第 60 节第 6 条）。
 */
const ASSERTION_LABEL: Record<Evidence['assertion_kind'], string> = {
  fact: '事实',
  inference: '推断',
  hypothesis: '假设',
  market_discussion: '市场讨论',
}

export function EvidenceDrawer({
  evidence,
  open,
  onClose,
}: {
  evidence: Evidence[]
  open: boolean
  onClose: () => void
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      <button
        type="button"
        aria-label="关闭"
        className="flex-1 bg-black/60"
        onClick={onClose}
      />
      <aside className="w-full max-w-xl h-full overflow-y-auto bg-panel border-l border-border-strong">
        <header className="sticky top-0 flex items-center justify-between px-4 py-3 bg-panel border-b border-border">
          <div>
            <div className="text-sm font-semibold">证据链</div>
            <div className="text-2xs text-faint">
              每条证据都可定位到原文段落（page / para_index）—— 无法核对的判断不应被当成事实
            </div>
          </div>
          <button type="button" className="btn" onClick={onClose}>
            关闭
          </button>
        </header>

        <div className="divide-y divide-border">
          {evidence.length === 0 && (
            <div className="p-4 text-xs text-faint">暂无可展示的证据。</div>
          )}
          {evidence.map((item) => (
            <article key={item.id} className="p-4 space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <ReliabilityTag level={item.reliability_level} />
                <span className="text-xs text-text">{item.source_name}</span>
                <span className="chip text-faint border-border">
                  {ASSERTION_LABEL[item.assertion_kind]}
                </span>
                <span className="num text-2xs text-faint">{item.publication_time.slice(0, 10)}</span>
                {item.relative_time && (
                  <span className="num text-2xs text-faint">{item.relative_time}</span>
                )}
              </div>

              <div className="text-2xs text-faint">{item.reliability_note}</div>

              <blockquote className="text-xs text-muted leading-reading border-l-2 border-border-strong pl-3 py-1 bg-panel-2/60">
                {item.relevant_text}
              </blockquote>

              <div className="flex items-center gap-3 text-2xs text-faint">
                {item.page !== null && item.page !== undefined && (
                  <span className="num">
                    第 {item.page} 页 第 {item.para_index} 段
                  </span>
                )}
                {item.document_id && <span className="num">{item.document_id}</span>}
                <span className="num">
                  抽取信心 {(item.confidence * 100).toFixed(0)}%
                </span>
                <a
                  className="text-accent/80 hover:text-accent"
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  跳转原文 ↗
                </a>
              </div>

              {item.extracted_facts.length > 0 && (
                <ul className="text-2xs text-muted space-y-0.5">
                  {item.extracted_facts.map((fact) => (
                    <li key={fact}>· {fact}</li>
                  ))}
                </ul>
              )}
            </article>
          ))}
        </div>
      </aside>
    </div>
  )
}

export default EvidenceDrawer

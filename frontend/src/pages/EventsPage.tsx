import { useEffect, useState } from 'react'
import api, { ApiError } from '../api/client'
import type { EventsFixture } from '../api/types'
import DisclaimerBanner from '../components/DisclaimerBanner'
import FreshnessTag from '../components/FreshnessTag'

const FILTERS: { label: string; value: string }[] = [
  { label: '全部', value: '' },
  { label: '重大资产重组', value: 'RESTRUCTURING' },
  { label: '控制权变更', value: 'CONTROL_CHANGE' },
  { label: '资产注入', value: 'ASSET_INJECTION' },
  { label: '增持', value: 'SHAREHOLDER_BUY' },
  { label: '回购', value: 'BUYBACK' },
  { label: '业绩预告', value: 'EARNINGS_TURNAROUND' },
  { label: '政策催化', value: 'POLICY_CATALYST' },
  { label: '监管风险', value: 'REGULATORY_RISK' },
]

/**
 * Event Feed（M10-06 / 规格 §34.5）。
 *
 * 展示所有重要市场事件，可按时点类型过滤。
 * 每条事件都区分「事件发生时间」与「系统发现时间」（规格 §40）。
 */
export function EventsPage() {
  const [eventType, setEventType] = useState('')
  const [payload, setPayload] = useState<EventsFixture | null>(null)
  const [offline, setOffline] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const params: Record<string, string> = eventType ? { event_type: eventType } : {}
    api
      .events(params)
      .then(({ data, meta }) => {
        setPayload(data as EventsFixture)
        setOffline(meta.offline)
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)))
  }, [eventType])

  return (
    <div className="space-y-3">
      <DisclaimerBanner offline={offline} />

      <section className="panel p-3">
        <div className="label mb-2">事件类型</div>
        <div className="flex flex-wrap gap-1">
          {FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              onClick={() => setEventType(filter.value)}
              className={
                eventType === filter.value
                  ? 'chip text-accent border-accent/50 bg-accent/10'
                  : 'chip text-faint border-border hover:text-text'
              }
            >
              {filter.label}
            </button>
          ))}
        </div>
      </section>

      {error && <div className="panel p-3 text-xs text-status-invalid">加载失败：{error}</div>}

      {payload && (
        <section className="panel">
          <header className="flex items-center justify-between px-3 py-2 border-b border-border">
            <span className="label">重要事件</span>
            <span className="num text-2xs text-faint">共 {payload.page.total} 条</span>
          </header>
          <ul className="divide-y divide-border">
            {payload.data.map((event) => (
              <li key={event.id} className="px-3 py-2.5 space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs text-text font-medium">
                    {event.company?.name ?? '—'}
                  </span>
                  <span className="num text-2xs text-faint">{event.company?.code}</span>
                  <span className="chip text-muted border-border">{event.event_type_label}</span>
                  <FreshnessTag freshness={event.freshness} relative={event.relative_time} />
                  {event.is_invalidating && (
                    <span className="chip text-status-invalid border-status-invalid/50">
                      命中失效条件
                    </span>
                  )}
                  <span className="num text-2xs text-faint ml-auto">
                    重要性 {(event.importance * 100).toFixed(0)} · 确定性{' '}
                    {(event.certainty * 100).toFixed(0)}
                  </span>
                </div>
                <div className="text-xs text-muted">{event.title}</div>
                {event.summary && <div className="text-2xs text-faint">{event.summary}</div>}
                <div className="num text-2xs text-faint">{event.time_note}</div>
              </li>
            ))}
            {payload.data.length === 0 && (
              <li className="px-3 py-4 text-xs text-faint">该类型下暂无事件。</li>
            )}
          </ul>
        </section>
      )}
    </div>
  )
}

export default EventsPage

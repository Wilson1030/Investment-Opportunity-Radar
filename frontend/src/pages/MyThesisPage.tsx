import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api, { ApiError } from '../api/client'
import type { MyThesisPayload } from '../api/types'
import DisclaimerBanner from '../components/DisclaimerBanner'
import StageBadge from '../components/StageBadge'
import { StatusBadge } from '../components/StatusBadge'

/**
 * My Thesis（M10-05 / 规格 §21、§34.4）。
 *
 * ★ 这里**不列股票代码**，而是按「投资逻辑」聚合 ——
 * 因为用户保存的不是「自选股：ST XXX」，而是「因为重组预期，所以关注 ST XXX」。
 * 每个条目都必须能看到**失效条件**（规格 §58 原则 5）。
 */
export function MyThesisPage() {
  const [payload, setPayload] = useState<MyThesisPayload | null>(null)
  const [offline, setOffline] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .myThesis()
      .then(({ data, meta }) => {
        setPayload(data)
        setOffline(meta.offline)
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)))
  }, [])

  if (error) return <div className="panel p-4 text-xs text-status-invalid">加载失败：{error}</div>
  if (!payload) return <div className="text-xs text-faint">加载中…</div>

  return (
    <div className="space-y-3">
      <DisclaimerBanner text={payload.disclaimer} offline={offline} />

      <section className="panel p-3">
        <div className="flex items-baseline gap-3">
          <div className="label">我的投资 Thesis</div>
          <span className="num text-2xl font-semibold">{payload.total_tracking}</span>
          <span className="text-2xs text-faint">个跟踪中</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {payload.by_type.length === 0 && <span className="text-2xs text-faint">暂无跟踪中的逻辑</span>}
          {payload.by_type.map((row) => (
            <span key={row.display_name} className="chip text-muted border-border">
              {row.display_name}
              <span className="num text-faint">{row.count}</span>
            </span>
          ))}
        </div>
      </section>

      {payload.items.length === 0 ? (
        <div className="panel p-4 text-xs text-faint">
          还没有跟踪中的投资逻辑。去 <Link to="/" className="text-accent">Radar</Link> 确认关注一个机会，
          系统会把它保存成「因为 X 逻辑，所以关注 Y」。
        </div>
      ) : (
        <ul className="space-y-3">
          {payload.items.map((item) => (
            <li key={item.id} className="panel">
              <header className="flex items-start justify-between gap-3 p-3">
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-semibold">{item.company.name}</span>
                    <span className="num text-2xs text-faint">{item.company.code}</span>
                    <span className="chip text-accent border-accent/40">{item.display_name}</span>
                    {item.has_unread_alert && (
                      <span className="chip text-status-pending border-status-pending/50">
                        ⚠ 逻辑有变化
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5 flex items-center gap-2 flex-wrap">
                    <StatusBadge status={item.status} />
                    <StageBadge stage={item.catalyst_stage} early={item.is_early_signal} />
                  </div>
                </div>
                <div className="text-right shrink-0 text-2xs text-faint">
                  <div>
                    机会分 <span className="num text-text">{item.rule_score?.toFixed(0) ?? '—'}</span>
                  </div>
                  <div>
                    风险 <span className="num text-status-pending">{item.risk_score?.toFixed(0) ?? '—'}</span>
                  </div>
                </div>
              </header>

              <div className="hairline mx-3" />

              <div className="p-3 space-y-2">
                <p className="text-xs text-muted leading-reading border-l-2 border-accent/40 pl-3">
                  {item.statement}
                </p>

                {/* ★ 失效条件必须可见 */}
                <div>
                  <div className="label mb-1">逻辑失效条件</div>
                  <div className="flex flex-wrap gap-1">
                    {item.invalidating_events.map((event) => (
                      <span key={event} className="chip text-status-invalid/80 border-status-invalid/30">
                        {event}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="flex items-center gap-4 text-2xs text-faint">
                  <span>
                    证据 <span className="num text-muted">{item.evidence_count}</span>
                  </span>
                  <span>
                    反证 <span className="num text-status-pending">{item.contradictory_count}</span>
                  </span>
                  <span>
                    待确认 <span className="num text-muted">{item.open_question_count}</span>
                  </span>
                  <span className="num ml-auto">{item.last_updated_at.slice(0, 16).replace('T', ' ')}</span>
                </div>
              </div>

              <footer className="hairline p-3">
                <Link to={`/opportunities/${item.opportunity_id}`} className="btn-primary">
                  查看研究卡
                </Link>
              </footer>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default MyThesisPage

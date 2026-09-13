import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import api, { ApiError } from '../api/client'
import type { RadarPayload } from '../api/types'
import DisclaimerBanner from '../components/DisclaimerBanner'
import OpportunityCard from '../components/OpportunityCard'
import StrategyPanel from '../components/StrategyPanel'
import { StatusBadge } from '../components/StatusBadge'

/**
 * Radar 首页（M10-01 / 规格 §18）。
 *
 * 首屏结构：我的投资画像 → 今日机会 → 最近重要事件 → 提醒。
 * **首屏不出现 K 线**（M10-08）；信息顺序遵循规格 §45 的 7 步。
 */
export function RadarPage() {
  const [radar, setRadar] = useState<RadarPayload | null>(null)
  const [offline, setOffline] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [thesisFilter, setThesisFilter] = useState<string | null>(null)
  /** 操作结果反馈 —— 按钮必须让用户看到「确实做了什么」 */
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const load = useCallback(
    (filter: string | null) =>
      api.radar(filter).then(({ data, meta }) => {
        setRadar(data)
        setOffline(meta.offline)
      }),
    [],
  )

  useEffect(() => {
    load(null).catch((err: unknown) =>
      setError(err instanceof ApiError ? err.message : String(err)),
    )
  }, [load])

  const handleAction = async (id: number, action: string) => {
    setBusy(true)
    setNotice(null)
    setActionError(null)
    try {
      const result = await api.recordAction(id, action, [])
      // ★ 把**实际发生了什么**告诉用户：
      //   原先只刷新列表，状态没变时看起来像按钮没反应（用户以为它是摆设）。
      const payload = result as { data?: { message?: string } } | undefined
      setNotice(payload?.data?.message ?? '已记录')
      await load(thesisFilter)
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const handleFilter = async (next: string | null) => {
    setThesisFilter(next)
    setError(null)
    try {
      await load(next)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }

  if (error && !radar) {
    return (
      <div className="panel p-4 text-xs text-status-invalid">
        加载失败：{error}
      </div>
    )
  }
  if (!radar) return <div className="text-xs text-faint">加载中…</div>

  return (
    <div className="space-y-3">
      <DisclaimerBanner text={radar.disclaimer} offline={offline} />

      {radar.today.calendar_degraded && radar.today.calendar_warning && (
        <div className="px-3 py-1.5 border border-status-pending/40 bg-status-pending/5 rounded-panel text-2xs text-status-pending">
          {radar.today.calendar_warning}
        </div>
      )}

      {/* 我的投资画像 */}
      <section className="panel p-3">
        <header className="flex items-center justify-between">
          <span className="label">我的投资画像</span>
          <Link to="/profile" className="text-2xs text-accent/80 hover:text-accent">
            配置 →
          </Link>
        </header>
        <div className="mt-2 space-y-1.5">
          {radar.profile.top_weights.map((row) => (
            <div key={row.thesis_type} className="flex items-center gap-3">
              <span className="text-xs text-muted w-28 shrink-0">{row.display_name}</span>
              <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent/60"
                  style={{ width: `${Math.min(100, row.weight * 200)}%` }}
                />
              </div>
              <span className="num text-2xs text-faint w-10 text-right">
                {(row.weight * 100).toFixed(0)}%
              </span>
            </div>
          ))}
        </div>
        <div className="mt-2 text-2xs text-faint">
          {radar.profile.auto_learn_enabled ? '自动学习已开启' : '自动学习已关闭'}
          {radar.profile.locked_weights.length > 0 && (
            <>
              {' · '}已锁定 <span className="num">{radar.profile.locked_weights.join(', ')}</span>
              （自动学习不得覆盖，但你可以手动改）
            </>
          )}
        </div>
      </section>

      {/* 策略全景 —— 每类逻辑都要有交代 */}
      {radar.strategies.length > 0 && (
        <StrategyPanel
          overview={radar.strategies}
          active={thesisFilter}
          onSelect={handleFilter}
        />
      )}

      {/* 操作反馈：按钮必须让用户看到效果 */}
      {notice && (
        <div className="panel border-accent-dim/50 bg-accent/5 p-2 text-2xs text-accent">
          {notice}
        </div>
      )}
      {actionError && (
        <div className="panel border-status-invalid/50 p-2 text-2xs text-status-invalid">
          {actionError}
        </div>
      )}

      {/* 今日机会 */}
      <section>
        <header className="flex items-center justify-between px-1 mb-2">
          <h2 className="text-sm font-semibold">
            今日机会
            <span className="ml-2 num text-2xs text-status-pending">
              {radar.today.new_count} NEW
            </span>
          </h2>
          <div className="flex items-center gap-2 text-2xs text-faint">
            <span className="num">{radar.today.date}</span>
            <span>{radar.today.is_trading_day ? '交易日' : '非交易日'}</span>
          </div>
        </header>

        {radar.today.cards.length === 0 ? (
          <div className="panel p-4 text-xs text-faint leading-relaxed">
            {thesisFilter
              ? '该类投资逻辑当前没有机会卡（上方策略全景里有原因）。'
              : '今日暂无机会卡。'}
            {radar.pipeline.hint && (
              <>
                <br />
                漏斗提示：掉得最狠的一级是 <span className="num text-muted">{radar.pipeline.hint}</span>。
              </>
            )}
            <br />
            先跑一次 pipeline：<span className="num text-muted">python -m app.ingest --stage full</span>
            （默认 dry-run）
          </div>
        ) : (
          <div className="space-y-3">
            {radar.today.cards.map((card) => (
              <OpportunityCard
                key={card.id}
                card={card}
                busy={busy}
                onAction={(action) => handleAction(card.id, action)}
              />
            ))}
          </div>
        )}
      </section>

      {/* 状态计数 */}
      <section className="panel p-3">
        <div className="label mb-2">机会生命周期</div>
        <div className="flex flex-wrap gap-3">
          {Object.entries(radar.counts).map(([status, count]) => (
            <div key={status} className="flex items-center gap-1.5">
              <StatusBadge status={status as never} />
              <span className="num text-xs text-muted">{count}</span>
            </div>
          ))}
        </div>
      </section>

      {/* 最近重要事件 */}
      <section className="panel">
        <header className="px-3 py-2 hairline-none border-b border-border">
          <span className="label">最近重要事件</span>
        </header>
        <ul className="divide-y divide-border">
          {radar.recent_events.map((event) => (
            <li key={event.id} className="flex items-center gap-3 px-3 py-2 text-2xs">
              <span className="num text-faint w-14 shrink-0">
                {event.event_time ? event.event_time.slice(11, 16) : '—'}
              </span>
              <span className="text-muted w-24 shrink-0 truncate">
                {event.company?.name ?? '—'}
              </span>
              <span className="chip text-faint border-border shrink-0">
                {event.event_type_label}
              </span>
              <span className="text-muted truncate flex-1">{event.title}</span>
              <span className="num text-faint shrink-0">{event.relative_time}</span>
            </li>
          ))}
        </ul>
      </section>

      {/* 提醒 */}
      {radar.alerts.length > 0 && (
        <section className="panel border-status-pending/40">
          <header className="px-3 py-2 border-b border-border">
            <span className="label">投资逻辑变化提醒</span>
          </header>
          <ul className="divide-y divide-border">
            {radar.alerts.map((alert) => (
              <li key={alert.id} className="px-3 py-2 space-y-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-status-pending font-semibold">{alert.title}</span>
                  <span className="num text-2xs text-faint">{alert.relative_time}</span>
                </div>
                <div className="text-2xs text-muted">{alert.message}</div>
                {alert.score_before !== null && alert.score_after !== null && (
                  <div className="text-2xs text-faint">
                    原机会评分 <span className="num text-muted">{alert.score_before}</span> →{' '}
                    <span className="num text-status-invalid">{alert.score_after}</span>
                    {alert.suggestion && <span className="ml-2">{alert.suggestion}</span>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 漏斗与质量（可观测） */}
      {radar.pipeline.last_run_at && (
        <section className="panel p-3">
          <div className="flex items-center justify-between">
            <span className="label">Pipeline 可观测</span>
            <span className="text-2xs text-faint">
              上次运行 <span className="num">{radar.pipeline.last_run_at.slice(0, 16).replace('T', ' ')}</span>
              {radar.pipeline.dry_run && (
                <span className="ml-2 chip text-status-pending border-status-pending/40">DRY-RUN</span>
              )}
            </span>
          </div>
          <div className="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-2">
            {Object.entries(radar.pipeline.funnel ?? {}).map(([stage, value]) => (
              <div key={stage} className="panel-2 px-2 py-1.5">
                <div className="text-2xs text-faint truncate" title={stage}>
                  {stage}
                </div>
                <div className="num text-sm text-text">{value}</div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

export default RadarPage

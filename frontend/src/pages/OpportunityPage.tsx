import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import api, { ApiError } from '../api/client'
import type { Evidence, OpportunityDetail, ScoreBreakdown as Breakdown } from '../api/types'
import AiJudgement from '../components/AiJudgement'
import DisclaimerBanner from '../components/DisclaimerBanner'
import EvidenceDrawer from '../components/EvidenceDrawer'
import FinancialsPanel from '../components/FinancialsPanel'
import ReliabilityTag from '../components/ReliabilityTag'
import ScoreBreakdown from '../components/ScoreBreakdown'
import StageBadge from '../components/StageBadge'
import { StatusBadge } from '../components/StatusBadge'
import Timeline from '../components/Timeline'

/**
 * Opportunity Detail（M10-04 / 规格 §34.3 与 §45）。
 *
 * 页面顺序遵循规格 §45 的 7 步信息层级，并补入「基本面数据」：
 *   ① 发生了什么 → ② 为什么重要 → ③ 为什么与我有关 → ④ 有什么证据
 *   → ⑤ 基本面数据 → ⑥ 哪些地方还不确定 → ⑦ 风险是什么 → ⑧ 下一步看什么
 *
 * 行情（K 线等）只作为最后的辅助信息层出现（规格 §46）。
 */
export function OpportunityPage() {
  const { id } = useParams()
  const opportunityId = Number(id)

  const [detail, setDetail] = useState<OpportunityDetail | null>(null)
  const [breakdown, setBreakdown] = useState<Breakdown | null>(null)
  const [offline, setOffline] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [focusEvidence, setFocusEvidence] = useState<number[]>([])

  useEffect(() => {
    if (!Number.isFinite(opportunityId)) return
    Promise.all([api.opportunity(opportunityId), api.scoreBreakdown(opportunityId)])
      .then(([detailResult, breakdownResult]) => {
        setDetail(detailResult.data)
        setBreakdown(breakdownResult.data)
        setOffline(detailResult.meta.offline || breakdownResult.meta.offline)
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)))
  }, [opportunityId])

  const allEvidence = useMemo<Evidence[]>(() => {
    if (!detail) return []
    return [...detail.evidence, ...(detail.thesis?.contradictory_evidence ?? [])]
  }, [detail])

  const openEvidence = (ids: number[]) => {
    setFocusEvidence(ids)
    setDrawerOpen(true)
  }

  if (error) return <div className="panel p-4 text-xs text-status-invalid">加载失败：{error}</div>
  if (!detail) return <div className="text-xs text-faint">加载中…</div>

  const { card, thesis } = detail
  const openQuestions = detail.open_questions.filter((q) => q.status === 'open')
  const confirmedQuestions = detail.open_questions.filter((q) => q.status === 'confirmed')
  const shownEvidence = focusEvidence.length
    ? allEvidence.filter((e) => focusEvidence.includes(e.id))
    : allEvidence

  return (
    <div className="space-y-3">
      <DisclaimerBanner text={detail.disclaimer} offline={offline} />

      {/* 面包屑 */}
      <div className="text-2xs text-faint">
        <Link to="/" className="hover:text-accent">
          Radar
        </Link>
        <span className="mx-1">/</span>
        <span>{card.company.name}</span>
        {card.thesis_display_name && (
          <>
            <span className="mx-1">/</span>
            <span className="text-accent">{card.thesis_display_name}</span>
          </>
        )}
      </div>

      {/* 头部 */}
      <header className="panel p-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-lg font-semibold">{card.company.name}</h1>
              <span className="num text-2xs text-faint">{card.company.code}</span>
              {card.company.industry && (
                <span className="chip text-faint border-border">{card.company.industry}</span>
              )}
              {card.thesis_display_name && (
                <span className="chip text-accent border-accent/40">{card.thesis_display_name}</span>
              )}
            </div>
            <div className="mt-1.5 flex items-center gap-2 flex-wrap">
              <StatusBadge status={card.status} label={card.status_label} />
              <StageBadge stage={card.catalyst_stage} early={card.is_early_signal} />
            </div>
          </div>
          <div className="text-right shrink-0">
            <div className="text-2xs text-faint">
              MATCH{' '}
              <span className="num text-accent text-base font-semibold">
                {card.match_score?.toFixed(0) ?? '—'}%
              </span>
            </div>
            <div className="text-2xs text-faint">
              机会分 <span className="num text-text text-xl font-semibold">{card.rule_score?.toFixed(0) ?? '—'}</span>
            </div>
            <div className="text-2xs text-faint">
              风险 <span className="num text-status-pending">{card.risk_score?.toFixed(0) ?? '—'}</span>
              <span className="ml-1 text-faint">（越高越危险）</span>
            </div>
          </div>
        </div>
      </header>

      {card.is_early_signal && (
        <div className="panel p-3 border-status-pending/50 bg-status-pending/5">
          <div className="text-xs text-status-pending font-semibold">⚑ 早期苗头 · 低确定性</div>
          <p className="mt-1 text-2xs text-muted leading-reading">
            该机会处于「{card.catalyst_stage?.split('｜')[1] ?? '早期'}」阶段。
            这类信号的价值在于<b>提前量</b>：市场关注度低、可验证信息少，
            因此确定性显著低于「进展」与「完成」阶段的机会。
            请以「待确认」的心态阅读，并重点关注下方的失效条件 ——
            例如法院不受理、申请被撤回、或停牌后终止筹划。
          </p>
        </div>
      )}

      {/* ② 为什么重要 —— 投资 Thesis */}
      {thesis && (
        <section className="panel p-3 space-y-3">
          <div>
            <div className="label mb-1">投资 Thesis（为什么关注它）</div>
            <p className="text-sm leading-reading">{thesis.statement}</p>
          </div>

          <div className="hairline" />

          {/* Why Now（规格 §52 固定字段） */}
          <div>
            <div className="label mb-1">为什么现在（Why Now）</div>
            <dl className="space-y-1 text-xs">
              <Row term="过去" desc={thesis.why_now.past} />
              <Row term="最近" desc={thesis.why_now.recent} />
              <Row term="本周" desc={thesis.why_now.this_week} />
              <Row term="因此" desc={thesis.why_now.conclusion} />
            </dl>
          </div>

          <div className="hairline" />

          {/* ★ 失效条件 —— 规格 §58 原则 5 */}
          <div>
            <div className="label mb-1">什么情况下这个逻辑不再成立</div>
            <ul className="space-y-1 text-xs">
              {thesis.invalidating_events.map((item) => (
                <li key={item.description} className="flex items-start gap-2">
                  <span
                    className={`chip shrink-0 ${
                      item.severity === 'terminal'
                        ? 'text-status-invalid border-status-invalid/40'
                        : item.severity === 'severe'
                          ? 'text-status-pending border-status-pending/40'
                          : 'text-faint border-border'
                    }`}
                  >
                    {item.severity === 'terminal' ? '彻底失效' : item.severity === 'severe' ? '重大削弱' : '提示'}
                  </span>
                  <span className="text-muted">{item.description}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}

      {/* ②b AI 判断 —— 规格 §44：AI 负责解释，不替用户决策 */}
      <section className="panel p-3 space-y-2">
        <div className="flex items-baseline justify-between">
          <div className="label">AI 判断</div>
          <span className="text-2xs text-faint">
            由模型生成（可出错）—— 与上面的投资 Thesis 分开呈现，便于分辨哪句可核对
          </span>
        </div>
        <AiJudgement
          summary={detail.card.ai_judgement}
          ruleScore={detail.card.rule_score}
          semanticScore={detail.card.semantic_score}
          divergence={detail.card.divergence}
          divergenceFlagged={detail.card.divergence_flagged}
        />
      </section>

      {/* ③ 为什么与我有关 —— 评分拆解 */}
      {breakdown && (
        <section>
          <div className="label mb-1.5 px-1">为什么是 {breakdown.rule_score?.toFixed(0)}（点击展开逐项）</div>
          <ScoreBreakdown breakdown={breakdown} onOpenEvidence={openEvidence} />
        </section>
      )}

      {/* ① 发生了什么 —— 事件时间线 */}
      <section className="panel p-3">
        <div className="label mb-2">事件时间线</div>
        <Timeline items={detail.timeline} onOpenEvidence={openEvidence} />
      </section>

      {/* ④ 有什么证据 */}
      <section className="panel p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="label">证据（{allEvidence.length} 条）</span>
          <button type="button" className="btn" onClick={() => openEvidence([])}>
            打开证据抽屉
          </button>
        </div>
        <ul className="space-y-2">
          {allEvidence.slice(0, 4).map((item) => (
            <li key={item.id} className="space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <ReliabilityTag level={item.reliability_level} showNote />
                <span className="text-xs text-muted">{item.source_name}</span>
                <span className="num text-2xs text-faint">{item.publication_time.slice(0, 10)}</span>
                {/* ★ 带页码锚点的深链 —— 不加锚点只会停在 PDF 第 1 页 */}
                {item.page !== null && item.page !== undefined && (
                  <a
                    className="num text-2xs text-accent/80 hover:text-accent"
                    href={item.source_deep_link || item.source_url}
                    target="_blank"
                    rel="noreferrer"
                    title={`打开原文并跳到第 ${item.page} 页`}
                  >
                    第 {item.page} 页 第 {item.para_index} 段 ↗
                  </a>
                )}
              </div>
              <blockquote className="text-2xs text-muted leading-reading border-l-2 border-border-strong pl-3">
                {item.relevant_text}
              </blockquote>
            </li>
          ))}
        </ul>
      </section>

      {/* ④b 新闻聚类 —— 规格 §42/§43：N 条报道归成一簇，不是 N 张重复卡片 */}
      {detail.news_clusters.length > 0 && (
        <section className="panel p-3 space-y-2">
          <div className="label">相关报道</div>
          {detail.news_clusters.map((cluster) => (
            <div key={cluster.id} className="space-y-1">
              <div className="flex items-baseline justify-between text-xs">
                <span>{cluster.label}</span>
                {cluster.last_seen && (
                  <span className="num text-2xs text-faint">
                    最近 {cluster.last_seen.slice(0, 16).replace('T', ' ')}
                  </span>
                )}
              </div>
              <ul className="space-y-0.5 text-2xs text-muted">
                {cluster.key_points.map((point) => (
                  <li key={point} className="border-l-2 border-border pl-2">
                    {point}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <p className="text-2xs text-faint">
            要点为<b>真实标题</b>（去近重复后的代表条目），不是模型概括 —— 便于你直接核对。
          </p>
        </section>
      )}

      {/* ⑤ 基本面数据 —— 让「基本面扣分」可核对（P1-1） */}
      <FinancialsPanel
        financials={detail.financials ?? []}
        signals={detail.financial_signals ?? []}
      />

      {/* ⑥ 哪些地方还不确定 —— ★ 规格 §24 的核心 */}
      <section className="panel p-3">
        <div className="label mb-2">等待确认的事项</div>
        <div className="grid sm:grid-cols-2 gap-3">
          <div>
            <div className="text-2xs text-reliability-a mb-1">✓ 已确认</div>
            <ul className="space-y-0.5 text-xs text-muted">
              {detail.confirmed_facts.map((fact) => (
                <li key={fact}>· {fact}</li>
              ))}
              {confirmedQuestions.map((q) => (
                <li key={q.id}>· {q.question}</li>
              ))}
            </ul>
          </div>
          <div>
            <div className="text-2xs text-status-pending mb-1">? 待确认</div>
            <ul className="space-y-0.5 text-xs text-muted">
              {openQuestions.length === 0 && <li className="text-faint">· 暂无未确认事项</li>}
              {openQuestions.map((q) => (
                <li key={q.id}>· {q.question}</li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {/* ⑦ 风险是什么 —— 含反证 */}
      <section className="panel p-3 space-y-3">
        <div>
          <div className="label mb-1.5">风险</div>
          <ul className="space-y-1 text-xs">
            {detail.risks.map((risk) => (
              <li key={risk.factor} className="flex items-start gap-2">
                <span className="text-status-pending shrink-0">⚠</span>
                <span>
                  <span className="text-text">{risk.factor}</span>
                  {risk.description && <span className="text-muted"> —— {risk.description}</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>

        {thesis && thesis.contradictory_evidence.length > 0 && (
          <div>
            <div className="label mb-1.5">反证（系统主动寻找的相反证据）</div>
            <ul className="space-y-1">
              {thesis.contradictory_evidence.map((item) => (
                <li key={item.id} className="text-2xs">
                  <div className="flex items-center gap-2">
                    <ReliabilityTag level={item.reliability_level} />
                    <span className="text-muted">{item.relevant_text}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* ⑧ 下一步看什么 */}
      <section className="panel p-3">
        <div className="label mb-1.5">下一步观察什么</div>
        <ol className="space-y-0.5 text-xs text-muted">
          {detail.next_events_to_watch.map((item, index) => (
            <li key={item}>
              <span className="num text-faint mr-2">{String(index + 1).padStart(2, '0')}</span>
              {item}
            </li>
          ))}
        </ol>
      </section>

      {/* 状态变更历史 */}
      <section className="panel p-3">
        <div className="label mb-2">状态变更</div>
        <ul className="space-y-1 text-2xs">
          {detail.status_history.map((entry, index) => (
            <li key={index} className="flex items-center gap-2">
              <span className="num text-faint">{entry.changed_at.slice(0, 16).replace('T', ' ')}</span>
              <span className="text-muted">
                {entry.from_status ?? '—'} → {entry.to_status}
              </span>
              <span className="text-faint">{entry.reason}</span>
              {entry.score_before !== null && entry.score_after !== null && (
                <span className="num text-faint">
                  {entry.score_before} → {entry.score_after}
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>

      {/* 辅助信息层（规格 §46）—— 放最后，首屏不出现 */}
      <section className="panel p-3 border-dashed">
        <div className="label mb-1">辅助信息层</div>
        <p className="text-2xs text-faint">{detail.market.note}</p>

        {detail.market.valuation ? (
          <div className="mt-2 text-2xs space-y-1">
            <div className="flex flex-wrap gap-x-4 gap-y-1 num text-muted">
              <span>
                市值 {detail.market.valuation.market_cap?.toFixed(2) ?? '—'}{' '}
                {detail.market.valuation.market_cap_unit}
              </span>
              <span>PE(TTM) {detail.market.valuation.pe_ttm?.toFixed(2) ?? '—'}</span>
              <span>PB {detail.market.valuation.pb?.toFixed(2) ?? '—'}</span>
              <span>
                PE 分位{' '}
                {detail.market.valuation.pe_percentile !== null
                  ? `${(detail.market.valuation.pe_percentile * 100).toFixed(0)}%`
                  : '—'}
              </span>
            </div>
            <p className="text-faint">
              {detail.market.valuation.direction_note} · 窗口{' '}
              {detail.market.valuation.window_days} 天 · 数据日{' '}
              {detail.market.valuation.as_of}
            </p>
          </div>
        ) : (
          <p className="text-2xs text-faint mt-1">{detail.market.valuation_note}</p>
        )}

        <p className="text-2xs text-faint mt-1">
          行情与图表在这里、也只在这里 —— 它不是本系统的主体（规格 §19）。
        </p>
      </section>

      <EvidenceDrawer
        evidence={shownEvidence}
        open={drawerOpen}
        onClose={() => {
          setDrawerOpen(false)
          setFocusEvidence([])
        }}
      />
    </div>
  )
}

function Row({ term, desc }: { term: string; desc?: string | null }) {
  return (
    <div className="flex gap-3">
      <dt className="text-faint w-10 shrink-0">{term}</dt>
      <dd className="text-muted">{desc ?? '—'}</dd>
    </div>
  )
}

export default OpportunityPage

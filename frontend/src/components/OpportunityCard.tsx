import { Link } from 'react-router-dom'
import type { OpportunityCard as Card } from '../api/types'
import AiJudgement from './AiJudgement'
import CardActions from './CardActions'
import FreshnessTag from './FreshnessTag'
import StageBadge from './StageBadge'
import StatusBadge from './StatusBadge'

/**
 * 机会卡（规格 §17）。
 *
 * 结构必须回答四个问题：这是什么 / 为什么进入你的关注池 / 最新发生了什么 / AI 怎么判断。
 * **首屏不出现 K 线**（M10-08）—— 这里连价格字段都不展示。
 * 卡片操作按钮 ≤ 4 个（M9-01）。
 */
export function OpportunityCard({
  card,
  onActionDone,
}: {
  card: Card
  /** 操作完成后刷新列表（状态与按钮随之更新） */
  onActionDone: () => void | Promise<void>
}) {
  return (
    <article className="panel">
      {/* 头部：公司 + 策略 + 匹配度 + 状态 */}
      <header className="flex items-start justify-between gap-3 p-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base font-semibold tracking-wide">{card.company.name}</span>
            <span className="num text-2xs text-faint">{card.company.code}</span>
            {card.company.is_st && (
              <span className="chip text-status-invalid border-status-invalid/40">ST</span>
            )}
            {card.thesis_display_name && (
              <span className="chip text-accent border-accent/40">{card.thesis_display_name}</span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-3 flex-wrap">
            <StatusBadge status={card.status} label={card.status_label} />
            {/* ★ 阶段必须在这里：用户要「提前布局」，就要能一眼分辨苗头与已推进 */}
            <StageBadge stage={card.catalyst_stage} early={card.is_early_signal} />
            <span className="text-2xs text-faint">
              证据 <span className="num text-muted">{card.evidence_count}</span> 条
              {card.a_grade_evidence_count > 0 && (
                <span className="num text-reliability-a"> （A 级 {card.a_grade_evidence_count}）</span>
              )}
              · 待确认 <span className="num text-muted">{card.open_question_count}</span> 项
            </span>
          </div>
        </div>

        <div className="text-right shrink-0">
          <div className="text-2xs text-faint">
            MATCH <span className="num text-accent text-sm font-semibold">{card.match_score?.toFixed(0) ?? '—'}%</span>
          </div>
          <div className="mt-1 text-2xs text-faint">
            机会分 <span className="num text-text text-lg font-semibold">{card.rule_score?.toFixed(0) ?? '—'}</span>
          </div>
          <div className="text-2xs text-faint">
            风险 <span className="num text-status-pending">{card.risk_score?.toFixed(0) ?? '—'}</span>
          </div>
        </div>
      </header>

      {/* ★ 早期苗头警示：不能让人把苗头当成确定的事（§24 / §38） */}
      {card.is_early_signal && (
        <div className="mx-3 mb-2 px-2 py-1 border border-status-pending/50 bg-status-pending/10 rounded-sm text-2xs text-status-pending leading-relaxed">
          <span className="font-bold">⚑ 早期苗头 · 低确定性</span>
          <span className="text-muted">
            　当前处于「{card.catalyst_stage?.split('｜')[1] ?? '早期'}」阶段。
            这是提前布局用的线索，不是已确认的催化事件 —— 交易可能最终不成立。
          </span>
        </div>
      )}

      {/* ★ M4-04：支撑证据里没有 A/B 类 → 强制警示 */}
      {card.only_market_discussion && (
        <div className="mx-3 mb-2 px-2 py-1 border border-status-pending/50 bg-status-pending/10 rounded-sm text-2xs text-status-pending">
          仅市场讨论，未经证实 —— 当前没有 A/B 类（公告 / 财报）证据支撑该判断
        </div>
      )}

      <div className="hairline mx-3" />

      {/* 为什么进入你的关注池 */}
      {card.why_in_radar.length > 0 && (
        <section className="p-3">
          <div className="label mb-1.5">为什么进入你的关注池？</div>
          <ol className="space-y-0.5 text-xs text-muted">
            {card.why_in_radar.map((reason, index) => (
              <li key={reason} className="flex gap-2">
                <span className="text-accent/70 num shrink-0">{'①②③④⑤⑥⑦⑧'[index] ?? '·'}</span>
                <span>{reason}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* 最新事件 */}
      {card.latest_events.length > 0 && (
        <>
          <div className="hairline mx-3" />
          <section className="p-3">
            <div className="label mb-1.5">最新事件</div>
            <ul className="space-y-1">
              {card.latest_events.slice(0, 4).map((event) => (
                <li key={event.id} className="flex items-center gap-2 text-2xs">
                  <span className="num text-faint w-16 shrink-0">
                    {event.event_time ? event.event_time.slice(5, 10).replace('-', '.') : '—'}
                  </span>
                  <span className="text-muted truncate flex-1">{event.title}</span>
                  <FreshnessTag freshness={event.freshness} relative={event.relative_time} />
                </li>
              ))}
            </ul>
          </section>
        </>
      )}

      {/* AI 判断（与详情页共用同一个组件，避免两处漂移） */}
      {card.ai_judgement && (
        <>
          <div className="hairline mx-3" />
          <section className="p-3">
            <div className="label mb-1.5">AI 判断</div>
            <AiJudgement
              summary={card.ai_judgement}
              ruleScore={card.rule_score}
              semanticScore={card.semantic_score}
              divergence={card.divergence}
              divergenceFlagged={card.divergence_flagged}
            />
          </section>
        </>
      )}

      {/* 下一步观察什么（规格 §53：固定字段） */}
      {card.next_events_to_watch.length > 0 && (
        <>
          <div className="hairline mx-3" />
          <section className="p-3">
            <div className="label mb-1.5">下一步观察什么</div>
            <div className="flex flex-wrap gap-1">
              {card.next_events_to_watch.map((item) => (
                <span key={item} className="chip text-faint border-border">
                  {item}
                </span>
              ))}
            </div>
          </section>
        </>
      )}

      {/* ★ M9-01：卡片操作 ≤ 4 个（链接 + 至多 3 个操作）
          ★ 按钮**随状态变化** —— 关注之后会变成「取消关注」。
            原先三个按钮写死，点完一个字都不变，用户看不出操作生效了。 */}
      <footer className="hairline p-3 space-y-2">
        <div className="flex items-center gap-2">
          <Link to={`/opportunities/${card.id}`} className="btn-primary">
            查看证据与研究卡
          </Link>
        </div>
        <CardActions opportunityId={card.id} status={card.status} onDone={onActionDone} />
      </footer>
    </article>
  )
}

export default OpportunityCard

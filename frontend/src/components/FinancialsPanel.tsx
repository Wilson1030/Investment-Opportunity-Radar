/**
 * 基本面数据面板（P1-1）。
 *
 * 为什么要有它：机会卡上写着「基本面扣分」，但用户此前**无从核对** ——
 * 后端采了 80 期结构化财务，详情接口一个字段都没返回。
 * 只给结论不给依据，等于要求用户相信一个看不见的数字。
 *
 * 两块内容：
 *   ① 财务信号 —— 每条都是**驱动 C4 / FUNDAMENTALS / RISK 的规则条件**，
 *      带 ``key`` 可追溯到 ``engine/rules.py``；tone 只表达方向，不混淆事实与判断
 *   ② 报告期数据 —— 指标为行、报告期为列，便于横向看趋势
 */
import type { FinancialPeriod, FinancialSignal } from '../api/types'

/** 指标的展示口径。显式列出而不从 unit 字符串猜 —— 猜错单位的代价比缺数据大。 */
const METRIC_META: Record<string, { label: string; format: (v: number) => string }> = {
  revenue: { label: '营业总收入', format: (v) => `${v.toFixed(2)} 亿` },
  net_profit: { label: '净利润', format: (v) => `${v.toFixed(2)} 亿` },
  ocf: { label: '经营现金流净额', format: (v) => `${v.toFixed(2)} 亿` },
  // 毛利率 / 资产负债率是无量纲比率，显示成百分比才符合直觉
  gross_margin: { label: '毛利率', format: (v) => `${(v * 100).toFixed(1)}%` },
  debt_ratio: { label: '资产负债率', format: (v) => `${(v * 100).toFixed(1)}%` },
  ocf_per_share: { label: '每股经营现金流', format: (v) => `${v.toFixed(2)} 元` },
  receivable_days: { label: '应收周转天数', format: (v) => `${v.toFixed(2)} 天` },
}

const METRIC_ROWS = [
  'revenue',
  'net_profit',
  'ocf',
  'gross_margin',
  'debt_ratio',
  'ocf_per_share',
  'receivable_days',
]

/** 表格最多展示的期数（再多就挤了，且越久远的参考价值越低） */
const MAX_PERIODS = 5

const TONE_CLASS: Record<FinancialSignal['tone'], string> = {
  bad: 'text-status-invalid',
  good: 'text-status-confirmed',
  muted: 'text-faint',
}

const TONE_MARK: Record<FinancialSignal['tone'], string> = {
  bad: '×',
  good: '✓',
  muted: '·',
}

function yoyText(yoy: number | null): { text: string; cls: string } {
  if (yoy === null || yoy === undefined) return { text: '—', cls: 'text-faint' }
  const pct = (yoy * 100).toFixed(1)
  const cls = yoy > 0 ? 'text-status-confirmed' : yoy < 0 ? 'text-status-invalid' : 'text-faint'
  return { text: `${yoy > 0 ? '+' : ''}${pct}%`, cls }
}

export function FinancialsPanel({
  financials,
  signals,
  title = '基本面数据',
}: {
  financials: FinancialPeriod[]
  signals: FinancialSignal[]
  /** 标题可定制：在详情页里它是 ③（为什么与我有关）的**事实依据** */
  title?: string
}) {
  const periods = financials.slice(0, MAX_PERIODS)
  const proxySignal = signals.find((s) => s.key === 'ocf_not_positive' && s.value_text.includes('代理值'))

  if (financials.length === 0 && signals.length === 0) {
    return (
      <section className="panel p-3 border-dashed">
        <div className="label mb-1">{title}</div>
        <p className="text-2xs text-faint">
          未采集到结构化财务数据 —— 因此本卡的「经营困境 C4」「基本面」「风险 RISK」
          三个维度缺少依据。<b>缺失就是不显示，不用估算值填充。</b>
        </p>
      </section>
    )
  }

  return (
    <section className="panel p-3 space-y-3">
      <div className="flex items-baseline justify-between">
        <div className="label">{title}</div>
        <span className="text-2xs text-faint">
          {financials.length > 0 && `共 ${financials.length} 个报告期`}
        </span>
      </div>

      {/* ① 驱动评分的规则条件 —— 让「扣分」变得可核对 */}
      <div>
        <div className="text-2xs text-faint mb-1.5">
          下面每一条都是评分时<b>实际使用</b>的条件，可追溯到对应维度
        </div>
        <ul className="space-y-1">
          {signals.map((s) => (
            <li key={s.key} className="flex flex-wrap items-baseline gap-x-2 text-xs">
              <span className={`num w-3 ${TONE_CLASS[s.tone]}`}>{TONE_MARK[s.tone]}</span>
              <span className="text-muted min-w-[7.5rem]">{s.label}</span>
              <span className={`num ${TONE_CLASS[s.tone]}`}>{s.value_text}</span>
              <span className="text-2xs text-faint ml-auto">{s.impact}</span>
            </li>
          ))}
        </ul>
        {proxySignal && (
          <p className="text-2xs text-status-pending mt-1.5">
            注：现金流用的是「每股经营现金流」代理值（总额未取到）——
            方向可信，绝对规模不可比。
          </p>
        )}
      </div>

      {/* ② 报告期数据 —— 指标为行、报告期为列 */}
      {periods.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-2xs text-faint">
                <th className="text-left font-normal pb-1">指标</th>
                {periods.map((p) => (
                  <th key={p.period_end} className="text-right font-normal pb-1 num pl-3">
                    {p.period}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {METRIC_ROWS.map((metric) => {
                const present = periods.some((p) => p.metrics[metric])
                if (!present) return null
                const meta = METRIC_META[metric]
                return (
                  <tr key={metric} className="border-t border-border">
                    <td className="text-muted py-1 whitespace-nowrap">{meta.label}</td>
                    {periods.map((p) => {
                      const entry = p.metrics[metric]
                      const yoy = yoyText(entry?.yoy ?? null)
                      return (
                        <td
                          key={p.period_end}
                          className="text-right py-1 pl-3 whitespace-nowrap align-top"
                          title={entry?.is_anomaly ? entry.anomaly_note ?? '' : undefined}
                        >
                          <div className="num">
                            {entry && entry.value !== null ? meta.format(entry.value) : '—'}
                            {entry?.is_anomaly && (
                              <span className="text-status-pending ml-0.5" title={entry.anomaly_note ?? ''}>
                                ⚠
                              </span>
                            )}
                          </div>
                          <div className={`num text-2xs ${yoy.cls}`}>{yoy.text}</div>
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="text-2xs text-faint mt-1.5">
            每格上方是报告期数值、下方是同比（比率类为<b>相对变化</b>，不是百分点）。
            源：同花顺财务摘要 + 新浪现金流量表。
          </p>
        </div>
      )}
    </section>
  )
}

export default FinancialsPanel

/**
 * 策略全景（规格 §19：让用户知道「系统在看什么」）。
 *
 * ★ 为什么必须有这一块：
 * 系统实现了 10 类投资逻辑，但某次采集往往只出 4 类卡 ——
 * 用户看到的界面是「剩下 6 类没有」，于是得出结论「这些都没加入」。
 * 而真相可能是「这批候选公司里没有符合那 6 类逻辑的标的」。
 *
 * 所以每类策略都必须给出一个**可核对的数字**：
 *   有卡 → 几张
 *   没卡 → 最高覆盖率是多少、比门槛差多少
 * 这样「没卡」也是一个有依据的结论，而不是一片空白。
 */
import type { RadarPayload } from '../api/types'

export function StrategyPanel({
  overview,
  active,
  onSelect,
}: {
  overview: RadarPayload['strategies']
  active: string | null
  onSelect: (thesisType: string | null) => void
}) {
  const total = overview.reduce((sum, row) => sum + row.card_count, 0)
  const withCards = overview.filter((row) => row.card_count > 0).length

  return (
    <section className="panel p-3 space-y-2">
      <header className="flex items-baseline justify-between">
        <span className="label">策略全景</span>
        <span className="text-2xs text-faint">
          {overview.length} 类逻辑 · {withCards} 类有卡 · 共 {total} 张
        </span>
      </header>

      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => onSelect(null)}
          className={`chip ${active === null ? 'text-accent border-accent-dim' : 'text-faint border-border'}`}
        >
          全部
        </button>
        {overview.map((row) => (
          <button
            key={row.thesis_type}
            type="button"
            onClick={() => onSelect(row.thesis_type)}
            title={row.reason ?? `权重 ${(row.weight * 100).toFixed(0)}%`}
            className={`chip ${
              active === row.thesis_type
                ? 'text-accent border-accent-dim'
                : row.card_count > 0
                  ? 'text-muted border-border'
                  : 'text-faint border-border'
            }`}
          >
            {row.display_name}
            <span className="num ml-1">{row.card_count}</span>
            {row.weight <= 0 && <span className="ml-1 text-status-pending">未关注</span>}
          </button>
        ))}
      </div>

      {/* 没有卡的策略：必须给出原因，否则用户会以为它没实现 */}
      <ul className="space-y-1 text-2xs">
        {overview
          .filter((row) => row.card_count === 0)
          .map((row) => (
            <li key={row.thesis_type} className="flex gap-2 text-faint">
              <span className="text-muted shrink-0 w-24">{row.display_name}</span>
              <span className="leading-reading">{row.reason}</span>
              {row.best_coverage !== undefined && (
                <span className="num shrink-0 text-faint">
                  （最高 {row.best_coverage.toFixed(2)}）
                </span>
              )}
            </li>
          ))}
      </ul>

      {overview.some((row) => row.weight <= 0) && (
        <p className="text-2xs text-status-pending">
          有策略因当前画像未关注而<b>永远不会出卡</b> —— 到画像页调整权重即可纳入。
        </p>
      )}
    </section>
  )
}

export default StrategyPanel

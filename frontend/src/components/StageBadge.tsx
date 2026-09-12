/**
 * 催化剂阶段徽标。
 *
 * **为什么这个组件重要**：用户要的是「提前布局」，所以早期苗头必须被找到；
 * 但绝不能让人把苗头看成确定的事 —— 那会直接误导决策。
 *
 * 阶段字符串的格式是 `层级｜具体阶段`（由后端策略注册表定义），例如：
 *
 *     存量｜重组已完成（限售解禁 / 后续手续）      5 分   ← 召回保留，但不是新催化
 *     早期｜法院受理 / 指定管理人                 28 分  ← 苗头，需醒目提示
 *     进展｜草案 + 评估                          60 分
 *     完成｜监管核准 / 实施完成                    95 分
 *     终止 / 失败                                0 分   ← 触发失效检测
 *
 * 层级配色刻意做成「暖色=不确定 / 冷色=已推进」，让人扫一眼就知道该不该当真。
 */

export type StageTier = '存量' | '早期' | '进展' | '完成' | '终止' | '未知'

const TIER_STYLE: Record<StageTier, { cls: string; dot: string; hint: string }> = {
  存量: {
    cls: 'text-faint border-border',
    dot: 'bg-faint',
    hint: '重组已完成，这是后续手续 —— 不是新的催化事件',
  },
  早期: {
    cls: 'text-status-pending border-status-pending/50 bg-status-pending/10',
    dot: 'bg-status-pending',
    hint: '苗头阶段：确定性低、离价值兑现远，适合提前布局但不宜当作已确认的催化',
  },
  进展: {
    cls: 'text-accent border-accent/40',
    dot: 'bg-accent',
    hint: '交易已进入实质推进阶段',
  },
  完成: {
    cls: 'text-status-confirmed border-status-confirmed/40',
    dot: 'bg-status-confirmed',
    hint: '已获核准 / 已完成 —— 确定性最高，但提前量最小',
  },
  终止: {
    cls: 'text-status-invalid border-status-invalid/50 bg-status-invalid/10',
    dot: 'bg-status-invalid',
    hint: '交易已终止或失败，原投资逻辑失效',
  },
  未知: {
    cls: 'text-faint border-border',
    dot: 'bg-faint',
    hint: '尚未计算阶段',
  },
}

export function parseStage(stage?: string | null): { tier: StageTier; detail: string } {
  if (!stage) return { tier: '未知', detail: '' }
  if (stage.startsWith('终止')) return { tier: '终止', detail: stage.replace(/^终止\s*\/?\s*/, '') }
  const [head, ...rest] = stage.split('｜')
  const tier = (['存量', '早期', '进展', '完成'] as StageTier[]).includes(head as StageTier)
    ? (head as StageTier)
    : '未知'
  return { tier, detail: rest.join('｜') || (tier === '未知' ? stage : '') }
}

export function StageBadge({
  stage,
  early,
  compact = false,
}: {
  stage?: string | null
  early?: boolean
  compact?: boolean
}) {
  const { tier, detail } = parseStage(stage)
  const style = TIER_STYLE[tier]
  // is_early_signal 是后端算出来的权威标志（存量阶段分数也很低，但不是早期）
  const isEarly = early === true
  const label = compact ? tier : detail || tier

  return (
    <span
      className={`chip ${style.cls}`}
      title={`${style.hint}${stage && !compact ? `\n阶段：${stage}` : ''}`}
    >
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${style.dot}`} />
      {isEarly && <span aria-hidden>⚑</span>}
      <span className="text-faint">{tier}</span>
      {label && label !== tier && <span className="opacity-80">· {label}</span>}
    </span>
  )
}

export default StageBadge

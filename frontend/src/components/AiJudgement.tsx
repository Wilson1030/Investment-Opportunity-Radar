/**
 * AI 判断区块（规格 §44：AI 负责解释，不替用户决策）。
 *
 * ★ 为什么抽成组件：原先只渲染在**雷达首页的卡片**上，详情页没有 ——
 * 用户点进详情反而看不到 AI 分析。抽成组件后两处复用，
 * 也让它能被渲染自检覆盖（`npm run render:check`）。
 *
 * 这里刻意把「AI 判断」与「投资 Thesis」**分开呈现**：
 *   · 投资 Thesis 由策略与规则生成（确定性、可追溯到条件与证据）
 *   · AI 判断由模型生成（解释性、可能出错）
 * 两者视觉上必须能区分，否则用户无法判断哪句话是可核对的。
 */
import DivergenceBadge from './DivergenceBadge'

export function AiJudgement({
  summary,
  ruleScore,
  semanticScore,
  divergence,
  divergenceFlagged,
}: {
  summary: string | null | undefined
  ruleScore: number | null
  semanticScore: number | null
  divergence: number | null
  divergenceFlagged: boolean
}) {
  if (!summary) {
    return (
      <p className="text-2xs text-faint">
        AI 判断待生成 —— 节点为 <span className="num">hunt_risk → analyze → score_semantic</span>，
        失败或未开启时这里为空。<b>空白就是空白，不填充占位文案。</b>
      </p>
    )
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted leading-reading border-l-2 border-border-strong pl-3">
        {summary}
      </p>
      <DivergenceBadge
        ruleScore={ruleScore}
        semanticScore={semanticScore}
        divergence={divergence}
        flagged={divergenceFlagged}
      />
    </div>
  )
}

export default AiJudgement

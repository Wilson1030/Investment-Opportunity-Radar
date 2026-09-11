/**
 * 免责声明常驻条（规格第 60 节第 21 条）。
 *
 * **不允许被隐藏**：任何展示分数的页面都必须渲染本组件。
 * 同样地，后端未启动时显示的「离线演示数据」也在这里 —— 绝不让人把演示数据当成真实结果。
 */
export function DisclaimerBanner({
  text,
  offline = false,
}: {
  text?: string
  offline?: boolean
}) {
  return (
    <div className="space-y-1">
      {offline && (
        <div className="flex items-start gap-2 px-3 py-1.5 border border-status-pending/50 bg-status-pending/10 rounded-panel text-2xs">
          <span className="text-status-pending font-bold shrink-0">离线演示数据</span>
          <span className="text-muted">
            未连接到后端（uvicorn 未运行或 8000 端口不可达）。当前页面展示的是内置示例数据，
            <span className="text-status-pending">不是真实计算结果</span>。
          </span>
        </div>
      )}
      <div className="px-3 py-1.5 border border-border rounded-panel text-2xs text-faint leading-relaxed">
        <span className="text-muted font-bold">免责声明 · </span>
        {text ??
          '评分为概率性研究线索的相对排序信号，不构成投资建议，不代表任何收益预期。系统只辅助发现、解释、验证与跟踪，不替用户决策。'}
      </div>
    </div>
  )
}

export default DisclaimerBanner

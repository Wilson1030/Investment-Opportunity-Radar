/**
 * 卡片操作按钮 —— **随当前状态变化**，且关注之后一定有「取消」。
 *
 * ★ 这一条来自用户的直接反馈：
 * > 「像什么重点关注这类的按钮，应该关注之后也要加入取消按钮」
 * > 「没有确认啊」
 *
 * 原先卡片的三个按钮是写死的（确认关注 / 加入跟踪 / 暂时忽略）：
 *   · 点完之后按钮**一个字都不变** → 用户看不出操作生效了（「没有确认」）
 *   · 已关注的卡上找不到「取消关注」→ 用户以为系统不允许撤销
 *
 * 现在按钮由 `actionsFor(status)` 生成，状态一变按钮就变；
 * 并且**撤销类操作**（取消关注 / 恢复关注 / 归档）用不同样式标出来，
 * 让用户一眼看到退路在哪。
 */
import { useState } from 'react'
import api, { ApiError } from '../api/client'
import { actionsFor, STATUS_LABEL, type ActionSpec } from '../lib/actions'

export function CardActions({
  opportunityId,
  status,
  onDone,
}: {
  opportunityId: number
  status: string
  onDone: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const actions = actionsFor(status)

  const run = async (action: ActionSpec) => {
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      const response = action.viaStatus
        ? await api.patchStatus(opportunityId, action.target, `用户操作：${action.key}`)
        : await api.recordAction(opportunityId, action.key, [])
      const message =
        (response as { data?: { message?: string } })?.data?.message ??
        `已执行「${action.label}」`
      // ★ 就地给出确认：用户不必去猜「点了有没有生效」
      setResult(message)
      await onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-2xs text-faint">
          当前：<span className="text-muted">{STATUS_LABEL[status] ?? status}</span>
        </span>
        {actions.map((action) => (
          <button
            key={action.key}
            type="button"
            className={action.undo ? 'btn' : 'btn-primary'}
            disabled={busy}
            title={action.hint}
            onClick={() => run(action)}
          >
            {action.label}
          </button>
        ))}
        {actions.length === 0 && (
          <span className="text-2xs text-faint">（状态机未加载，暂不提供操作）</span>
        )}
      </div>

      {/* ★ 操作确认：点完必须看到「做了什么」 */}
      {result && (
        <p className="text-2xs text-accent border-l-2 border-accent-dim pl-2">{result}</p>
      )}
      {error && (
        <p className="text-2xs text-status-invalid border-l-2 border-status-invalid pl-2">
          {error}
        </p>
      )}
    </div>
  )
}

export default CardActions

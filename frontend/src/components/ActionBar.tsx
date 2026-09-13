/**
 * 操作栏（详情页）—— 与卡片共用 `lib/actions.ts` 的**同一份**定义。
 *
 * ★ 为什么必须只有一份：卡片原先自己写死了三个按钮、详情页另一套，
 * 结果两处能力不一致（卡片上没有「取消关注」，详情页上才有）。
 * 现在「当前状态下能做什么」只在 `lib/actions.ts` 定义一次。
 *
 * 合法迁移仍然由**后端**提供（`/api/health` 的 `status_machine`）——
 * 前端不复制一张合法性表（复制一旦漂移，用户点下去只会得到 4xx，
 * 而界面还一直显示那个按钮）。
 */
import { useEffect, useState } from 'react'
import api, { ApiError } from '../api/client'
import {
  actionsFor,
  isMachineLoaded,
  setStatusMachine,
  STATUS_LABEL,
  statusHint,
  type ActionSpec,
} from '../lib/actions'

let machineRequested = false

/** 从后端取状态机（只取一次，全应用共享） */
async function ensureMachine(): Promise<void> {
  if (isMachineLoaded() || machineRequested) return
  machineRequested = true
  try {
    const { data } = await api.health()
    setStatusMachine(data.status_machine)
  } catch {
    // 拿不到状态机 → 不给任何改状态的按钮（宁可少给，不给错的）
    setStatusMachine()
  }
}

export { ensureMachine }

export function ActionBar({
  opportunityId,
  status,
  onDone,
}: {
  opportunityId: number
  status: string
  onDone: () => void | Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [machineReady, setMachineReady] = useState(isMachineLoaded())

  useEffect(() => {
    if (machineReady) return
    ensureMachine().then(() => setMachineReady(true))
  }, [machineReady])

  const actions = machineReady ? actionsFor(status) : []

  const run = async (action: ActionSpec) => {
    setBusy(true)
    setNotice(null)
    setError(null)
    try {
      const result = action.viaStatus
        ? await api.patchStatus(opportunityId, action.target, `用户操作：${action.key}`)
        : await api.recordAction(opportunityId, action.key, [])
      const message =
        (result as { data?: { message?: string } })?.data?.message ??
        `已执行「${action.label}」`
      setNotice(message)
      await onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel p-3 space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="label">我可以做什么</span>
        <span className="text-2xs text-faint">
          当前状态 <span className="text-muted">{STATUS_LABEL[status] ?? status}</span>
          {actions.length === 0 && ' · 暂不提供操作'}
        </span>
      </div>

      <p className="text-2xs text-faint">{statusHint(status)}</p>

      <div className="flex flex-wrap gap-1.5">
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
      </div>

      {notice && (
        <p className="text-2xs text-accent border-l-2 border-accent-dim pl-2">{notice}</p>
      )}
      {error && (
        <p className="text-2xs text-status-invalid border-l-2 border-status-invalid pl-2">
          {error}
        </p>
      )}
      <p className="text-2xs text-faint leading-reading">
        操作会改变机会状态并记入状态变更历史；画像开启自动学习时，还会
        <b>微调对应投资逻辑的权重</b>
        （单次幅度有上限，你可以在画像页锁定某项以免被调整）。
      </p>
    </section>
  )
}

export default ActionBar

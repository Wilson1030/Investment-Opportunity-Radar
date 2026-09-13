/**
 * 研究卡操作栏 —— 把状态机的**合法迁移**暴露成按钮。
 *
 * ★ 为什么必须完整：
 * 原先只有「确认关注 / 加入跟踪」能改状态，而且两者效果相同（都到 TRACKING）；
 * 「暂时忽略」只记一条日志、状态一动不动 —— 用户点完看不出变化，
 * 认为按钮是摆设。详情页更是**一个操作都没有**。
 *
 * ★ 为什么按状态**显式**列出操作，而不是「按目标状态筛选」
 * （这是实现时踩到的坑）：
 * 第一版是「给每个操作标一个目标状态，再看它在当前状态下是否合法」。
 * 结果「确认关注」（target = tracking）在**已失效**的卡上也合法 ——
 * 于是失效卡上渲染出「确认关注」，而真正该有的「恢复跟踪」被去重逻辑吃掉了。
 * 语义完全反了：失效卡的操作是**撤销失效**，不是「认可这个逻辑」。
 *
 * 所以每个状态显式声明它有哪些操作、叫什么名字。
 * 目标状态仍然要与状态机一致，由 `render:check` 与后端测试共同守住。
 */
import { useEffect, useState } from 'react'
import api, { ApiError } from '../api/client'

interface Action {
  key: string
  label: string
  target: string
  hint: string
  /** 走 /status 接口（直接改状态）还是 /actions 接口（记录表态 + 改状态） */
  viaStatus?: boolean
}

/**
 * 状态机合法迁移 —— **从后端取**（``/api/health`` 的 ``status_machine``）。
 *
 * ★ 为什么不在这里写一张表（实现时踩到的坑）：
 * 第一版本地复制了一份。复制一旦与后端漂移，用户点下去只会得到一个 4xx，
 * 而界面还会一直显示那个不合法的按钮 —— 又是一颗「摆设按钮」。
 * 现在合法性与后端天然同源；取不到时**保守到只有查看**
 * （宁可少给按钮，也不要给一个点了会报错的按钮）。
 */
let ALLOWED: Record<string, Set<string>> = {}
let machineLoaded = false

/** 供离线 / 渲染自检注入状态机（避免在无网络环境下渲染不出按钮） */
export function __setStatusMachine(machine: Record<string, string[]>): void {
  ALLOWED = Object.fromEntries(
    Object.entries(machine).map(([k, v]) => [k, new Set(v)]),
  )
  machineLoaded = true
}

async function ensureMachine(): Promise<void> {
  if (machineLoaded) return
  try {
    const { data } = await api.health()
    ALLOWED = Object.fromEntries(
      Object.entries(data.status_machine ?? {}).map(([k, v]) => [k, new Set(v)]),
    )
    machineLoaded = true
  } catch {
    // 拿不到状态机 → 不给任何改状态的按钮（而不是猜）
    ALLOWED = {}
  }
}

const CONFIRM: Action = {
  key: 'confirmed', label: '确认关注', target: 'tracking',
  hint: '我认可这个投资逻辑，纳入跟踪（并微调画像权重）',
}
const TRACK: Action = {
  key: 'tracked', label: '加入跟踪', target: 'tracking',
  hint: '纳入跟踪（与「确认关注」同为跟踪，区别只在记录你的表态方式）',
}
const IGNORE: Action = {
  key: 'ignored', label: '暂时忽略', target: 'archived',
  hint: '归档 —— 它不再出现在「今日机会」里，但随时可以恢复',
}
const CONFIRM_THESIS: Action = {
  key: 'thesis_confirmed', label: '逻辑成立', target: 'thesis_confirmed',
  hint: '逻辑已被证实（必须走状态接口，不能由反馈接口直接跳）', viaStatus: true,
}
const OBSERVE: Action = {
  key: 'observing', label: '转入观察', target: 'observing',
  hint: '逻辑已成立，转入观察它的实际兑现', viaStatus: true,
}
const ARCHIVE: Action = {
  key: 'archived', label: '归档', target: 'archived',
  hint: '不再跟踪（可恢复）', viaStatus: true,
}
const RESTORE: Action = {
  key: 'restore', label: '恢复跟踪', target: 'tracking',
  hint: '撤销之前的忽略 / 归档，重新纳入跟踪', viaStatus: true,
}
const BACK_TO_PENDING: Action = {
  key: 'pending', label: '回到待确认', target: 'pending_confirmation',
  hint: '重新走一遍确认流程', viaStatus: true,
}

/** 每个状态下的操作（**显式**声明，不靠目标状态反推） */
const ACTIONS_BY_STATUS: Record<string, Action[]> = {
  discovered: [CONFIRM, IGNORE],
  pending_confirmation: [CONFIRM, TRACK, IGNORE],
  tracking: [CONFIRM_THESIS, ARCHIVE],
  thesis_confirmed: [OBSERVE, ARCHIVE],
  observing: [ARCHIVE],
  // ★ 失效卡的操作是「撤销失效」——不是「确认关注」
  invalidated: [RESTORE, BACK_TO_PENDING, ARCHIVE],
  // ★ 归档必须可撤销（按钮承诺过「暂时」）
  archived: [RESTORE, BACK_TO_PENDING],
}

/** 当前状态下可用的操作（同步版：用已加载的状态机过滤） */
export function availableActions(status: string): Action[] {
  const allowed = ALLOWED[status]
  if (!allowed || allowed.size === 0) return []
  // 双保险：显式声明里若混入非法迁移，这里直接过滤掉（而不是渲染出一个会报错的按钮）
  return (ACTIONS_BY_STATUS[status] ?? []).filter((action) => allowed.has(action.target))
}

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
  const [machineReady, setMachineReady] = useState(machineLoaded)

  useEffect(() => {
    if (machineReady) return
    ensureMachine().then(() => setMachineReady(true))
  }, [machineReady])

  const actions = machineReady ? availableActions(status) : []

  const run = async (action: Action) => {
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
          当前状态 <span className="num">{status}</span>
          {actions.length === 0 && ' · 该状态无可用操作'}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {actions.map((action) => (
          <button
            key={action.key}
            type="button"
            className="btn"
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

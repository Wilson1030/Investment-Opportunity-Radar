/**
 * 「当前状态下能做什么」—— 卡片与详情页**共用同一份**定义。
 *
 * ★ 为什么必须共享（这是用户反馈的直接原因）：
 * 卡片上的三个按钮（确认关注 / 加入跟踪 / 暂时忽略）是**写死**的，
 * 不随状态变化。于是：
 *   · 点「确认关注」→ 状态变成「跟踪中」，但按钮还是那三个
 *   · 用户找不到「取消关注」→ 认为按钮没生效 / 没有确认
 *   · 已跟踪的卡还显示「确认关注」→ 再点只得到「无需重复操作」
 *
 * 详情页的 ActionBar 是对的（按状态渲染），但那是另一份实现 ——
 * 一处改了另一处不会改。所以把定义收到这里，两处都用它。
 *
 * ★ 合法迁移由**后端**提供（`/api/health` 的 `status_machine`），
 * 这里只负责「给每个合法目标起一个符合语义的名字」。
 */

export interface ActionSpec {
  key: string
  label: string
  target: string
  hint: string
  /** 走 /status 接口（直接改状态）还是 /actions 接口（记录表态 + 改状态） */
  viaStatus?: boolean
  /** 是否属于「撤销」类操作（界面上用不同样式，让用户一眼看到退路） */
  undo?: boolean
}

/** 状态 → 操作的**完整**定义（目标必须在该状态的合法迁移里，由渲染自检校验） */
export const ACTIONS_BY_STATUS: Record<string, ActionSpec[]> = {
  discovered: [
    { key: 'confirmed', label: '确认关注', target: 'tracking',
      hint: '我认可这个投资逻辑，纳入跟踪' },
    { key: 'ignored', label: '暂时忽略', target: 'archived',
      hint: '归档 —— 它不再出现在「今日机会」里，随时可恢复' },
  ],
  pending_confirmation: [
    { key: 'confirmed', label: '确认关注', target: 'tracking',
      hint: '我认可这个投资逻辑，纳入跟踪' },
    { key: 'ignored', label: '暂时忽略', target: 'archived',
      hint: '归档 —— 它不再出现在「今日机会」里，随时可恢复' },
  ],
  tracking: [
    // ★ 用户要的「取消按钮」：关注之后必须能看到退路
    { key: 'ignored', label: '取消关注', target: 'archived',
      hint: '取消关注并归档 —— 随时可恢复', undo: true },
    { key: 'thesis_confirmed', label: '逻辑成立', target: 'thesis_confirmed',
      hint: '投资逻辑已被证实', viaStatus: true },
  ],
  thesis_confirmed: [
    { key: 'observing', label: '转入观察', target: 'observing',
      hint: '逻辑已成立，转入观察它的实际兑现', viaStatus: true },
    { key: 'archived', label: '归档', target: 'archived',
      hint: '不再跟踪（可恢复）', viaStatus: true, undo: true },
  ],
  observing: [
    { key: 'archived', label: '归档', target: 'archived',
      hint: '不再跟踪（可恢复）', viaStatus: true, undo: true },
  ],
  invalidated: [
    { key: 'restore', label: '恢复跟踪', target: 'tracking',
      hint: '撤销失效判定，重新纳入跟踪', viaStatus: true, undo: true },
    { key: 'pending', label: '回到待确认', target: 'pending_confirmation',
      hint: '重新走一遍确认流程', viaStatus: true, undo: true },
  ],
  archived: [
    { key: 'restore', label: '恢复关注', target: 'tracking',
      hint: '撤销忽略 / 归档，重新纳入跟踪', viaStatus: true, undo: true },
    { key: 'pending', label: '回到待确认', target: 'pending_confirmation',
      hint: '重新走一遍确认流程', viaStatus: true, undo: true },
  ],
}

/** 状态的中文标签（按钮文案与状态提示共用） */
export const STATUS_LABEL: Record<string, string> = {
  discovered: '已发现',
  pending_confirmation: '待确认',
  tracking: '跟踪中',
  thesis_confirmed: '逻辑成立',
  observing: '观察中',
  invalidated: '逻辑失效',
  archived: '已忽略',
}

let ALLOWED: Record<string, Set<string>> = {}
let machineLoaded = false

/** 后端提供的合法迁移（`/api/health` 的 `status_machine`） */
export function setStatusMachine(machine?: Record<string, string[]>): void {
  ALLOWED = Object.fromEntries(
    Object.entries(machine ?? {}).map(([k, v]) => [k, new Set(v)]),
  )
  machineLoaded = true
}

export function isMachineLoaded(): boolean {
  return machineLoaded
}

/**
 * 该状态下可用的操作。
 *
 * ★ 拿不到状态机时返回**空数组**（不给任何改状态的按钮）——
 * 宁可少给按钮，也不要给一个点了会报错的按钮。
 */
export function actionsFor(status: string): ActionSpec[] {
  const allowed = ALLOWED[status]
  if (!allowed || allowed.size === 0) return []
  // 双保险：声明里若混入非法迁移，直接过滤掉
  return (ACTIONS_BY_STATUS[status] ?? []).filter((action) => allowed.has(action.target))
}

/** 状态提示语：解释「这张卡现在处于什么处境」 */
export function statusHint(status: string): string {
  switch (status) {
    case 'tracking':
      return '你已确认关注，它出现在「我的投资逻辑」里'
    case 'thesis_confirmed':
      return '你已判定逻辑成立'
    case 'observing':
      return '逻辑已成立，正在观察兑现'
    case 'invalidated':
      return '投资逻辑已失效 —— 系统仍在跟踪它的变化'
    case 'archived':
      return '已忽略，不在「今日机会」里显示'
    default:
      return '尚未确认，需要你判断'
  }
}

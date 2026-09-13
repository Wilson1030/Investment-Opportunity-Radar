/**
 * API 客户端。
 *
 * 关键设计：后端未启动时自动降级到离线演示数据，并在 `meta.offline` 里明确标注，
 * 由页面渲染「离线演示数据」横幅 —— **绝不让人把演示数据误认为真实结果**。
 */

import {
  FIXTURE_BREAKDOWN,
  FIXTURE_DETAIL,
  FIXTURE_EVENTS,
  FIXTURE_MY_THESIS,
  FIXTURE_RADAR,
} from './fixtures'
import type {
  AlertPayload,
  ApiErrorBody,
  Envelope,
  IngestRunPayload,
  MyThesisPayload,
  OpportunityCard,
  OpportunityDetail,
  ProfilePayload,
  RadarPayload,
  ScoreBreakdown,
  StrategyDef,
  WeightRow,
} from './types'

export const API_BASE = '/api'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message)
  }
}

export interface CallMeta {
  /** true = 后端不可用，当前展示的是离线演示数据 */
  offline: boolean
  generatedAt?: string
  disclaimer?: string
  note?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<{ data: T; meta: CallMeta }> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接后端（请确认 uvicorn 已在 8000 端口运行）')
  }

  const text = await response.text()
  const payload = text ? JSON.parse(text) : {}

  if (!response.ok) {
    const body = payload as ApiErrorBody
    throw new ApiError(
      response.status,
      body.error?.code ?? 'UNKNOWN',
      body.error?.message ?? `HTTP ${response.status}`,
      body.error?.detail ?? {},
    )
  }

  const envelope = payload as Envelope<T>
  return {
    data: envelope.data,
    meta: {
      offline: false,
      generatedAt: envelope.meta?.generated_at as string | undefined,
      disclaimer: envelope.meta?.disclaimer as string | undefined,
      note: envelope.meta?.note as string | undefined,
    },
  }
}

/** 带离线降级的请求 */
async function requestOrFixture<T>(
  path: string,
  fixture: T,
  init?: RequestInit,
): Promise<{ data: T; meta: CallMeta }> {
  try {
    return await request<T>(path, init)
  } catch (error) {
    if (error instanceof ApiError && (error.status === 0 || error.status >= 500)) {
      return { data: fixture, meta: { offline: true } }
    }
    throw error
  }
}

export const api = {
  health: () => request<Record<string, unknown>>('/health'),

  radar: (thesisType?: string | null) =>
    requestOrFixture<RadarPayload>(
      thesisType ? `/radar?thesis_type=${encodeURIComponent(thesisType)}` : '/radar',
      FIXTURE_RADAR,
    ),

  opportunities: (params: Record<string, string> = {}) => {
    const query = new URLSearchParams(params).toString()
    return requestOrFixture<OpportunityCard[]>(
      `/opportunities${query ? `?${query}` : ''}`,
      FIXTURE_RADAR.today.cards,
    )
  },

  opportunity: (id: number) =>
    requestOrFixture<OpportunityDetail>(`/opportunities/${id}`, FIXTURE_DETAIL),

  scoreBreakdown: (id: number) =>
    requestOrFixture<ScoreBreakdown>(`/opportunities/${id}/score-breakdown`, FIXTURE_BREAKDOWN),

  recordAction: (id: number, action: string, reasonThesisTypes: string[] = []) =>
    request<{ action: string; new_status: string; message: string }>(
      `/opportunities/${id}/actions`,
      { method: 'POST', body: JSON.stringify({ action, reason_thesis_types: reasonThesisTypes }) },
    ),

  patchStatus: (id: number, toStatus: string, reason = '') =>
    request<{ to_status: string }>(`/opportunities/${id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ to_status: toStatus, reason }),
    }),

  events: (params: Record<string, string> = {}) => {
    const query = new URLSearchParams(params).toString()
    return requestOrFixture(`/events${query ? `?${query}` : ''}`, FIXTURE_EVENTS)
  },

  alerts: () => requestOrFixture<AlertPayload[]>('/alerts', FIXTURE_RADAR.alerts),

  myThesis: () => requestOrFixture<MyThesisPayload>('/thesis', FIXTURE_MY_THESIS),

  profile: () => requestOrFixture<ProfilePayload>('/profile', FIXTURE_PROFILE),

  weights: () =>
    requestOrFixture<{ weights: WeightRow[]; ai_suggested: unknown[]; normalized_note: string }>(
      '/profile/weights',
      FIXTURE_WEIGHTS,
    ),

  strategies: () => requestOrFixture<StrategyDef[]>('/strategies', FIXTURE_STRATEGIES),

  updateProfile: (body: Record<string, unknown>) =>
    request<Record<string, unknown>>('/profile', { method: 'PUT', body: JSON.stringify(body) }),

  applyTemplate: (template: string) =>
    request<Record<string, unknown>>('/profile/weights', {
      method: 'PUT',
      body: JSON.stringify({ template }),
    }),

  parseNaturalLanguage: (text: string) =>
    request<{
      interpretation: string
      structured: Record<string, unknown>
      unresolved: string[]
      requires_confirmation: boolean
      note?: string
    }>('/strategies/parse-nl', { method: 'POST', body: JSON.stringify({ text }) }),

  runs: () => requestOrFixture<IngestRunPayload[]>('/admin/runs', []),

  llmRuns: () =>
    requestOrFixture<{ data: unknown[]; meta: Record<string, unknown> }>('/admin/llm-runs', {
      data: [],
      meta: {},
    }),
}

// --------------------------------------------------------------------------- #
// 离线演示数据（仅用于后端未启动时）
// --------------------------------------------------------------------------- #
const FIXTURE_PROFILE: ProfilePayload = {
  id: 1,
  name: '默认画像（重组猎手模板）',
  horizon: 'mid',
  markets: ['A股'],
  industry_prefs: [],
  exclusions: [],
  auto_learn_enabled: true,
  accept_early_signals: true,
  locked_weights: ['restructuring'],
  available_templates: ['重组猎手', '困境反转', '价值发现', '成长投资', '事件驱动'],
}

const FIXTURE_WEIGHTS = {
  weights: [
    { thesis_type: 'restructuring' as const, display_name: '重组预期', weight: 0.4, status: 'implemented', locked: true },
    { thesis_type: 'ma_integration' as const, display_name: '并购 / 产业整合', weight: 0.25, status: 'designed', locked: false },
    { thesis_type: 'turnaround' as const, display_name: '困境反转', weight: 0.15, status: 'designed', locked: false },
    { thesis_type: 'shareholder_action' as const, display_name: '股东行为', weight: 0.1, status: 'designed', locked: false },
    { thesis_type: 'event_driven' as const, display_name: '事件驱动', weight: 0.1, status: 'designed', locked: false },
    { thesis_type: 'policy' as const, display_name: '政策驱动', weight: 0, status: 'designed', locked: false },
    { thesis_type: 'cycle' as const, display_name: '行业周期反转', weight: 0, status: 'designed', locked: false },
    { thesis_type: 'growth' as const, display_name: '成长', weight: 0, status: 'designed', locked: false },
    { thesis_type: 'product' as const, display_name: '技术 / 新产品突破', weight: 0, status: 'designed', locked: false },
    { thesis_type: 'value' as const, display_name: '价值发现 / 高股息', weight: 0, status: 'designed', locked: false },
  ],
  ai_suggested: [],
  normalized_note: '权重之和不必为 100%，系统读取时归一化（INV-PW1）',
}

const FIXTURE_STRATEGIES: StrategyDef[] = [
  {
    code: 'restructuring',
    display_name: '重组预期',
    status: 'implemented',
    user_goal: '寻找经营困难、但近期出现重大资产重组、控制权变化、资产注入或破产重整迹象的公司',
    description: '公司处于经营困境，同时出现重组/控制权/资产注入迹象，因此存在潜在重组预期',
    support_event_types: ['RESTRUCTURING', 'ASSET_INJECTION', 'CONTROL_CHANGE', 'BANKRUPTCY_REORGANIZATION', 'M&A'],
    invalidating_event_types: ['RESTRUCTURING', 'CONTROL_CHANGE', 'REGULATORY_RISK', 'ASSET_INJECTION', 'LITIGATION'],
    core_condition_count: 4,
    core_conditions: [
      { key: 'C1', label: '出现重大资产重组相关公告（A 类）', weight: 0.35 },
      { key: 'C2', label: '存在控制权 / 实际控制人变化', weight: 0.25 },
      { key: 'C3', label: '存在资产注入或资产置换迹象', weight: 0.2 },
      { key: 'C4', label: '经营困境背景', weight: 0.2 },
    ],
    open_question_templates: ['交易标的', '交易价格', '重组方案', '资产评估结果', '监管审核结果', '股东大会决议'],
    catalyst_ladder: [
      { stage: '筹划 / 停牌', score: 20 },
      { stage: '预案披露', score: 40 },
      { stage: '草案 + 评估', score: 60 },
      { stage: '股东大会通过', score: 80 },
      { stage: '监管核准 / 实施完成', score: 95 },
    ],
    default_weights: {
      thesis_match: 0.3,
      event_catalyst: 0.25,
      catalyst_strength: 0.1,
      certainty: 0.1,
      fundamentals: 0.05,
      shareholder_structure: 0.1,
      market_attention: 0.05,
      history_case: 0,
    },
    risk_factors: [
      { key: 'event_failure', label: '事件失败可能性', weight: 0.3 },
      { key: 'regulatory', label: '监管 / 合规风险', weight: 0.25 },
      { key: 'fundamentals_deterioration', label: '公司基本面恶化', weight: 0.2 },
      { key: 'pledge', label: '大股东质押 / 资金占用', weight: 0.15 },
      { key: 'tradability', label: '可交易性', weight: 0.1 },
    ],
    anti_patterns: [
      'ST ≠ 重组预期：is_st 只作为 C4 的部分背景证据，禁止「is_st=true → 命中重组策略」的直接规则（INV-C1）',
      '有重组公告 ≠ 重组会成功',
      '重组预期不是只看 ST',
    ],
  },
]

export default api

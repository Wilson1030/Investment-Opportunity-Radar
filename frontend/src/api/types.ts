/**
 * API 类型 —— 由 docs/05-API契约.md 直接翻译。
 * **契约先行**：接口结构变化必须先改 docs/05，再改本文件。
 */

export type OpportunityStatus =
  | 'discovered'
  | 'pending_confirmation'
  | 'tracking'
  | 'thesis_confirmed'
  | 'observing'
  | 'invalidated'
  | 'archived'

export type ThesisType =
  | 'restructuring'
  | 'turnaround'
  | 'value'
  | 'growth'
  | 'event_driven'
  | 'policy'
  | 'cycle'
  | 'product'
  | 'shareholder_action'
  | 'ma_integration'

export type ReliabilityLevel = 'A' | 'B' | 'C' | 'D' | 'E'
export type Freshness = 'new' | 'updated' | 'breaking' | 'stale'
export type AssertionKind = 'fact' | 'inference' | 'hypothesis' | 'market_discussion'
export type ScoreDirection = 'positive' | 'negative'

export interface Envelope<T> {
  data: T
  meta?: { generated_at?: string; disclaimer?: string; [k: string]: unknown }
  page?: { limit: number; offset: number; total: number; has_more: boolean }
}

export interface ApiErrorBody {
  error: { code: string; message: string; detail: Record<string, unknown> }
}

export interface CompanyBrief {
  id: number
  name: string
  code: string
  industry?: string | null
  is_st?: boolean
}

export interface EventBrief {
  id: number
  event_type: string
  event_type_label: string
  title: string
  summary?: string
  event_time?: string | null
  discovery_time?: string | null
  importance: number
  certainty: number
  certainty_level: string
  source_type: string
  source_url?: string
  affected_thesis: string[]
  evidence_ids: number[]
  is_invalidating: boolean
  freshness: Freshness
  relative_time: string
  time_note?: string
  company?: CompanyBrief
}

export interface Evidence {
  id: number
  source_type: string
  source_name: string
  source_url: string
  /** ★ 带页码锚点的深链（如 ...PDF#page=14）——「跳转原文」必须用它，
   *  否则只会停在 PDF 第 1 页，几十页的公告里根本找不到被引用的那句 */
  source_deep_link?: string
  publication_time: string
  reliability_level: ReliabilityLevel
  reliability_note: string
  assertion_kind: AssertionKind
  document_id?: string | null
  relevant_text: string
  page?: number | null
  para_index?: number | null
  extracted_facts: string[]
  confidence: number
  freshness?: Freshness
  relative_time?: string
}

export interface ScoreItem {
  /** 可追溯到规则源码 */
  rule_id: string
  delta: number
  reason: string
  /** 可追溯到原文段落 */
  evidence_ids: number[]
}

export interface ScoreDimension {
  dimension: string
  display_name: string
  raw_value: number
  /** 风险维度为负数（等价扣分比例） */
  weight: number
  weighted_value: number
  direction: ScoreDirection
  /** ★ 风险维度必须带方向说明，否则「风险 43」会被读反 */
  direction_note?: string
  items: ScoreItem[]
}

export interface ScoreBreakdown {
  rule_score: number | null
  semantic_score: number | null
  divergence: number | null
  divergence_flagged: boolean
  risk_score: number | null
  score_version?: string
  computed_at?: string
  dimensions: ScoreDimension[]
  disclaimer?: string
  note?: string
}

export interface OpportunityCard {
  id: number
  company: CompanyBrief
  thesis_type: ThesisType | null
  thesis_display_name: string | null
  status: OpportunityStatus
  status_label: string
  match_score: number | null
  rule_score: number | null
  risk_score: number | null
  semantic_score: number | null
  divergence: number | null
  divergence_flagged: boolean
  why_in_radar: string[]
  summary?: string | null
  latest_events: EventBrief[]
  ai_judgement?: string | null
  evidence_count: number
  contradictory_count?: number
  open_question_count: number
  risk_count: number
  a_grade_evidence_count: number
  /** ★ 为 true 时前端强制显示「仅市场讨论，未经证实」 */
  only_market_discussion: boolean
  /** 催化剂阶段，如「早期｜法院受理 / 指定管理人」（docs/06 的阶梯） */
  catalyst_stage?: string | null
  /** ★ 是否为早期苗头：确定性低，必须显式提示，不能当作已确认的催化 */
  is_early_signal?: boolean
  next_events_to_watch: string[]
  risks?: string[]
  score_version?: string
  first_discovered_at: string
  last_updated_at: string
}

export interface ThesisPayload {
  id: number
  thesis_type: ThesisType
  display_name: string
  statement: string
  why_now: { past?: string | null; recent?: string | null; this_week?: string | null; conclusion?: string | null }
  supporting_evidence: Evidence[]
  contradictory_evidence: Evidence[]
  invalidating_events: { event_type: string; severity: string; description: string }[]
}

export interface OpenQuestion {
  id: number
  question: string
  status: 'open' | 'confirmed' | 'obsolete'
  confirmed_evidence_id?: number | null
}

export interface OpportunityDetail {
  card: OpportunityCard
  thesis: ThesisPayload | null
  open_questions: OpenQuestion[]
  confirmed_facts: string[]
  risks: { factor: string; weight: number | null; severity: string | null; description: string }[]
  next_events_to_watch: string[]
  timeline: { date: string | null; event_type: string; title: string; evidence_id: number | null }[]
  evidence: Evidence[]
  news_clusters: unknown[]
  market: { note: string }
  status_history: {
    from_status: string | null
    to_status: string
    reason: string
    score_before: number | null
    score_after: number | null
    changed_at: string
  }[]
  disclaimer?: string
}

export interface RadarPayload {
  profile: {
    id: number
    name: string
    top_weights: { thesis_type: ThesisType; display_name: string; weight: number }[]
    auto_learn_enabled: boolean
    accept_early_signals?: boolean
    locked_weights: string[]
  }
  today: {
    date: string
    is_trading_day: boolean
    calendar_degraded: boolean
    calendar_warning?: string | null
    new_count: number
    cards: OpportunityCard[]
  }
  counts: Record<OpportunityStatus, number>
  recent_events: EventBrief[]
  alerts: {
    id: number
    alert_type: string
    title: string
    message: string
    suggestion?: string | null
    opportunity_id: number
    score_before: number | null
    score_after: number | null
    is_read: boolean
    created_at: string
    relative_time?: string
  }[]
  pipeline: {
    last_run_at?: string | null
    dry_run?: boolean | null
    funnel?: Record<string, number>
    quality?: Record<string, unknown>
    hint?: string | null
  }
  disclaimer?: string
}

export interface StrategyDef {
  code: ThesisType
  display_name: string
  status: 'implemented' | 'designed' | 'planned'
  user_goal: string
  description: string
  support_event_types: string[]
  invalidating_event_types: string[]
  core_condition_count: number
  core_conditions: { key: string; label: string; weight: number }[]
  open_question_templates: string[]
  catalyst_ladder: { stage: string; score: number }[]
  default_weights: Record<string, number>
  risk_factors: { key: string; label: string; weight: number }[]
  anti_patterns: string[]
}

export interface ProfilePayload {
  id: number
  name: string
  horizon?: string | null
  markets: string[]
  industry_prefs: string[]
  exclusions: string[]
  auto_learn_enabled: boolean
  /** 是否把「早期苗头」纳入关注范围（预重整 / 重整申请 / 法院受理 / 筹划停牌） */
  accept_early_signals?: boolean
  locked_weights: string[]
  available_templates: string[]
}

export interface WeightRow {
  thesis_type: ThesisType
  display_name: string
  weight: number
  status: string
  locked: boolean
}

export interface MyThesisPayload {
  total_tracking: number
  by_type: { display_name: string; count: number }[]
  items: {
    id: number
    opportunity_id: number
    company: CompanyBrief
    thesis_type: ThesisType
    display_name: string
    statement: string
    status: OpportunityStatus
    rule_score: number | null
    risk_score: number | null
    evidence_count: number
    contradictory_count: number
    open_question_count: number
    invalidating_events: string[]
    next_events_to_watch: string[]
    last_updated_at: string
    has_unread_alert: boolean
    catalyst_stage?: string | null
    is_early_signal?: boolean
  }[]
  disclaimer?: string
}

/** 事件流响应 */
export interface EventsFixture {
  data: EventBrief[]
  page: { limit: number; offset: number; total: number; has_more: boolean }
}

/** 提醒 */
export interface AlertPayload {
  id: number
  alert_type: string
  title: string
  message: string
  suggestion?: string | null
  opportunity_id: number
  score_before: number | null
  score_after: number | null
  is_read: boolean
  created_at: string
  relative_time?: string
}

/** 管理端运行记录（可观测） */
export interface IngestRunPayload {
  id: number
  adapter: string
  scope: string
  stage: string
  dry_run: boolean
  started_at: string
  finished_at: string | null
  items_found: number
  items_new: number
  items_skipped: number
  items_failed: number
  parse_failed: number
  parse_needs_ocr: number
  parse_failure_rate: number | null
  field_missing_rate: number | null
  funnel: Record<string, number>
  quality: Record<string, unknown>
  error_summary: string | null
}

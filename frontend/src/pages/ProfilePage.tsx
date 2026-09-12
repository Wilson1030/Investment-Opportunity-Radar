import { useEffect, useState } from 'react'
import api, { ApiError } from '../api/client'
import type { ProfilePayload, StrategyDef, WeightRow } from '../api/types'
import DisclaimerBanner from '../components/DisclaimerBanner'

/**
 * Profile（M10-07 / 规格 §4、§34.6）。
 *
 * ★ 规格 §4.1：**不提供「保守 / 稳健 / 激进」三档分类** ——
 * 本项目需要的是「投资逻辑偏好」，允许多选并可为每项设权重。
 *
 * 策略列表显示实现状态：`implemented` / `designed`（D13：设计全量、实现逐个），
 * 并展示每类策略的**失效条件**与**反例警示** —— 这两项是产品可信度的一部分。
 */
export function ProfilePage() {
  const [profile, setProfile] = useState<ProfilePayload | null>(null)
  const [weights, setWeights] = useState<WeightRow[]>([])
  const [strategies, setStrategies] = useState<StrategyDef[]>([])
  const [offline, setOffline] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [nlText, setNlText] = useState('')
  const [nlResult, setNlResult] = useState<{ interpretation: string; requires_confirmation: boolean } | null>(null)

  const load = () => {
    Promise.all([api.profile(), api.weights(), api.strategies()])
      .then(([p, w, s]) => {
        setProfile(p.data)
        setWeights(w.data.weights)
        setStrategies(s.data)
        setOffline(p.meta.offline || w.meta.offline || s.meta.offline)
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)))
  }

  useEffect(load, [])

  const applyTemplate = async (name: string) => {
    setMessage(null)
    try {
      await api.applyTemplate(name)
      setMessage(`已套用模板「${name}」`)
      load()
    } catch (err) {
      setMessage(err instanceof ApiError ? `${err.code}：${err.message}` : String(err))
    }
  }

  const saveEarlyToggle = async (enabled: boolean) => {
    setMessage(null)
    try {
      await api.updateProfile({ accept_early_signals: enabled })
      setProfile((prev) => (prev ? { ...prev, accept_early_signals: enabled } : prev))
      setMessage(enabled ? '已开启早期苗头信号' : '已关闭早期信号 —— 只看「进展」及之后的机会')
    } catch (err) {
      setMessage(err instanceof ApiError ? `${err.code}：${err.message}` : String(err))
    }
  }

  const parseNl = async () => {
    setNlResult(null)
    try {
      const { data } = await api.parseNaturalLanguage(nlText)
      setNlResult({ interpretation: data.interpretation, requires_confirmation: data.requires_confirmation })
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : String(err))
    }
  }

  if (error && !profile) {
    return <div className="panel p-4 text-xs text-status-invalid">加载失败：{error}</div>
  }
  if (!profile) return <div className="text-xs text-faint">加载中…</div>

  return (
    <div className="space-y-3">
      <DisclaimerBanner offline={offline} />

      {message && (
        <div className="panel p-2.5 text-2xs text-accent border-accent/40">{message}</div>
      )}

      {/* 画像基本信息 */}
      <section className="panel p-3 space-y-3">
        <div>
          <div className="label mb-1">画像</div>
          <div className="text-sm">{profile.name}</div>
          <div className="text-2xs text-faint mt-0.5">
            市场：{profile.markets.join('、') || '—'} · 周期：{profile.horizon ?? '—'} ·
            排除：{profile.exclusions.join('、') || '无'}
          </div>
        </div>

        <div className="hairline" />

        {/* ★ 规格 §4.1 的反向验证：这里没有风险偏好三档 */}
        <div>
          <div className="label mb-1">投资逻辑偏好与权重</div>
          <p className="text-2xs text-faint mb-2">
            权重之和不必为 100%，系统读取时归一化（INV-PW1）。锁定的项不会被自动学习覆盖，
            但你可以随时手动修改（M1-06）。
          </p>
          <ul className="space-y-1.5">
            {weights.map((row) => (
              <li key={row.thesis_type} className="flex items-center gap-3">
                <span className="text-xs w-32 shrink-0 flex items-center gap-1.5">
                  {row.display_name}
                  {row.locked && (
                    <span className="chip text-accent border-accent/40" title="已锁定，自动学习不得覆盖">
                      锁
                    </span>
                  )}
                </span>
                <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
                  <div
                    className="h-full bg-accent/60"
                    style={{ width: `${Math.min(100, row.weight * 200)}%` }}
                  />
                </div>
                <span className="num text-2xs text-faint w-10 text-right">
                  {(row.weight * 100).toFixed(0)}%
                </span>
                <span
                  className={`chip shrink-0 ${
                    row.status === 'implemented'
                      ? 'text-reliability-a border-reliability-a/40'
                      : 'text-faint border-border'
                  }`}
                  title={
                    row.status === 'implemented'
                      ? '该策略已完整实现'
                      : '设计已完成（规则 / 失效条件 / 评分权重齐备），实现待排期'
                  }
                >
                  {row.status === 'implemented' ? '已实现' : '设计中'}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="hairline" />

        {/* ★ 早期苗头开关：用户要「提前布局」时开；不想看低确定性信号时关 */}
        <div>
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              className="mt-1 accent-[#2DD4BF]"
              checked={profile.accept_early_signals ?? true}
              onChange={(e) => saveEarlyToggle(e.target.checked)}
            />
            <span>
              <span className="text-xs text-text">把「早期苗头」纳入关注范围</span>
              <span className="block text-2xs text-faint leading-relaxed mt-0.5">
                包含：预重整 · 债权人申请重整 · 法院受理重整 · 筹划停牌 · 意向协议。
                开启后这类低确定性信号会出现在 Radar 上（并标注「⚑ 早期」）；
                关闭则只在机会推进到「进展」阶段后才出现。
              </span>
            </span>
          </label>
        </div>

        <div className="hairline" />

        <div>
          <div className="label mb-1.5">预设策略模板</div>
          <div className="flex flex-wrap gap-1.5">
            {profile.available_templates.map((name) => (
              <button key={name} type="button" className="btn" onClick={() => applyTemplate(name)}>
                {name}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* 自然语言策略（M1-04） */}
      <section className="panel p-3 space-y-2">
        <div className="label">用自然语言描述你的投资逻辑</div>
        <textarea
          className="w-full bg-bg border border-border rounded-sm p-2 text-xs text-text
                     focus:border-accent-dim focus:outline-none"
          rows={3}
          value={nlText}
          onChange={(e) => setNlText(e.target.value)}
          placeholder="例如：我想找那些连续亏损，但是最近出现重组、资产注入或者大股东变化迹象的 ST 股票。"
        />
        <div className="flex items-center gap-2">
          <button type="button" className="btn-primary" onClick={parseNl} disabled={!nlText.trim()}>
            解析为结构化策略
          </button>
          <span className="text-2xs text-faint">
            系统会先复述它的理解，等你确认后才执行扫描（不会直接开始扫描）
          </span>
        </div>

        {nlResult && (
          <div className="panel-2 p-2.5 space-y-1">
            <div className="text-2xs text-accent">
              {nlResult.requires_confirmation ? '需要你确认' : '已确认'}
            </div>
            <div className="text-xs text-muted">{nlResult.interpretation}</div>
          </div>
        )}
      </section>

      {/* 策略注册表：设计全量、实现逐个 */}
      <section className="space-y-2">
        <div className="label px-1">策略注册表（10 类 · 设计全量，实现逐个）</div>
        {strategies.map((strategy) => (
          <article key={strategy.code} className="panel p-3 space-y-2">
            <header className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold">{strategy.display_name}</span>
              <span
                className={`chip ${
                  strategy.status === 'implemented'
                    ? 'text-reliability-a border-reliability-a/40'
                    : 'text-faint border-border'
                }`}
              >
                {strategy.status === 'implemented' ? '已实现' : '设计中'}
              </span>
              <span className="num text-2xs text-faint">
                核心条件 {strategy.core_condition_count} 项 · 正向权重合计{' '}
                {Object.values(strategy.default_weights)
                  .reduce((a, b) => a + b, 0)
                  .toFixed(2)}
              </span>
            </header>

            <p className="text-2xs text-faint">{strategy.user_goal}</p>

            <div>
              <div className="label mb-1">核心条件</div>
              <ul className="text-2xs text-muted space-y-0.5">
                {strategy.core_conditions.map((condition) => (
                  <li key={condition.key}>
                    {condition.key}（权重 {condition.weight}）· {condition.label}
                  </li>
                ))}
              </ul>
            </div>

            {/* ★ 失效条件 */}
            <div>
              <div className="label mb-1">逻辑失效条件</div>
              <div className="flex flex-wrap gap-1">
                {strategy.invalidating_event_types.map((event) => (
                  <span key={event} className="chip text-status-invalid/80 border-status-invalid/30">
                    {event}
                  </span>
                ))}
              </div>
            </div>

            {/* ★ 反例警示 —— 产品可信度的一部分 */}
            {strategy.anti_patterns.length > 0 && (
              <div className="panel-2 p-2 space-y-1">
                <div className="text-2xs text-status-pending">反例警示（这类判断是被明确禁止的）</div>
                <ul className="text-2xs text-muted space-y-0.5">
                  {strategy.anti_patterns.map((item) => (
                    <li key={item}>· {item}</li>
                  ))}
                </ul>
              </div>
            )}
          </article>
        ))}
      </section>
    </div>
  )
}

export default ProfilePage

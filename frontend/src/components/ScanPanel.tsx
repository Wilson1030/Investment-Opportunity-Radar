/**
 * 运行扫描（规格 §27：采集入口）。
 *
 * ★ 为什么要有这一块：
 * 系统的采集原先**只能从命令行跑**（`python -m app.ingest`）。
 * 用户装好系统、打开界面，看到的是「今日暂无机会卡」加一行命令提示 ——
 * 而这是个本地单用户的应用，让用户去敲命令行不算「可以直接使用」。
 *
 * 另外这里刻意暴露「关键词」与「回看天数」两个参数：
 * 全市场扫描一次要处理上千条公告、几百次模型调用；
 * 用户往往只想先针对某个方向看看（例如「重整」「分红」），
 * 那就该能自己收窄范围，而不是等一次全量跑完。
 */
import { useState } from 'react'
import api, { ApiError } from '../api/client'

interface ScanResult {
  candidates?: number
  cards?: number
  events_extracted?: number
  created?: number
}

export function ScanPanel({ onFinished }: { onFinished: () => void | Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [searchkey, setSearchkey] = useState('')
  const [lookback, setLookback] = useState(120)
  const [live, setLive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ScanResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      const response = await api.triggerScan({
        searchkey,
        lookback_days: lookback,
        live,
      })
      const payload = response as {
        data?: { funnel?: ScanResult }
        meta?: { cards?: number; created?: number }
      }
      setResult({
        ...(payload.data?.funnel ?? {}),
        cards: payload.meta?.cards,
        created: payload.meta?.created,
      })
      await onFinished()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <button type="button" className="btn" onClick={() => setOpen(true)}>
        运行扫描…
      </button>
    )
  }

  return (
    <section className="panel p-3 space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="label">运行扫描</span>
        <button type="button" className="text-2xs text-faint" onClick={() => setOpen(false)}>
          收起
        </button>
      </div>

      <div className="flex flex-wrap items-end gap-3 text-2xs">
        <label className="flex flex-col gap-1">
          <span className="text-faint">全文检索关键词（留空 = 全市场）</span>
          <input
            className="bg-bg border border-border rounded-sm px-2 py-1 text-xs text-text w-40
                       focus:border-accent-dim focus:outline-none"
            value={searchkey}
            placeholder="如：重整 / 分红 / 中标"
            onChange={(event) => setSearchkey(event.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-faint">回看天数</span>
          <input
            type="number"
            min={1}
            max={400}
            className="bg-bg border border-border rounded-sm px-2 py-1 text-xs text-text w-20 num
                       focus:border-accent-dim focus:outline-none"
            value={lookback}
            onChange={(event) => setLookback(Number(event.target.value) || 90)}
          />
        </label>
        <label className="flex items-center gap-1.5 text-faint">
          <input
            type="checkbox"
            checked={live}
            onChange={(event) => setLive(event.target.checked)}
          />
          真写库（不勾选 = dry-run，只出报告）
        </label>
        <button type="button" className="btn-primary" disabled={busy} onClick={run}>
          {busy ? '扫描中…（需等它跑完）' : '开始扫描'}
        </button>
      </div>

      <p className="text-2xs text-faint leading-reading">
        扫描会真实请求巨潮公告、同花顺财务、百度估值与财经新闻，并对命中的公告调用本地模型抽取事件。
        <b>不勾选「真写库」时不会改动任何业务数据</b>（用于先看一遍会命中什么）。
      </p>

      {result && (
        <div className="text-2xs text-accent space-y-0.5">
          <div>扫描完成：</div>
          <div className="num text-muted">
            候选公司 {result.candidates ?? '—'} 家 · 抽出事件 {result.events_extracted ?? '—'} 条 ·
            新建卡片 {result.created ?? '—'} 张 · 当前卡片 {result.cards ?? '—'} 张
          </div>
        </div>
      )}
      {error && (
        <p className="text-2xs text-status-invalid border-l-2 border-status-invalid pl-2">
          {error}
        </p>
      )}
    </section>
  )
}

export default ScanPanel

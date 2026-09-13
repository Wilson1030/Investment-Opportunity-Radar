/**
 * 渲染自检：把真实组件渲染成 HTML，断言「数据真的出现在页面上」。
 *
 * 为什么需要它：`tsc` 通过只说明类型对，`vite build` 通过只说明能打包 ——
 * **都不能证明数据被渲染出来了**。这个脚本真的执行组件，
 * 从而抓到「字段名写错」「空值没兜住」「map 里用了不存在的键」这类运行时问题。
 *
 * 用法：cd frontend && node scripts/render_check.mjs
 */
import { build } from 'esbuild'
import { createRequire } from 'node:module'
import { mkdtempSync, rmSync, readdirSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, dirname } from 'node:path'

// 用脚本自身位置定位项目根 —— 这样从任何 cwd 调用都能工作
const here = dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
const src = resolve(here, '..', 'src').replace(/\\/g, '/')

const entry = `
import { renderToStaticMarkup } from 'react-dom/server'
import { createElement } from 'react'
import { FinancialsPanel } from '${src}/components/FinancialsPanel'
import { AiJudgement } from '${src}/components/AiJudgement'
import { ActionBar, __setStatusMachine } from '${src}/components/ActionBar'
import { FIXTURE_DETAIL } from '${src}/api/fixtures'

export const html = renderToStaticMarkup(
  FinancialsPanel({
    financials: FIXTURE_DETAIL.financials,
    signals: FIXTURE_DETAIL.financial_signals,
  }),
)
export const empty = renderToStaticMarkup(FinancialsPanel({ financials: [], signals: [] }))

// AI 判断区块：有内容 / 空内容两种
const aiCard = FIXTURE_DETAIL.card
export const ai = renderToStaticMarkup(
  AiJudgement({
    summary: aiCard.ai_judgement,
    ruleScore: aiCard.rule_score,
    semanticScore: aiCard.semantic_score,
    divergence: aiCard.divergence,
    divergenceFlagged: aiCard.divergence_flagged,
  }),
)
// 操作栏：待确认状态下的可用操作
//
// ★ 必须用 createElement 真正渲染，**不能**像上面两个纯组件那样直接调用函数 ——
// ActionBar 用了 useState，直接调用会抛
// 「Invalid hook call / Cannot read properties of null (reading 'useState')」。
// （FinancialsPanel 与 AiJudgement 是无状态纯组件，才能那样调。）
// 注入与后端一致的状态机（真实运行时由 /api/health 提供）
__setStatusMachine({
  discovered: ['archived', 'pending_confirmation'],
  pending_confirmation: ['archived', 'invalidated', 'tracking'],
  tracking: ['archived', 'invalidated', 'thesis_confirmed'],
  thesis_confirmed: ['invalidated', 'observing'],
  observing: ['archived', 'invalidated'],
  invalidated: ['archived', 'pending_confirmation', 'tracking'],
  archived: ['pending_confirmation', 'tracking'],
})

export const actions = renderToStaticMarkup(
  createElement(ActionBar, {
    opportunityId: 1, status: 'pending_confirmation', onDone: () => {},
  }),
)
export const actionsInvalidated = renderToStaticMarkup(
  createElement(ActionBar, {
    opportunityId: 1, status: 'invalidated', onDone: () => {},
  }),
)

export const aiEmpty = renderToStaticMarkup(
  AiJudgement({
    summary: null,
    ruleScore: 0,
    semanticScore: null,
    divergence: null,
    divergenceFlagged: false,
  }),
)
`

const dir = mkdtempSync(join(tmpdir(), 'radar-render-'))
const outFile = join(dir, 'bundle.cjs')

// 用 stdin + resolveDir 而不是写临时入口文件：
// 临时文件在项目外，esbuild 从它的目录往上找 node_modules，解析不到 react-dom
await build({
  stdin: { contents: entry, resolveDir: resolve(here, '..'), loader: 'jsx' },
  bundle: true,
  // 用 CJS 而不是 ESM：react-dom/server 是 CJS，打包成 ESM 后
  // 它的 require('stream') 会变成不支持的动态 require
  format: 'cjs',
  platform: 'node',
  outfile: outFile,
  jsx: 'automatic',
  loader: { '.tsx': 'tsx', '.ts': 'ts' },
  resolveExtensions: ['.tsx', '.ts', '.jsx', '.js'],
  logLevel: 'error',
})

const mod = createRequire(import.meta.url)(outFile)
const html = mod.html
const empty = mod.empty
const ai = mod.ai
const aiEmpty = mod.aiEmpty
const actions = mod.actions
const actionsInvalidated = mod.actionsInvalidated

/** 断言表：字符串必须在渲染结果里出现（或必须不出现） */
const must = [
  '基本面数据',
  '营业总收入',
  '净利润',
  '经营现金流净额',
  '毛利率',
  '资产负债率',
  '每股经营现金流',
  '应收周转天数',
  '20.10 亿',           // 金额换算成亿元（fixture 是 20.1）
  '18.0%',              // 比率显示成百分比（0.18）
  '68.0%',              // 资产负债率
  '2026H1',             // 报告期标签，不是日期
  '2025A',
  '连续亏损年数',
  '影响「经营困境」C4',  // 中性 impact
  '×',                  // 不利条件
  '✓',                  // 有利条件
]
const mustNot = ['推高', 'undefined', 'NaN', '[object Object]']

let failed = 0
for (const needle of must) {
  if (!html.includes(needle)) {
    console.error(`  ✗ 缺少：${needle}`)
    failed += 1
  } else {
    console.log(`  ✓ ${needle}`)
  }
}
for (const needle of mustNot) {
  if (html.includes(needle)) {
    console.error(`  ✗ 不该出现：${needle}`)
    failed += 1
  }
}

// 空数据必须优雅降级，而不是崩、也不是显示 0
if (!empty.includes('未采集到结构化财务数据')) {
  console.error('  ✗ 空数据没有降级提示')
  failed += 1
} else {
  console.log('  ✓ 空数据降级提示')
}
if (empty.includes('亿元')) {
  console.error('  ✗ 空数据下仍显示了金额 —— 疑似用 0 填充')
  failed += 1
}

// ②b AI 判断区块：模型叙事必须出现在页面上，空值时必须明说「待生成」
// 注意：组件本身不含「AI 判断」标题（那是父级区块的 label），
// 所以这里断言的是**叙事文本**与**语义分徽标**。
if (!ai.includes('重组预期策略') || !ai.includes('语义分')) {
  console.error('  ✗ AI 判断区块没有渲染出叙事或语义分徽标')
  failed += 1
} else {
  console.log('  ✓ AI 判断区块（叙事 + 语义分徽标）')
}
if (!aiEmpty.includes('待生成')) {
  console.error('  ✗ AI 叙事为空时没有提示「待生成」')
  failed += 1
} else {
  console.log('  ✓ AI 叙事为空时明说待生成（不用占位文案填充）')
}

// ②c 操作栏：按钮必须真的渲染出来，且随状态变化
if (!actions.includes('确认关注') || !actions.includes('暂时忽略')) {
  console.error('  ✗ 待确认状态没有渲染出操作按钮')
  failed += 1
} else {
  console.log('  ✓ 操作按钮（待确认 → 确认关注 / 暂时忽略）')
}
// 无效状态只能归档或恢复 —— 不能出现「确认关注」这种非法迁移
if (actionsInvalidated.includes('确认关注')) {
  console.error('  ✗ 已失效状态渲染了非法操作「确认关注」')
  failed += 1
} else {
  console.log('  ✓ 操作按钮随状态机变化（失效状态不出现非法迁移）')
}
if (!actionsInvalidated.includes('恢复跟踪')) {
  console.error('  ✗ 失效状态没有提供「恢复跟踪」—— 归档/失效无法撤销')
  failed += 1
} else {
  console.log('  ✓ 失效状态提供「恢复跟踪」（可撤销）')
}

// ③ 全项目扫「JSX 文本里的字面 **」—— Markdown 粗体在 JSX 里不会生效，
//    会原样显示成星号（实测抓到 3 处）。用 <b> 才是对的。
const literalMarks = []
// 匹配 **粗体**：两个星号 + 非星号内容 + 两个星号
const literalBold = new RegExp('[*][*][^*]+[*][*]')
const walk = (dirPath) => {
  for (const entry of readdirSync(dirPath, { withFileTypes: true })) {
    const full = join(dirPath, entry.name)
    if (entry.isDirectory()) { walk(full); continue }
    if (!entry.name.endsWith('.tsx')) continue
    readFileSync(full, 'utf8').split(String.fromCharCode(10)).forEach((line, i) => {
      const s = line.trim()
      if (s.startsWith('*') || s.startsWith('/*') || s.startsWith('//')) return
      // 用 String 构造正则，避免在源码里出现需要转义的星号
      if (literalBold.test(line)) literalMarks.push(entry.name + ':' + (i + 1))
    })
  }
}
walk(resolve(here, '..', 'src'))
if (literalMarks.length) {
  console.error(`  ✗ JSX 里有字面 ** （Markdown 不会生效）：${literalMarks.join(', ')}`)
  failed += 1
} else {
  console.log('  ✓ 无字面 ** 残留')
}

rmSync(dir, { recursive: true, force: true })

if (failed) {
  console.error(`\n渲染自检失败：${failed} 项`)
  process.exit(1)
}
console.log('\n渲染自检通过')

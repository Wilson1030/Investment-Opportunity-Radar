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
import { ActionBar } from '${src}/components/ActionBar'
import { CardActions } from '${src}/components/CardActions'
import { setStatusMachine } from '${src}/lib/actions'
import { ScanPanel } from '${src}/components/ScanPanel'
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
setStatusMachine({
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

// ★ 卡片按钮：待确认 / 已关注 / 已忽略 —— **必须互不相同**，
//   且关注后必须出现「取消关注」（用户反馈：「关注之后也要加入取消按钮」）
export const cardPending = renderToStaticMarkup(
  createElement(CardActions, {
    opportunityId: 1, status: 'pending_confirmation', onDone: () => {},
  }),
)
export const cardTracking = renderToStaticMarkup(
  createElement(CardActions, {
    opportunityId: 1, status: 'tracking', onDone: () => {},
  }),
)
export const cardArchived = renderToStaticMarkup(
  createElement(CardActions, {
    opportunityId: 1, status: 'archived', onDone: () => {},
  }),
)

export const scan = renderToStaticMarkup(
  createElement(ScanPanel, { onFinished: () => {} }),
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
const cardPending = mod.cardPending
const cardTracking = mod.cardTracking
const cardArchived = mod.cardArchived
const scan = mod.scan

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

// ②d 扫描面板：必须能从界面触发采集（否则用户只能去敲命令行）
if (!scan.includes('运行扫描')) {
  console.error('  ✗ 扫描面板没有渲染出「运行扫描」入口')
  failed += 1
} else {
  console.log('  ✓ 扫描面板（可从界面触发采集）')
}

// ②e ★ 卡片按钮必须随状态变化（用户反馈：「关注之后也要加入取消按钮」）
if (!cardPending.includes('确认关注')) {
  console.error('  ✗ 待确认的卡片没有「确认关注」')
  failed += 1
} else {
  console.log('  ✓ 卡片按钮（待确认 → 确认关注）')
}
if (cardPending.includes('取消关注')) {
  console.error('  ✗ 尚未关注就出现了「取消关注」')
  failed += 1
}
if (!cardTracking.includes('取消关注')) {
  console.error('  ✗ 已关注的卡片没有「取消关注」—— 用户找不到退路')
  failed += 1
} else {
  console.log('  ✓ 已关注的卡片提供「取消关注」（有关注就有取消）')
}
if (cardTracking.includes('确认关注')) {
  console.error('  ✗ 已关注的卡片仍显示「确认关注」—— 点了只会得到「无需重复操作」')
  failed += 1
}
if (!cardArchived.includes('恢复关注')) {
  console.error('  ✗ 已忽略的卡片没有「恢复关注」')
  failed += 1
} else {
  console.log('  ✓ 已忽略的卡片提供「恢复关注」（暂时忽略真的可以撤销）')
}
if (cardPending === cardTracking || cardTracking === cardArchived) {
  console.error('  ✗ 不同状态渲染出了相同的按钮组（按钮没随状态变化）')
  failed += 1
} else {
  console.log('  ✓ 三个状态的按钮组互不相同')
}

// ②f ★ 详情页的 7 步编号必须**按顺序**出现（规格 §45）
//
// 实测踩到：区块编号曾经是 ② → ①b → ②b → ③ → ① → ④…
// —— ① 排在 ③ 后面，用户读起来完全乱套。
// 编号不连续比没有编号更糟：它在暗示一个不存在的阅读顺序。
{
  const pageSource = readFileSync(
    resolve(here, '..', 'src', 'pages', 'OpportunityPage.tsx'),
    'utf8',
  )
  const order = ['①', '②', '③', '④', '⑤', '⑥', '⑦']
  const positions = order.map((m) => pageSource.indexOf(m + ' '))
  const missing = order.filter((_, i) => positions[i] < 0)
  if (missing.length) {
    console.error(`  ✗ 详情页缺少步骤编号：${missing.join(' ')}`)
    failed += 1
  } else {
    const sorted = positions.every((p, i) => i === 0 || p > positions[i - 1])
    if (!sorted) {
      console.error(`  ✗ 详情页步骤编号顺序错乱：${JSON.stringify(positions)}`)
      failed += 1
    } else {
      console.log('  ✓ 详情页 7 步编号按顺序出现（①→⑦）')
    }
  }
}

// ②g ★ 状态机的加载必须由**应用级**触发，不能只挂在某一个组件上
//
// 实测踩到（看真实截图才发现的）：只有详情页的 ActionBar 会加载状态机，
// 于是**用户没进过详情页时，雷达首页的卡片一个操作按钮都没有**，
// 只显示「状态机未加载，暂不提供操作」。
// 这类 bug 渲染自检抓不到（自检里是显式注入状态机的）——
// 所以这里改为检查「谁负责触发加载」这个**契约**。
{
  const cardSrc = readFileSync(
    resolve(here, '..', 'src', 'components', 'CardActions.tsx'), 'utf8',
  )
  const appSrc = readFileSync(resolve(here, '..', 'src', 'App.tsx'), 'utf8')
  if (!cardSrc.includes('ensureMachine')) {
    console.error('  ✗ CardActions 没有触发状态机加载 —— 没进过详情页时卡片上没有按钮')
    failed += 1
  } else if (!appSrc.includes('ensureMachine')) {
    console.error('  ✗ App 没有在启动时加载状态机 —— 按钮要等用户碰对页面才出现')
    failed += 1
  } else {
    console.log('  ✓ 状态机由应用级加载，卡片也各自兜底（按钮不会缺席）')
  }
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
    // ★ 逐行扫描，但必须跳过 **JSX 注释块** ``{/* … */}``。
    //   注释里的强调符永远不会渲染到页面上 —— 实测踩到：
    //   我在 ``{/* ★ 按钮**随状态变化** */}`` 里写了强调，被这条守卫误报。
    //   守卫写得比真实约束更严，只会逼着后来者把守卫删掉。
    let inJsxComment = false
    readFileSync(full, 'utf8').split(String.fromCharCode(10)).forEach((line, i) => {
      const s = line.trim()
      if (inJsxComment) {
        if (s.includes('*/')) inJsxComment = false
        return
      }
      if (s.includes('{/*')) {
        if (!s.includes('*/')) inJsxComment = true
        return
      }
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

/**
 * 主题集中管理（D05 深色研究终端风）。
 *
 * 所有颜色 / 字号都在这里定义，页面组件只使用语义化类名（bg-panel / text-muted / ...），
 * 因此将来换肤只需改本文件（docs/07 §3.2）。
 *
 * 关键约束（docs/07 §3.3）：
 *   - 数字一律等宽 + tabular-nums（避免价格/分数跳动）
 *   - 正文行高 ≥ 1.7（深色底下的可读性）
 *   - 用分隔线而非卡片阴影组织信息（更像研究终端，不像荐股 App）
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0B0E11',
        panel: '#12161C',
        'panel-2': '#171C24',
        border: '#1F262E',
        'border-strong': '#2A333E',
        text: '#E6E9EF',
        muted: '#8B94A3',
        faint: '#5B6472',
        accent: '#2DD4BF',
        'accent-dim': '#1F8F82',
        // 状态色：与 OpportunityStatus 一一对应
        status: {
          discovered: '#8B94A3',
          pending: '#F59E0B',
          tracking: '#2DD4BF',
          confirmed: '#22C55E',
          observing: '#60A5FA',
          invalid: '#EF4444',
          archived: '#6B7280',
        },
        // 证据可信度 A~E
        reliability: {
          a: '#22C55E',
          b: '#84CC16',
          c: '#EAB308',
          d: '#F97316',
          e: '#6B7280',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Cascadia Mono', 'Consolas', 'ui-monospace', 'monospace'],
        sans: ['Inter', 'Microsoft YaHei UI', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
      lineHeight: { reading: '1.75' },
      borderRadius: { panel: '0.375rem' },
    },
  },
  plugins: [],
}

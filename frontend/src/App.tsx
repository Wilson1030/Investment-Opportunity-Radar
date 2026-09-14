import { useEffect } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { ensureMachine } from './lib/actions'

const NAV = [
  { to: '/', label: 'Radar', hint: '今日机会' },
  { to: '/thesis', label: 'My Thesis', hint: '我的投资逻辑' },
  { to: '/events', label: 'Events', hint: '重要事件' },
  { to: '/profile', label: 'Profile', hint: '投资倾向与策略' },
]

/** 应用外壳：顶部状态条 + 左侧导航 + 内容区 */
export function App() {
  // ★ 应用级加载一次状态机：卡片的操作按钮依赖它
  useEffect(() => {
    void ensureMachine()
  }, [])

  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-30 border-b border-border bg-bg/95 backdrop-blur">
        <div className="mx-auto max-w-5xl flex items-center gap-4 px-4 py-2">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold tracking-[0.2em]">INVESTMENT RADAR</span>
            <span className="text-2xs text-faint hidden sm:inline">投资机会雷达</span>
          </div>
          <nav className="flex items-center gap-1 ml-auto">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                title={item.hint}
                className={({ isActive }) =>
                  `px-2.5 py-1 text-2xs border rounded-sm transition-colors ${
                    isActive
                      ? 'text-accent border-accent/40 bg-accent/10'
                      : 'text-muted border-transparent hover:text-text hover:border-border'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <Outlet />
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto max-w-5xl px-4 py-3 text-2xs text-faint">
          本系统是投资研究与信息组织工具，不是行情软件，不是荐股工具。
          AI 的职责是发现、解释、关联、比较、提醒、验证与监控 —— <span className="text-muted">不替你做决策</span>。
        </div>
      </footer>
    </div>
  )
}

export default App

import { NavLink } from 'react-router-dom'
import { Activity, BarChart3, ClipboardList, Wallet } from 'lucide-react'

const TABS = [
  { to: '/', label: 'Now', end: true, Icon: Activity },
  { to: '/positions', label: 'Positions', end: false, Icon: Wallet },
  { to: '/activity', label: 'Activity', end: false, Icon: ClipboardList },
  { to: '/performance', label: 'Performance', end: false, Icon: BarChart3 },
]

export function BottomNav() {
  return (
    <>
      <nav className="sticky top-0 z-30 hidden border-b border-white/[0.06] bg-anchor-spine/95 backdrop-blur md:block">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-6 px-6 py-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-white/35">Anchor futures</p>
            <p className="mt-1 font-display text-[1.35rem] leading-none text-anchor-brass">Operator console</p>
          </div>
          <div className="flex items-center gap-2">
            {TABS.map(({ to, label, end, Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `flex items-center gap-2 rounded-full border px-4 py-2 transition-all ${
                    isActive
                      ? 'border-anchor-chartblue/40 bg-anchor-chartblue/12 text-anchor-chartblue'
                      : 'border-white/8 bg-white/[0.03] text-white/55 hover:border-white/14 hover:bg-white/[0.05] hover:text-white/78'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon size={15} strokeWidth={isActive ? 2.2 : 1.8} />
                    <span className="font-mono text-[10px] uppercase tracking-[0.14em]">{label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </div>
        </div>
      </nav>

      <nav className="fixed bottom-0 inset-x-0 z-30 bg-anchor-spine border-t border-white/[0.06] md:hidden">
        <div className="flex items-center justify-around h-[64px]">
          {TABS.map(({ to, label, end, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex flex-col items-center gap-1.5 flex-1 py-3 transition-all ${
                  isActive ? 'text-anchor-chartblue' : 'text-white/25'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={16} strokeWidth={isActive ? 2.2 : 1.5} />
                  <span className="font-mono text-[8px] tracking-[0.12em] uppercase">{label}</span>
                </>
              )}
            </NavLink>
          ))}
        </div>
      </nav>
    </>
  )
}

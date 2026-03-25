import { NavLink } from 'react-router-dom'
import { Activity, BookOpen, Zap } from 'lucide-react'

const TABS = [
  { to: '/',             label: 'Now',   end: true,  Icon: Activity },
  { to: '/trades',       label: 'Log',   end: false, Icon: BookOpen },
  { to: '/intelligence', label: 'Intel', end: false, Icon: Zap      },
]

export function BottomNav() {
  return (
    <nav className="fixed bottom-0 inset-x-0 z-30 bg-anchor-spine border-t border-white/[0.06] md:hidden">
      <div className="flex items-center justify-around h-[60px]">
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
                <span className="font-mono text-[8px] tracking-[0.2em] uppercase">{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

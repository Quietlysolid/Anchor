import { NavLink } from 'react-router-dom'
import { Home, Clock, Radio } from 'lucide-react'

const TABS = [
  { to: '/',             Icon: Home,  label: 'Home',    end: true  },
  { to: '/trades',       Icon: Clock, label: 'History', end: false },
  { to: '/intelligence', Icon: Radio, label: 'Activity', end: false },
]

export function BottomNav() {
  return (
    <nav className="fixed bottom-0 inset-x-0 z-30 bg-anchor-surface border-t border-anchor-border md:hidden">
      <div className="flex items-center justify-around h-16">
        {TABS.map(({ to, Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex flex-col items-center gap-1 flex-1 py-2 transition-colors ${
                isActive ? 'text-anchor-green' : 'text-anchor-muted'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <Icon size={22} strokeWidth={isActive ? 2 : 1.5} />
                <span className="text-[10px] font-medium">{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

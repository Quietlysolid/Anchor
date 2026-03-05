import { NavLink } from 'react-router-dom'
import { useSystemStore } from '../../store'

const NAV = [
  { to: '/',            label: 'Dashboard',    icon: '⬡' },
  { to: '/performance', label: 'Performance',  icon: '◈' },
  { to: '/journal',     label: 'Journal',      icon: '▤' },
  { to: '/backtest',    label: 'Backtest',     icon: '◷' },
  { to: '/settings',    label: 'Settings',     icon: '⚙' },
]

export function Sidebar() {
  const { wsConnected } = useSystemStore()

  return (
    <aside className="w-52 bg-card border-r border-border flex flex-col shrink-0">
      <div className="p-5 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-primary text-xl">⚓</span>
          <span className="font-bold text-foreground tracking-tight">ANCHOR</span>
        </div>
        <div className="flex items-center gap-1.5 mt-2">
          <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-xs text-muted-foreground">{wsConnected ? 'Live' : 'Disconnected'}</span>
        </div>
      </div>

      <nav className="flex-1 p-3">
        {NAV.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-md text-sm mb-1 transition-colors
               ${isActive ? 'bg-primary/15 text-primary font-medium' : 'text-muted-foreground hover:text-foreground hover:bg-muted'}`
            }
          >
            <span>{icon}</span>
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="p-4 border-t border-border text-xs text-muted-foreground">
        <div>Anchor v1.0</div>
        <div className="opacity-50 mt-0.5">Private System</div>
      </div>
    </aside>
  )
}
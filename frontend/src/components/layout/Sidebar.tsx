import { NavLink } from 'react-router-dom'
import { useSystemStore } from '../../store'

const NAV = [
  { to: '/',            label: 'Dashboard',    icon: '⬡' },
  { to: '/performance', label: 'Performance',  icon: '◈' },
  { to: '/journal',     label: 'Journal',      icon: '▤' },
  { to: '/backtest',    label: 'Backtest',     icon: '◷' },
  { to: '/settings',    label: 'Settings',     icon: '⚙' },
]

interface Props { onClose?: () => void }

export function Sidebar({ onClose }: Props) {
  const { wsConnected } = useSystemStore()

  return (
    <aside className="w-52 h-full bg-card border-r border-border flex flex-col shrink-0">
      <div className="p-5 border-b border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-primary text-xl">⚓</span>
            <span className="font-bold text-foreground tracking-tight">ANCHOR</span>
          </div>
          {/* Close button — mobile only */}
          {onClose && (
            <button type="button" onClick={onClose}
              className="md:hidden text-muted-foreground hover:text-foreground p-1"
              aria-label="Close menu">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                <path d="M2 2l12 12M14 2L2 14" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </button>
          )}
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
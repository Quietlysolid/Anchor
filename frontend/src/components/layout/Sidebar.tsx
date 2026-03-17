import { NavLink } from 'react-router-dom'
import { useSystemStore } from '../../store'
import { useSystemHealth } from '../../api/hooks'

import {
  LayoutDashboard, TrendingUp, BookOpen,
  FlaskConical, Settings, Anchor, Wifi, WifiOff,
} from 'lucide-react'

const NAV = [
  { to: '/',            label: 'Dashboard',   Icon: LayoutDashboard },
  { to: '/performance', label: 'Performance', Icon: TrendingUp       },
  { to: '/journal',     label: 'Journal',     Icon: BookOpen         },
  { to: '/backtest',    label: 'Backtest',    Icon: FlaskConical     },
  { to: '/settings',    label: 'Settings',    Icon: Settings         },
]

interface Props { onClose?: () => void }

export function Sidebar({ onClose }: Props) {
  const { wsConnected, balance, equity } = useSystemStore()
  const { data: health } = useSystemHealth()

  return (
    <aside className="w-52 h-full bg-card border-r border-border flex flex-col shrink-0">
      {/* Logo */}
      <div className="p-5 border-b border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Anchor size={17} className="text-primary" />
            <span className="font-bold tracking-tight text-foreground">ANCHOR</span>
          </div>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="md:hidden text-muted-foreground hover:text-foreground p-1 rounded transition-colors"
              aria-label="Close menu"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M2 2l12 12M14 2L2 14" />
              </svg>
            </button>
          )}
        </div>

        {/* Connection status */}
        <div className="flex items-center gap-1.5 mt-2.5">
          {wsConnected
            ? <Wifi size={11} className="text-green-400" />
            : <WifiOff size={11} className="text-red-400" />
          }
          <span className={`text-xs font-medium ${wsConnected ? 'text-green-400' : 'text-red-400'}`}>
            {wsConnected ? 'Live' : 'Disconnected'}
          </span>
          {health && !health.stream_connected && (
            <span className="text-[10px] text-red-400/80 ml-1">· prices offline</span>
          )}
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-3 space-y-0.5">
        {NAV.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm transition-all duration-150
               ${isActive
                 ? 'bg-primary/15 text-primary font-medium'
                 : 'text-muted-foreground hover:text-foreground hover:bg-muted/70'
               }`
            }
          >
            <Icon size={15} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer: account snapshot — live via WS, falls back to health API seed */}
      <div className="p-4 border-t border-border">
        {(balance > 0 || (health?.account_balance ?? 0) > 0) ? (
          <div className="space-y-1.5 animate-fade-in">
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted-foreground">Balance</span>
              <span className="text-xs font-mono font-medium">
                ${(balance || health?.account_balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-xs text-muted-foreground">Equity</span>
              <span className={`text-xs font-mono font-medium ${
                (equity || health?.account_equity || 0) >= (balance || health?.account_balance || 0)
                  ? 'text-green-400' : 'text-red-400'
              }`}>
                ${(equity || health?.account_equity || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="skeleton h-3 w-full" />
            <div className="skeleton h-3 w-3/4" />
          </div>
        )}
        {health?.last_reconciliation && (
          <div className="flex justify-between items-center mt-1.5">
            <span className="text-[10px] text-muted-foreground/40">Synced</span>
            <span className="text-[10px] text-muted-foreground/40 font-mono">
              {new Date(health.last_reconciliation).toLocaleTimeString('en-US', {
                timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true,
              })}
            </span>
          </div>
        )}
        <div className="text-[10px] text-muted-foreground/40 mt-2">Anchor v1.0 · Private</div>
      </div>
    </aside>
  )
}

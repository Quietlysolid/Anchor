import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { Activity, Sparkles, BarChart2, PanelLeftClose, PanelLeftOpen, X } from 'lucide-react'
import { StatusDot } from '../ui/StatusDot'
import { useSystemStore } from '../../store'

// The Anchor mark — custom, not from any library
function AnchorMark({ size = 20, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" className={className}>
      <circle cx="10" cy="4"  r="2.2" stroke="currentColor" strokeWidth="1.5"/>
      <line x1="10" y1="6.2"  x2="10"  y2="17"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="5"  y1="8.8"  x2="15"  y2="8.8"  stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <path d="M10 17 Q 5.5 17.5 4.5 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
      <path d="M10 17 Q 14.5 17.5 15.5 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
    </svg>
  )
}

const NAV = [
  { to: '/',             Icon: Activity,  label: 'Console',      end: true  },
  { to: '/intelligence', Icon: Sparkles,  label: 'Intelligence', end: false },
  { to: '/trades',       Icon: BarChart2, label: 'Trades',       end: false },
]

interface Props { onClose?: () => void }

export function Sidebar({ onClose }: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const { wsConnected } = useSystemStore()

  return (
    <aside className={`
      h-full bg-anchor-surface border-r border-anchor-border
      flex flex-col shrink-0 transition-all duration-200
      ${collapsed ? 'w-[60px]' : 'w-[220px]'}
    `}>

      {/* Logo */}
      <div className={`
        flex items-center border-b border-anchor-border h-[56px]
        ${collapsed ? 'justify-center px-0' : 'px-5 gap-3'}
      `}>
        <AnchorMark size={18} className="text-anchor-green shrink-0" />
        {!collapsed && (
          <span className="font-mono font-semibold text-anchor-text tracking-[0.15em] text-sm">
            ANCHOR
          </span>
        )}
        {onClose && !collapsed && (
          <button
            type="button"
            onClick={onClose}
            className="md:hidden ml-auto text-anchor-muted hover:text-anchor-text transition-colors"
          >
            <X size={15} />
          </button>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 p-2 space-y-0.5 pt-3">
        {NAV.map(({ to, Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            title={collapsed ? label : undefined}
            className={({ isActive }) => `
              relative flex items-center rounded-lg transition-all duration-150
              ${collapsed ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2.5'}
              ${isActive
                ? 'text-anchor-green bg-anchor-green/8'
                : 'text-anchor-muted hover:text-anchor-text hover:bg-white/[0.04]'
              }
            `}
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <span className="absolute left-0 inset-y-2 w-[3px] bg-anchor-green rounded-r-full" />
                )}
                <Icon size={16} strokeWidth={isActive ? 2 : 1.75} />
                {!collapsed && (
                  <span className={`text-sm ${isActive ? 'text-anchor-text font-medium' : ''}`}>
                    {label}
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className={`
        border-t border-anchor-border p-3
        flex ${collapsed ? 'flex-col items-center gap-3' : 'items-center justify-between'}
      `}>
        <div className={`flex items-center gap-2 ${collapsed ? 'flex-col gap-1.5' : ''}`}>
          <StatusDot connected={wsConnected} />
          {!collapsed && (
            <span className={`text-[11px] font-mono ${wsConnected ? 'text-anchor-green' : 'text-anchor-red'}`}>
              {wsConnected ? 'Live' : 'Offline'}
            </span>
          )}
        </div>

        {!collapsed && (
          <span className="text-[10px] font-mono text-anchor-muted/30 tracking-widest">v2</span>
        )}

        <button
          type="button"
          onClick={() => setCollapsed(v => !v)}
          className="text-anchor-muted hover:text-anchor-text transition-colors hidden md:block"
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed
            ? <PanelLeftOpen size={15} strokeWidth={1.5} />
            : <PanelLeftClose size={15} strokeWidth={1.5} />
          }
        </button>
      </div>
    </aside>
  )
}

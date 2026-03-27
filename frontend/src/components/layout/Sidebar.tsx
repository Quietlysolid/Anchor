import { NavLink } from 'react-router-dom'
import { X, Activity, BookOpen, Zap } from 'lucide-react'

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
  { to: '/',             label: 'Now',   end: true,  Icon: Activity },
  { to: '/trades',       label: 'Log',   end: false, Icon: BookOpen },
  { to: '/intelligence', label: 'Intel', end: false, Icon: Zap      },
]

interface Props { onClose?: () => void }

export function Sidebar({ onClose }: Props) {
  return (
    <aside className="h-full w-[180px] shrink-0 bg-anchor-spine flex flex-col border-r border-white/[0.06]">

      {/* Brand */}
      <div className="px-5 pt-7 pb-6 flex items-start justify-between border-b border-white/[0.05]">
        <div>
          <div className="flex items-center gap-2.5">
            <AnchorMark size={20} className="text-anchor-rule shrink-0" />
            <span className="font-mono text-[11px] tracking-[0.28em] text-white/60 uppercase font-medium">anchor futures</span>
          </div>
          <p className="mt-2 text-[9px] text-white/20 font-mono tracking-[0.2em] uppercase">systematic · futures</p>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="md:hidden text-white/20 hover:text-white/50 transition-colors mt-0.5"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 pt-4 space-y-0.5">
        {NAV.map(({ to, label, end, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `group flex items-center gap-3 px-3 py-2.5 transition-all duration-150 border-l-2 ${
                isActive
                  ? 'bg-white/[0.06] text-white border-anchor-chartblue'
                  : 'text-white/30 hover:text-white/55 hover:bg-white/[0.03] border-transparent'
              }`
            }
          >
            {({ isActive }) => (
              <>
                <Icon
                  size={13}
                  strokeWidth={isActive ? 2.2 : 1.6}
                  className={`shrink-0 transition-all ${isActive ? 'text-anchor-chartblue' : 'text-white/25 group-hover:text-white/50'}`}
                />
                <span className="font-mono text-[10px] tracking-[0.18em] uppercase">{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Status footer */}
      <div className="px-5 py-5 border-t border-white/[0.05]">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="w-1.5 h-1.5 bg-anchor-chartblue/80 shrink-0" />
          <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-white/40">running</span>
        </div>
        <p className="font-mono text-[8px] tracking-[0.14em] text-white/15 uppercase">paper futures · no real cash</p>
      </div>
    </aside>
  )
}

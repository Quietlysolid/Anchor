interface Props {
  label:     string
  variant:   'buy' | 'sell' | 'muted' | 'high' | 'med' | 'low'
  className?: string
}

const VARIANTS = {
  buy:  'text-anchor-green bg-anchor-green/10 border-anchor-green/20',
  sell: 'text-anchor-red   bg-anchor-red/10   border-anchor-red/20',
  muted:'text-anchor-muted bg-anchor-muted/10 border-anchor-border',
  high: 'text-anchor-red   bg-anchor-red/10   border-anchor-red/20',
  med:  'text-amber-400    bg-amber-400/10    border-amber-400/20',
  low:  'text-anchor-muted bg-anchor-muted/10 border-anchor-border',
}

export function Pill({ label, variant, className = '' }: Props) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium border ${VARIANTS[variant]} ${className}`}>
      {label}
    </span>
  )
}

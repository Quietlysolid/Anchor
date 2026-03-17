import { useEffect, useRef } from 'react'

interface Props {
  value:      number    // 0–1
  className?: string
  animate?:   boolean
}

export function ConfidenceBar({ value, className = '', animate = true }: Props) {
  const barRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!barRef.current || !animate) return
    barRef.current.style.width = '0%'
    const id = setTimeout(() => {
      if (barRef.current) barRef.current.style.width = `${value * 100}%`
    }, 50)
    return () => clearTimeout(id)
  }, [value, animate])

  const pct = value * 100
  const color = pct >= 80 ? 'bg-anchor-green shadow-glow-green' :
                pct >= 65 ? 'bg-anchor-green/70' :
                pct >= 50 ? 'bg-amber-400' : 'bg-anchor-red'

  return (
    <div className={`h-1.5 bg-anchor-border rounded-full overflow-hidden ${className}`}>
      <div
        ref={barRef}
        className={`h-full rounded-full transition-all duration-700 ease-out ${color}`}
        style={{ width: animate ? '0%' : `${pct}%` }}
      />
    </div>
  )
}

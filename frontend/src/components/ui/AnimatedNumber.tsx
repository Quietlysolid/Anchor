import { useEffect, useRef, useState } from 'react'

interface Props {
  value:    number
  prefix?:  string
  suffix?:  string
  decimals?: number
  className?: string
}

export function AnimatedNumber({ value, prefix = '', suffix = '', decimals = 2, className = '' }: Props) {
  const [display, setDisplay]   = useState(value)
  const [flash,   setFlash]     = useState<'up' | 'down' | null>(null)
  const prevRef                  = useRef(value)
  const rafRef                   = useRef<number | null>(null)

  useEffect(() => {
    const prev = prevRef.current
    if (prev === value) return
    setFlash(value > prev ? 'up' : 'down')
    const timeout = setTimeout(() => setFlash(null), 300)

    // Spring interpolation
    const start  = prev
    const end    = value
    const dur    = 600
    const t0     = performance.now()

    function tick(now: number) {
      const p = Math.min((now - t0) / dur, 1)
      const ease = 1 - Math.pow(1 - p, 3)
      setDisplay(start + (end - start) * ease)
      if (p < 1) rafRef.current = requestAnimationFrame(tick)
      else {
        setDisplay(end)
        prevRef.current = end
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      clearTimeout(timeout)
    }
  }, [value])

  const color = flash === 'up' ? 'text-anchor-green' : flash === 'down' ? 'text-anchor-red' : ''

  return (
    <span className={`font-mono transition-colors duration-300 ${color} ${className}`}>
      {prefix}{display.toFixed(decimals)}{suffix}
    </span>
  )
}

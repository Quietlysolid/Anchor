import { useEffect, useRef } from 'react'

interface Props {
  value:      number
  maxValue?:  number
  size?:      number
}

export function DrawdownGauge({ value, maxValue = 0.20, size = 120 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animRef   = useRef<number | null>(null)
  const currRef   = useRef(0)

  const clamp = Math.min(Math.max(value, 0), maxValue)
  const pct   = clamp / maxValue

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    const dpr = window.devicePixelRatio || 1
    canvas.width        = size * dpr
    canvas.height       = size * dpr
    canvas.style.width  = `${size}px`
    canvas.style.height = `${size}px`
    ctx.scale(dpr, dpr)

    const cx         = size / 2
    const cy         = size / 2
    const r          = size * 0.38
    const sw         = size * 0.07
    const startAngle = Math.PI * 0.75
    const totalAngle = Math.PI * 1.5

    // #30D158 → #FF9F0A → #FF453A
    function lerpColor(t: number): string {
      if (t < 0.5) {
        const p  = t * 2
        const r2 = Math.round(48  + (255 - 48)  * p)
        const g2 = Math.round(209 + (159 - 209) * p)
        const b2 = Math.round(88  + (10  - 88)  * p)
        return `rgb(${r2},${g2},${b2})`
      } else {
        const p  = (t - 0.5) * 2
        const r2 = Math.round(255 + (255 - 255) * p)
        const g2 = Math.round(159 + (69  - 159) * p)
        const b2 = Math.round(10  + (58  - 10)  * p)
        return `rgb(${r2},${g2},${b2})`
      }
    }

    function draw(animPct: number) {
      ctx.clearRect(0, 0, size, size)

      ctx.beginPath()
      ctx.arc(cx, cy, r, startAngle, startAngle + totalAngle)
      ctx.strokeStyle = '#2C2C2E'
      ctx.lineWidth   = sw
      ctx.lineCap     = 'round'
      ctx.stroke()

      if (animPct > 0) {
        const color    = lerpColor(animPct)
        const endAngle = startAngle + totalAngle * animPct
        ctx.beginPath()
        ctx.arc(cx, cy, r, startAngle, endAngle)
        ctx.strokeStyle = color
        ctx.lineWidth   = sw
        ctx.lineCap     = 'round'
        ctx.shadowColor = color
        ctx.shadowBlur  = 6
        ctx.stroke()
        ctx.shadowBlur  = 0
      }

      const displayPct = Math.round(clamp * 100)
      ctx.fillStyle    = '#FFFFFF'
      ctx.font         = `600 ${size * 0.18}px 'JetBrains Mono', monospace`
      ctx.textAlign    = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(`${displayPct}%`, cx, cy - size * 0.05)

      ctx.fillStyle = '#636366'
      ctx.font      = `400 ${size * 0.10}px 'Inter', sans-serif`
      ctx.fillText('drawdown', cx, cy + size * 0.13)
    }

    const target = pct
    const start  = currRef.current
    const t0     = performance.now()
    const dur    = 800

    function animate(now: number) {
      const p       = Math.min((now - t0) / dur, 1)
      const ease    = 1 - Math.pow(1 - p, 3)
      const current = start + (target - start) * ease
      currRef.current = current
      draw(current)
      if (p < 1) animRef.current = requestAnimationFrame(animate)
    }

    animRef.current = requestAnimationFrame(animate)
    return () => { if (animRef.current) cancelAnimationFrame(animRef.current) }
  }, [pct, size, clamp])

  return <canvas ref={canvasRef} />
}

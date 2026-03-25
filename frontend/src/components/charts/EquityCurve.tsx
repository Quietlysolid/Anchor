import { useRef, useEffect, useMemo, useState } from 'react'
import type { EquityPoint } from '../../types'

interface Props {
  data: EquityPoint[]
}

const HEIGHT = 148
const PAD_TOP = 20
const PAD_BOT = 4
const INNER_H = HEIGHT - PAD_TOP - PAD_BOT

// Subsample to at most N points while always keeping first + last
function subsample(arr: EquityPoint[], max: number): EquityPoint[] {
  if (arr.length <= max) return arr
  const step = (arr.length - 1) / (max - 1)
  return Array.from({ length: max }, (_, i) =>
    arr[Math.min(Math.round(i * step), arr.length - 1)]
  )
}

// Catmull-Rom to cubic bezier smooth path
function smoothPath(pts: [number, number][]): string {
  if (pts.length < 2) return ''
  let d = `M ${pts[0][0].toFixed(2)},${pts[0][1].toFixed(2)}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[Math.min(pts.length - 1, i + 2)]
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6
    d += ` C ${cp1x.toFixed(2)},${cp1y.toFixed(2)} ${cp2x.toFixed(2)},${cp2y.toFixed(2)} ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`
  }
  return d
}

function fmtDate(isoStr: string): string {
  return new Date(isoStr).toLocaleDateString('en-US', {
    timeZone: 'America/New_York',
    month: 'short',
    day: 'numeric',
  })
}

function fmtDollars(v: number): string {
  return `$${Math.round(v).toLocaleString('en-US')}`
}

export function EquityCurve({ data }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const pathRef = useRef<SVGPathElement>(null)
  const [width, setWidth] = useState(0)

  // Measure container width
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    setWidth(el.clientWidth)
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const sampled = useMemo(() => subsample(data, 280), [data])

  const chart = useMemo(() => {
    if (!width || sampled.length < 2) return null

    const values = sampled.map(d => d.account_equity)
    const minV = Math.min(...values)
    const maxV = Math.max(...values)
    const range = maxV - minV || 1

    const xStep = width / (sampled.length - 1)
    const pts: [number, number][] = sampled.map((d, i) => [
      i * xStep,
      PAD_TOP + INNER_H - ((d.account_equity - minV) / range) * INNER_H,
    ])

    const linePath = smoothPath(pts)
    const last = pts[pts.length - 1]
    const first = pts[0]
    const fillPath = linePath
      + ` L ${last[0].toFixed(2)},${(PAD_TOP + INNER_H).toFixed(2)}`
      + ` L ${first[0].toFixed(2)},${(PAD_TOP + INNER_H).toFixed(2)} Z`

    const athVal = maxV
    const athY = PAD_TOP + INNER_H - ((athVal - minV) / range) * INNER_H
    const current = values[values.length - 1]
    const start = values[0]
    const isAtATH = current >= athVal * 0.9999
    const netPct = ((current - start) / start) * 100
    const isUp = current >= start

    return { pts, linePath, fillPath, first, last, athY, athVal, current, start, isAtATH, isUp, netPct }
  }, [width, sampled])

  // Draw animation: stroke-dashoffset reveals the line left to right
  useEffect(() => {
    const path = pathRef.current
    if (!path || !chart) return
    const len = path.getTotalLength()
    path.style.strokeDasharray = `${len}`
    path.style.strokeDashoffset = `${len}`
    const id = requestAnimationFrame(() => {
      path.style.transition = 'stroke-dashoffset 2s cubic-bezier(0.4, 0, 0.2, 1)'
      path.style.strokeDashoffset = '0'
    })
    return () => cancelAnimationFrame(id)
  }, [chart])

  const startDate = data.length ? fmtDate(data[0].time) : ''

  if (!data.length) {
    return (
      <div ref={containerRef} className="w-full py-6">
        <p className="font-mono text-[10px] text-anchor-muted/40">No account data yet.</p>
      </div>
    )
  }

  const strokeColor = chart?.isUp !== false ? '#10e898' : '#f5a200'
  const fillId = 'eq-grad'

  return (
    <div ref={containerRef} className="w-full">
      {/* Label row */}
      <div className="flex items-baseline justify-between mb-4">
        <p className="font-mono text-[9px] tracking-[0.28em] uppercase text-anchor-muted/45">
          Account curve
        </p>
        {chart && (
          <p className={`font-mono text-[11px] font-medium ${chart.isUp ? 'text-anchor-green' : 'text-anchor-amber'}`}>
            {chart.isUp ? '+' : ''}{chart.netPct.toFixed(1)}% since start
          </p>
        )}
      </div>

      {/* SVG */}
      {width > 0 && chart && (
        <svg
          width={width}
          height={HEIGHT}
          className="overflow-visible"
        >
          <defs>
            <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={strokeColor} stopOpacity="0.13" />
              <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />
            </linearGradient>
          </defs>

          {/* Baseline rule */}
          <line
            x1={0} y1={PAD_TOP + INNER_H}
            x2={width} y2={PAD_TOP + INNER_H}
            stroke="#102840" strokeWidth="0.75"
          />

          {/* ATH dotted line — only when below */}
          {!chart.isAtATH && (
            <line
              x1={0} y1={chart.athY}
              x2={width} y2={chart.athY}
              stroke={strokeColor} strokeWidth="0.75"
              strokeDasharray="3 5"
              opacity="0.35"
            />
          )}

          {/* ATH label */}
          {!chart.isAtATH && (
            <text
              x={4} y={chart.athY - 4}
              fontSize="9"
              fontFamily="'JetBrains Mono', monospace"
              fill={strokeColor}
              opacity="0.4"
            >
              high · {fmtDollars(chart.athVal)}
            </text>
          )}

          {/* Fill */}
          <path d={chart.fillPath} fill={`url(#${fillId})`} />

          {/* Curve — animates in */}
          <path
            ref={pathRef}
            d={chart.linePath}
            fill="none"
            stroke={strokeColor}
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Start dot */}
          <circle
            cx={chart.first[0]} cy={chart.first[1]}
            r="3"
            fill="#030c16"
            stroke={strokeColor}
            strokeWidth="1.5"
            opacity="0.6"
          />

          {/* End dot — breathing pulse ring */}
          <circle cx={chart.last[0]} cy={chart.last[1]} r="4" fill={strokeColor} opacity="0">
            <animate attributeName="r" values="4;9;4" dur="2.4s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.25;0;0.25" dur="2.4s" repeatCount="indefinite" />
          </circle>
          {/* End dot — solid */}
          <circle cx={chart.last[0]} cy={chart.last[1]} r="3.5" fill={strokeColor} />

          {/* Current value label — nudges right if near edge */}
          <text
            x={Math.min(chart.last[0] + 9, width - 72)}
            y={chart.last[1] + 4}
            fontSize="10"
            fontFamily="'JetBrains Mono', monospace"
            fill={strokeColor}
          >
            {fmtDollars(chart.current)}
          </text>
        </svg>
      )}

      {/* Date strip */}
      <div className="flex justify-between mt-2">
        <p className="font-mono text-[9px] text-anchor-muted/30">{startDate}</p>
        <p className="font-mono text-[9px] text-anchor-muted/30">now</p>
      </div>
    </div>
  )
}

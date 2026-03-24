import { useEffect, useRef } from 'react'
import {
  createChart, ColorType, AreaSeries, LineStyle,
  type IChartApi, type ISeriesApi, type Time, type IPriceLine,
} from 'lightweight-charts'
import type { EquityPoint } from '../../types'

interface Props {
  data:    EquityPoint[]
  height?: number
}

export function EquityCurve({ data, height = 220 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const seriesRef    = useRef<ISeriesApi<'Area'> | null>(null)
  const athLineRef   = useRef<IPriceLine | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor:  '#636366',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize:   11,
      },
      grid: {
        vertLines: { color: '#3A3A3C20' },
        horzLines: { color: '#3A3A3C20' },
      },
      crosshair: {
        vertLine: { color: '#30D15840', width: 1, labelBackgroundColor: '#1C1C1E' },
        horzLine: { color: '#30D15840', width: 1, labelBackgroundColor: '#1C1C1E' },
      },
      rightPriceScale: {
        borderColor: '#3A3A3C',
        textColor:   '#636366',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#3A3A3C',
        timeVisible: true,
        tickMarkFormatter: (t: number) => {
          const d = new Date(t * 1000)
          return d.toLocaleDateString('en-US', {
            timeZone: 'America/New_York',
            month: 'short',
            day: 'numeric',
          })
        },
      },
      handleScale:  { axisPressedMouseMove: false },
      handleScroll: false,
      height,
      width: containerRef.current.clientWidth,
    })

    const series = chart.addSeries(AreaSeries, {
      lineColor:                      '#30D158',
      topColor:                       '#30D15828',
      bottomColor:                    '#30D15800',
      lineWidth:                      2,
      crosshairMarkerVisible:         true,
      crosshairMarkerRadius:          4,
      crosshairMarkerBorderColor:     '#30D158',
      crosshairMarkerBackgroundColor: '#1C1C1E',
      lastValueVisible:               false,
      priceLineVisible:               false,
    })

    chartRef.current  = chart
    seriesRef.current = series

    const ro = new ResizeObserver(([e]) => {
      chart.applyOptions({ width: e.contentRect.width })
    })
    ro.observe(containerRef.current)

    return () => { chart.remove(); ro.disconnect(); chartRef.current = null; seriesRef.current = null }
  }, [height])

  useEffect(() => {
    if (!seriesRef.current || !data.length) return

    const pts = data.map(d => ({
      time:  (new Date(d.time).getTime() / 1000) as Time,
      value: d.account_equity,
    }))
    seriesRef.current.setData(pts)
    chartRef.current?.timeScale().fitContent()

    // All-time high dotted line
    const ath     = Math.max(...pts.map(p => p.value))
    const current = pts[pts.length - 1]?.value ?? 0
    const atATH   = current >= ath * 0.9999   // floating-point tolerance

    // Color: green at/above ATH, muted amber below
    seriesRef.current.applyOptions({
      lineColor:   atATH ? '#30D158' : '#FF9F0A',
      topColor:    atATH ? '#30D15828' : '#FF9F0A18',
      bottomColor: '#00000000',
    })

    // Remove old ATH line before drawing a new one
    if (athLineRef.current) {
      try { seriesRef.current.removePriceLine(athLineRef.current) } catch { /* ignore */ }
      athLineRef.current = null
    }

    // Only draw the ATH line when there's meaningful room above current
    if (!atATH) {
      athLineRef.current = seriesRef.current.createPriceLine({
        price:              ath,
        color:              '#30D15840',
        lineWidth:          1,
        lineStyle:          LineStyle.Dashed,
        axisLabelVisible:   false,
        title:              'ATH',
      })
    }
  }, [data])

  if (!data.length) {
    return (
      <div
        style={{ height }}
        className="w-full flex items-center justify-center"
      >
        <p className="text-xs text-anchor-muted font-mono">No equity data yet</p>
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      style={{ height }}
      className="w-full animate-reveal-right"
    />
  )
}

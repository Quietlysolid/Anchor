import { useEffect, useRef } from 'react'
import { createChart, ColorType, CrosshairMode, CandlestickSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi } from 'lightweight-charts'
import { wsClient } from '../../api/websocket'
import type { Candle, LivePrice } from '../../types'

interface Props {
  candles: Candle[]
  instrument: string
  height?: number
}

export function CandlestickChart({ candles, instrument, height = 300 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)
  const seriesRef    = useRef<ISeriesApi<'Candlestick'> | null>(null)

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: 'hsl(222,84%,5%)' }, textColor: '#94a3b8' },
      grid: { vertLines: { color: 'hsl(217,33%,13%)' }, horzLines: { color: 'hsl(217,33%,13%)' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: 'hsl(217,33%,17%)' },
      timeScale: { borderColor: 'hsl(217,33%,17%)', timeVisible: true },
      height,
      width: containerRef.current.clientWidth,
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })

    chartRef.current  = chart
    seriesRef.current = series

    const ro = new ResizeObserver(([e]) => {
      chart.applyOptions({ width: e.contentRect.width })
    })
    ro.observe(containerRef.current)

    return () => { chart.remove(); ro.disconnect(); chartRef.current = null; seriesRef.current = null }
  // height only — we intentionally keep the chart instance stable
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height])

  // Reload historical bars whenever the candles array changes (pair/tf switch or REST refetch)
  useEffect(() => {
    if (!seriesRef.current || !candles.length) return
    const data = candles.map(c => ({
      time: c.time as any,  // already Unix seconds from the API
      open: c.open, high: c.high, low: c.low, close: c.close,
    }))
    seriesRef.current.setData(data)
    chartRef.current?.timeScale().fitContent()
  }, [candles])

  // Update the live (last) candle on every WebSocket tick for this instrument
  useEffect(() => {
    const unsub = wsClient.on('ticks', (d) => {
      const tick = d as LivePrice
      if (tick.instrument !== instrument || !seriesRef.current || !candles.length) return

      // Build an updated version of the last candle using the mid price
      const last = candles[candles.length - 1]
      const mid  = (tick.bid + tick.ask) / 2
      const t    = last.time as any  // Unix seconds

      seriesRef.current.update({
        time:  t,
        open:  last.open,
        high:  Math.max(last.high, mid),
        low:   Math.min(last.low, mid),
        close: mid,
      })
    })
    return () => { unsub() }
  }, [instrument, candles])

  return <div ref={containerRef} className="chart-container w-full" data-height={height} />
}

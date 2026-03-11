import { useEffect, useRef } from 'react'
import { createChart, ColorType, CrosshairMode, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, SeriesMarker, Time, ISeriesMarkersPluginApi } from 'lightweight-charts'
import { wsClient } from '../../api/websocket'
import type { Candle, LivePrice, Trade } from '../../types'

interface Props {
  candles: Candle[]
  instrument: string
  trades?: Trade[]
  height?: number
}

export function CandlestickChart({ candles, instrument, trades = [], height = 300 }: Props) {
  const containerRef  = useRef<HTMLDivElement>(null)
  const chartRef      = useRef<IChartApi | null>(null)
  const seriesRef     = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersRef    = useRef<ISeriesMarkersPluginApi<Time> | null>(null)

  // Create chart once
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: 'hsl(222,84%,5%)' }, textColor: '#94a3b8' },
      grid: { vertLines: { color: 'hsl(217,33%,13%)' }, horzLines: { color: 'hsl(217,33%,13%)' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: 'hsl(217,33%,17%)', scaleMargins: { top: 0.1, bottom: 0.1 }, mode: 0 },
      timeScale: { borderColor: 'hsl(217,33%,17%)', timeVisible: true },
      height,
      width: containerRef.current.clientWidth,
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })

    chartRef.current   = chart
    seriesRef.current  = series
    markersRef.current = createSeriesMarkers(series, [])

    const ro = new ResizeObserver(([e]) => {
      chart.applyOptions({ width: e.contentRect.width })
    })
    ro.observe(containerRef.current)

    return () => { chart.remove(); ro.disconnect(); chartRef.current = null; seriesRef.current = null; markersRef.current = null }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height])

  // Reload historical bars whenever the candles array changes (pair/tf switch or REST refetch)
  useEffect(() => {
    if (!seriesRef.current) return
    if (!candles.length) { seriesRef.current.setData([]); return }
    const data = candles.map(c => ({
      time: c.time as any,  // already Unix seconds from the API
      open: c.open, high: c.high, low: c.low, close: c.close,
    }))
    seriesRef.current.setData(data)

    // Show only the last 100 candles so the price scale auto-fits the visible range
    const ts = chartRef.current?.timeScale()
    if (ts && data.length > 0) {
      const last  = data[data.length - 1].time as number
      const first = data[Math.max(0, data.length - 100)].time as number
      ts.setVisibleRange({ from: first as any, to: last as any })
      // Re-apply after a tick to ensure price scale recalculates from visible bars only
      setTimeout(() => {
        ts.setVisibleRange({ from: first as any, to: last as any })
      }, 0)
    }
  }, [candles])

  // Draw trade entry/exit markers whenever trades or candles change
  useEffect(() => {
    if (!markersRef.current || !candles.length) return

    const markers: SeriesMarker<Time>[] = []

    for (const trade of trades) {
      const entryTs = Math.floor(new Date(trade.opened_at).getTime() / 1000)
      const exitTs  = Math.floor(new Date(trade.closed_at).getTime() / 1000)

      // Snap to the nearest candle time that exists in the series
      const snap = (ts: number) => {
        let best = candles[0].time as number
        for (const c of candles) {
          if (Math.abs((c.time as number) - ts) < Math.abs(best - ts)) best = c.time as number
        }
        return best as Time
      }

      const isLong  = trade.direction === 'LONG'
      const isWin   = trade.net_pl > 0

      // Entry marker — triangle pointing in direction of trade
      markers.push({
        time:     snap(entryTs),
        position: isLong ? 'belowBar' : 'aboveBar',
        shape:    isLong ? 'arrowUp'  : 'arrowDown',
        color:    '#3b82f6',   // blue — entry
        text:     `${isLong ? '▲' : '▼'} ${trade.entry_price.toFixed(5)}`,
        size:     1,
      })

      // Exit marker — circle, green for win, red for loss
      markers.push({
        time:     snap(exitTs),
        position: isLong ? 'aboveBar' : 'belowBar',
        shape:    'circle',
        color:    isWin ? '#22c55e' : '#ef4444',
        text:     `${isWin ? '+' : ''}${trade.net_pl.toFixed(2)}`,
        size:     1,
      })
    }

    // Markers must be sorted by time
    markers.sort((a, b) => (a.time as number) - (b.time as number))
    markersRef.current?.setMarkers(markers)
  }, [trades, candles])

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

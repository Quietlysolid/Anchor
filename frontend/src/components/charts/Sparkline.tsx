import { useEffect, useRef } from 'react'
import { createChart, ColorType, LineSeries, type IChartApi, type Time } from 'lightweight-charts'

interface Props {
  data:    { time: number; value: number }[]
  color?:  string
  height?: number
  width?:  number
}

export function Sparkline({ data, color = '#00FF94', height = 36, width = 80 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: 'transparent' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
      rightPriceScale: { visible: false },
      leftPriceScale:  { visible: false },
      timeScale:       { visible: false },
      handleScale:     false,
      handleScroll:    false,
      height,
      width,
    })

    const series = chart.addSeries(LineSeries, {
      color,
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
    })

    if (data.length) {
      series.setData(data.map(d => ({ time: d.time as Time, value: d.value })))
      chart.timeScale().fitContent()
    }

    chartRef.current = chart

    return () => { chart.remove(); chartRef.current = null }
  }, [data, color, height, width])

  return <div ref={containerRef} style={{ width, height }} />
}

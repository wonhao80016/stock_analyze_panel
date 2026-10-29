import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { TaiwanQuarterlyFigures } from '@/lib/api'
import { useChartTheme, type ChartTheme } from '@/lib/theme'

export type MarginPoint = { label: string; figures: TaiwanQuarterlyFigures }

const MARGIN_SERIES = [
  { name: '毛利率', key: 'gross_margin_pct' },
  { name: '營業利益率', key: 'operating_margin_pct' },
  { name: '稅後淨利率', key: 'net_margin_pct' },
] as const

export function buildQuarterlyMarginOption(points: MarginPoint[], theme: ChartTheme): EChartsOption {
  return {
    animation: false,
    backgroundColor: 'transparent',
    color: ['#3B82F6', '#F59E0B', '#10B981'],
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipText, fontSize: 11 },
      valueFormatter: value => (typeof value === 'number' ? `${value.toFixed(2)}%` : '--'),
    },
    legend: {
      top: 0,
      left: 0,
      textStyle: { color: theme.text, fontSize: 11 },
      data: MARGIN_SERIES.map(series => series.name),
    },
    grid: { left: 44, right: 12, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: points.map(point => point.label),
      axisLine: { lineStyle: { color: theme.grid } },
      axisTick: { show: false },
      axisLabel: { color: theme.text, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: theme.grid } },
      axisLabel: { color: theme.text, fontSize: 10, formatter: '{value}%' },
    },
    series: MARGIN_SERIES.map(series => ({
      name: series.name,
      type: 'line',
      connectNulls: false,
      symbolSize: 6,
      data: points.map(point => point.figures[series.key]),
    })),
  }
}

export function TaiwanQuarterlyMarginChart({ points, height = 220 }: { points: MarginPoint[]; height?: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const theme = useChartTheme()

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const chart = chartRef.current ?? echarts.init(container, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.setOption(buildQuarterlyMarginOption(points, theme), true)
    if (!resizeObserverRef.current && typeof ResizeObserver !== 'undefined') {
      resizeObserverRef.current = new ResizeObserver(() => chart.resize())
      resizeObserverRef.current.observe(container)
    }
  }, [points, theme])

  useEffect(() => () => {
    resizeObserverRef.current?.disconnect()
    chartRef.current?.dispose()
    chartRef.current = null
  }, [])

  return <div ref={containerRef} role="img" aria-label="三率走勢圖" style={{ height }} className="w-full" />
}

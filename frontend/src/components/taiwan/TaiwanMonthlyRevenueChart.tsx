import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import { useChartTheme, type ChartTheme } from '@/lib/theme'

export type RevenuePoint = { label: string; revenue: number | null; yoyPct: number | null }

const HUNDRED_MILLION = 100_000_000

export function buildMonthlyRevenueOption(points: RevenuePoint[], theme: ChartTheme): EChartsOption {
  return {
    animation: false,
    backgroundColor: 'transparent',
    color: ['#3B82F6', '#F59E0B'],
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipText, fontSize: 11 },
    },
    legend: {
      top: 0,
      left: 0,
      textStyle: { color: theme.text, fontSize: 11 },
      data: ['營收（億）', '年增率'],
    },
    grid: { left: 52, right: 48, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: points.map(point => point.label),
      axisLine: { lineStyle: { color: theme.grid } },
      axisTick: { show: false },
      axisLabel: { color: theme.text, fontSize: 10, hideOverlap: true },
    },
    yAxis: [
      {
        type: 'value',
        splitLine: { lineStyle: { color: theme.grid } },
        axisLabel: { color: theme.text, fontSize: 10 },
      },
      {
        type: 'value',
        scale: true,
        splitLine: { show: false },
        axisLabel: { color: theme.text, fontSize: 10, formatter: '{value}%' },
      },
    ],
    series: [
      {
        name: '營收（億）',
        type: 'bar',
        barMaxWidth: 28,
        data: points.map(point => (point.revenue == null ? null : point.revenue / HUNDRED_MILLION)),
        tooltip: { valueFormatter: value => (typeof value === 'number' ? `${value.toFixed(2)} 億` : '--') },
      },
      {
        name: '年增率',
        type: 'line',
        yAxisIndex: 1,
        connectNulls: false,
        symbolSize: 6,
        data: points.map(point => point.yoyPct),
        tooltip: { valueFormatter: value => (typeof value === 'number' ? `${value.toFixed(2)}%` : '--') },
      },
    ],
  }
}

export function TaiwanMonthlyRevenueChart({ points, height = 220 }: { points: RevenuePoint[]; height?: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const theme = useChartTheme()

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const chart = chartRef.current ?? echarts.init(container, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.setOption(buildMonthlyRevenueOption(points, theme), true)
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

  return <div ref={containerRef} role="img" aria-label="月營收走勢圖" style={{ height }} className="w-full" />
}

import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { StrategyBacktestResult } from '@/lib/api'
import { useChartTheme, type ChartTheme } from '@/lib/theme'

type EquityPoint = StrategyBacktestResult['equity_curve'][number]
type DrawdownPoint = StrategyBacktestResult['drawdown_curve'][number]

interface Props {
  equityCurve: EquityPoint[]
  drawdownCurve: DrawdownPoint[]
  height?: number
}

function finiteValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function normalizeCurve<T extends { date: string; value: number }>(points: T[]): Map<string, number | null> {
  const first = points.map(point => finiteValue(point.value)).find(value => value != null && value !== 0)
  return new Map(points.map(point => {
    const value = finiteValue(point.value)
    return [point.date, value == null || first == null ? null : value / first * 100]
  }))
}

export function buildBacktestEquityOption(
  equityCurve: EquityPoint[],
  drawdownCurve: DrawdownPoint[],
  theme: ChartTheme,
): EChartsOption {
  const dates = [...new Set([
    ...equityCurve.map(point => point.date),
    ...drawdownCurve.map(point => point.date),
  ])].sort()
  const equityByDate = normalizeCurve(equityCurve)
  const drawdownByDate = new Map(drawdownCurve.map(point => [point.date, finiteValue(point.value)]))

  return {
    animation: false,
    backgroundColor: 'transparent',
    color: ['#3B82F6', '#F04438'],
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipText, fontSize: 11 },
    },
    legend: {
      top: 0,
      right: 8,
      textStyle: { color: theme.text, fontSize: 11 },
      data: ['策略淨值', '回撤'],
    },
    grid: { left: 58, right: 58, top: 36, bottom: 32 },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dates,
      axisLine: { lineStyle: { color: theme.grid } },
      axisTick: { show: false },
      axisLabel: { color: theme.text, fontSize: 10, hideOverlap: true },
    },
    yAxis: [
      {
        type: 'value',
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: theme.grid } },
        axisLabel: { color: theme.text, fontSize: 10 },
      },
      {
        type: 'value',
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { color: theme.text, fontSize: 10, formatter: '{value}%' },
      },
    ],
    series: [
      {
        name: '策略淨值',
        type: 'line',
        symbol: 'none',
        data: dates.map(date => equityByDate.get(date) ?? null),
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.08 },
      },
      {
        name: '回撤',
        type: 'line',
        yAxisIndex: 1,
        symbol: 'none',
        data: dates.map(date => {
          const value = drawdownByDate.get(date)
          return value == null ? null : value * 100
        }),
        lineStyle: { width: 1 },
        areaStyle: { opacity: 0.05 },
      },
    ],
  }
}

export function BacktestEquityChart({
  equityCurve,
  drawdownCurve,
  height = 300,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const theme = useChartTheme()

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const chart = chartRef.current ?? echarts.init(container, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.setOption(buildBacktestEquityOption(equityCurve, drawdownCurve, theme), true)

    if (!resizeObserverRef.current) {
      resizeObserverRef.current = new ResizeObserver(() => chart.resize())
      resizeObserverRef.current.observe(container)
    }
  }, [drawdownCurve, equityCurve, theme])

  useEffect(() => () => {
    resizeObserverRef.current?.disconnect()
    chartRef.current?.dispose()
    chartRef.current = null
  }, [])

  return <div ref={containerRef} role="img" aria-label="回測淨值與回撤曲線" style={{ height }} className="w-full" />
}

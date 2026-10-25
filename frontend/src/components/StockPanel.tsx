import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { X } from 'lucide-react'
import { type KlineRow } from '@/lib/api'
import { StockInfoBar } from '@/components/StockInfoBar'
import { StockDailyKChart, getDefaultRange, type StockDailyKChartResult } from '@/components/StockDailyKChart'
import { StockIntradayChart } from '@/components/StockIntradayChart'
import { rawChangeBase, scaleToRaw, type ChartPriceLine, type LinkedPrice } from '@/components/EChartsCandlestick'
import {
  loadInfoFields,
  saveInfoFields,
  buildInfoExtColumnsParam,
  type ColumnConfig,
} from '@/lib/stock-info-fields'

interface Props {
  symbol: string
  height?: number
  showIntraday?: boolean
  /** 外部傳入的日期範圍 */
  dateRange?: { start: string; end: string }
  priceLines?: ChartPriceLine[]
  onPriceDoubleClick?: (price: number, currentPrice: number) => void
  /** 分時圖自動刷新間隔(ms)。undefined = 不輪詢。個股對話框盤中實時刷新時傳入。 */
  refetchIntervalMs?: number
  /** 只渲染信息條, 隱藏圖表 (用於分時 tab 共享信息條) */
  infoBarOnly?: boolean
}

export { getDefaultRange }

export function StockPanel({
  symbol,
  height = 520,
  showIntraday = true,
  dateRange: externalDateRange,
  priceLines,
  onPriceDoubleClick,
  refetchIntervalMs,
  infoBarOnly = false,
}: Props) {
  const [linkedPrice, setLinkedPrice] = useState<LinkedPrice | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [intradayDismissed, setIntradayDismissed] = useState(false)
  // Tagged with its symbol so a result reported for the previous symbol is never shown.
  const [taggedDailyResult, setTaggedDailyResult] = useState<{ symbol: string; result: StockDailyKChartResult } | null>(null)
  const dailyResult = taggedDailyResult?.symbol === symbol ? taggedDailyResult.result : null
  const handleDailyData = useCallback(
    (result: StockDailyKChartResult) => setTaggedDailyResult({ symbol, result }),
    [symbol],
  )
  // 信息條指標配置提升到此層：同時供 StockInfoBar 渲染與 StockDailyKChart 請求 ext 數據
  const [fields, setFields] = useState<ColumnConfig[]>(loadInfoFields)
  const extColumns = useMemo(() => buildInfoExtColumnsParam(fields), [fields])

  const handleFieldsChange = useCallback((next: ColumnConfig[]) => {
    setFields(next)
    saveInfoFields(next)
  }, [])

  const dateRange = externalDateRange ?? getDefaultRange()

  const handleDateClick = useCallback((date: string) => {
    setSelectedDate(date)
    setIntradayDismissed(false)
  }, [])

  const rows = dailyResult?.rows ?? []
  const stockInfo = dailyResult?.stockInfo
  const rawRows: KlineRow[] = dailyResult?.rawRows ?? []

  // symbol 變化時重置分時相關狀態，避免切股後殘留舊日期。
  // dailyResult must not be cleared here: the child's onDataChange effect runs first and
  // has already reported a cached symbol's rows, which this effect would wipe out.
  const prevSymbol = useRef<string | null>(symbol)
  useEffect(() => {
    if (prevSymbol.current === symbol) return
    prevSymbol.current = symbol
    setSelectedDate(null)
    setLinkedPrice(null)
  }, [symbol])

  // 當分時開啟、無選中日期時，自動選中最新日期
  useEffect(() => {
    if (showIntraday && !selectedDate && rows.length > 0) {
      setSelectedDate(rows[rows.length - 1].date)
    }
  }, [showIntraday, selectedDate, rows])

  const selectedIdx = selectedDate ? rows.findIndex(r => r.date === selectedDate) : -1
  const baseIdx = selectedIdx >= 0 ? selectedIdx : rows.length - 1
  // Minute bars trade at unadjusted prices, so the daily base moves off the forward-adjusted scale.
  const prevClose = baseIdx >= 0 ? rawChangeBase(rows, baseIdx) ?? undefined : undefined
  // The intraday chart hovers traded prices while the daily chart is drawn forward-adjusted.
  const tradedPerAdjusted = selectedIdx >= 0 ? scaleToRaw(rows[selectedIdx]) : 1
  const handleIntradayHover = useCallback(
    (price: number | null) => setLinkedPrice(price == null ? null : { value: price / tradedPerAdjusted, traded: price }),
    [tradedPerAdjusted],
  )
  if (!symbol) return null

  return (
    <div>
      <StockInfoBar
        symbol={symbol}
        name={dailyResult?.name}
        stockInfo={stockInfo}
        rows={rawRows}
        fields={fields}
        onFieldsChange={handleFieldsChange}
      />

      {infoBarOnly ? null : (
      <div className="flex gap-3 items-start">
        <StockDailyKChart
          symbol={symbol}
          height={height}
          className="flex-1 min-w-0"
          dateRange={dateRange}
          priceLines={priceLines}
          linkedPrice={linkedPrice}
          onDateClick={handleDateClick}
          onPriceDoubleClick={onPriceDoubleClick}
          onDataChange={handleDailyData}
          visibleBars={showIntraday ? 40 : 60}
          extColumns={extColumns}
        />

        {showIntraday && selectedDate && !intradayDismissed && (
          <div className="relative flex-1 min-w-0 border-l border-border pl-3">
            <button
              onClick={() => setIntradayDismissed(true)}
              className="absolute -left-1.5 -top-1.5 z-10 flex h-5 w-5 items-center justify-center rounded-full border border-border bg-surface text-muted shadow-sm transition-colors hover:text-foreground hover:bg-elevated"
              title="收起分時圖"
              aria-label="收起分時圖"
            >
              <X className="h-3 w-3" />
            </button>
            <StockIntradayChart
              symbol={symbol}
              date={selectedDate}
              height={height}
              prevClose={prevClose}
              onPriceHover={handleIntradayHover}
              onPriceDoubleClick={onPriceDoubleClick}
              currentPrice={rows[rows.length - 1]?.close}
              priceLines={priceLines}
              refetchIntervalMs={selectedDate === rows[rows.length - 1]?.date ? refetchIntervalMs : undefined}
            />
          </div>
        )}
      </div>
      )}
    </div>
  )
}

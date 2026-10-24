import { MAX_COMPARE_SYMBOLS, mergeSymbolIntoCompare } from '@/lib/taiwanCompareSymbols'

/** The list with `symbol` added in place of `drop`; `drop` defaults to the earliest added symbol. */
export function replaceInCompareList(existing: string[], symbol: string, drop: string = existing[0]): string[] {
  return mergeSymbolIntoCompare(existing.filter(s => s !== drop), symbol)
}

/**
 * The compare list after adding `symbol`, or null when the user declines.
 * A full list never drops a symbol silently: the user confirms replacing the earliest one.
 */
export function addToCompareList(
  existing: string[],
  symbol: string,
  confirm: (message: string) => boolean = message => window.confirm(message),
): string[] | null {
  const merged = mergeSymbolIntoCompare(existing, symbol)
  if (merged.includes(symbol.trim().toUpperCase())) return merged
  const oldest = existing[0]
  const message = `比較清單已滿 ${MAX_COMPARE_SYMBOLS} 檔。要移除最早加入的 ${oldest}，改加入 ${symbol} 嗎？\n若要移除其他標的，請取消後到比較頁調整。`
  return confirm(message) ? replaceInCompareList(existing, symbol, oldest) : null
}

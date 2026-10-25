// 大盤指數 (加權/櫃買) 的正本定義, 對應後端 app/taiwan/benchmarks.py。
// 指數不在 TaiwanSecurityMaster 裡, 個股專屬的端點 (即時資料/研究脈絡/籌碼)
// 對它一律回 404, 呼叫端要先用 isBenchmarkSymbol 排除。

export const BENCHMARK_SYMBOLS = ['t00.TWSE', 'o00.TPEX'] as const

export function isBenchmarkSymbol(symbol: string | undefined | null): boolean {
  if (!symbol) return false
  const upper = symbol.toUpperCase()
  return BENCHMARK_SYMBOLS.some(s => s.toUpperCase() === upper)
}

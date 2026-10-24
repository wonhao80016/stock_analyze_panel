const SECTION_LABELS: Record<string, string> = {
  institutional_missing_twse: '三大法人缺上市',
  institutional_missing_tpex: '三大法人缺上櫃',
  margin_missing_twse: '融資券缺上市',
  margin_missing_tpex: '融資券缺上櫃',
}

const WINDOW_NOTES: Record<string, string> = {
  institutional_window_missing_twse: '近 5 日內有交易日缺上市三大法人資料，5 日合計不顯示',
  institutional_window_missing_tpex: '近 5 日內有交易日缺上櫃三大法人資料，5 日合計不顯示',
}

export function missingChipExchangesNote(degraded: string[] | undefined): string | null {
  const sections = degraded ?? []
  const parts = sections.map(s => SECTION_LABELS[s]).filter(Boolean)
  const notes = parts.length ? [`籌碼資料日${parts.join('、')}資料，該市場個股的籌碼欄位暫以空值顯示`] : []
  notes.push(...sections.map(s => WINDOW_NOTES[s]).filter(Boolean))
  return notes.length ? notes.join('；') : null
}

// 板塊篩選的持久化值清洗 (C2)。
//
// BOARDS 從 A 股的五個板塊改成上市/上櫃, 但舊值還在使用者的瀏覽器裡。原樣載入會
// 得到一個沒有任何個股符合的集合 —— 個股行全部消失, ETF/指數因為不受板塊篩選而
// 留著, 兩個板塊鈕卻都顯示未選取。畫面上沒有任何線索指向 localStorage。
//
// 這裡鎖的是清洗邏輯本身: 元件的初始化直接用它, 給錯的輸入就會重現那個畫面。
import { describe, expect, it } from 'vitest'

const BOARDS = ['上市', '上櫃'] as const
type BoardType = typeof BOARDS[number]

/** 與 Watchlist.tsx 的 boardFilter 初始化同一段邏輯。 */
function initialBoardFilter(saved: string[]): Set<string> {
  const kept = saved.filter((b): b is BoardType => (BOARDS as readonly string[]).includes(b))
  return kept.length > 0 ? new Set<string>(kept) : new Set<string>(BOARDS)
}

/** Watchlist.tsx 的「全選時跳過篩選」判斷。 */
function filterIsActive(f: Set<string>): boolean {
  return f.size > 0 && f.size < BOARDS.length
}

describe('自選頁板塊篩選 — 舊 A 股值的清洗', () => {
  it('完全是舊板塊時回退全選, 而不是篩成空集合', () => {
    const f = initialBoardFilter(['創業板'])
    expect([...f].sort()).toEqual(['上市', '上櫃'])
    expect(filterIsActive(f)).toBe(false)
  })

  it('多個舊板塊同樣回退全選', () => {
    const f = initialBoardFilter(['主板', '科創板', '創業板'])
    expect([...f].sort()).toEqual(['上市', '上櫃'])
  })

  it('舊值與新值混在一起時只留下新值', () => {
    const f = initialBoardFilter(['創業板', '上櫃'])
    expect([...f]).toEqual(['上櫃'])
    // 只剩一個 → 篩選仍然生效, 這是使用者真的選過上櫃的情況
    expect(filterIsActive(f)).toBe(true)
  })

  it('乾淨的新值原樣保留', () => {
    expect([...initialBoardFilter(['上市'])]).toEqual(['上市'])
    expect([...initialBoardFilter(['上市', '上櫃'])].sort()).toEqual(['上市', '上櫃'])
  })

  it('沒有存過任何值時預設全選', () => {
    expect([...initialBoardFilter([])].sort()).toEqual(['上市', '上櫃'])
  })

  it('清洗前的行為會讓篩選生效卻沒有任何板塊匹配', () => {
    // 未清洗的舊行為: 直接 new Set(saved)
    const unsanitised = new Set<string>(['創業板'])
    expect(filterIsActive(unsanitised)).toBe(true)
    expect(BOARDS.some(b => unsanitised.has(b))).toBe(false)
  })
})

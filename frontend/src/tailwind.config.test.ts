import { describe, expect, it } from 'vitest'
import config from '../tailwind.config'

describe('tailwind font stack', () => {
  it('prefers Traditional Chinese CJK fonts over Simplified Chinese ones', () => {
    const sans = config.theme.extend.fontFamily.sans as string[]
    const cjk = sans.filter(font => /PingFang|Noto Sans|JhengHei|HarmonyOS|Hiragino|YaHei/.test(font))

    expect(cjk.slice(0, 3)).toEqual(['"PingFang TC"', '"Noto Sans TC"', '"Microsoft JhengHei"'])
    expect(sans.some(font => /\bSC\b|YaHei/.test(font))).toBe(false)
  })
})

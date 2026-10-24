import { afterEach, describe, expect, it, vi } from 'vitest'
import { toast } from '@/components/Toast'
import * as apiModule from './api'

const { api } = apiModule

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

function failing(status: number, body: Record<string, unknown>) {
  return {
    ok: false,
    status,
    statusText: 'Error',
    text: async () => JSON.stringify(body),
  }
}

function succeeding(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('request error toasts', () => {
  it('raises one toast while the same read keeps failing, and again after it recovered', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(failing(500, { detail: '行情服務不可用' }))
      .mockResolvedValueOnce(failing(500, { detail: '行情服務不可用' }))
      .mockResolvedValueOnce(failing(500, { detail: '行情服務不可用' }))
      .mockResolvedValueOnce(succeeding({ running: true }))
      .mockResolvedValueOnce(failing(500, { detail: '行情服務不可用' }))
    vi.stubGlobal('fetch', fetchMock)

    for (let attempt = 0; attempt < 3; attempt += 1) {
      await expect(api.quoteStatus()).rejects.toThrow('行情服務不可用')
    }
    expect(toast).toHaveBeenCalledTimes(1)

    await api.quoteStatus()
    await expect(api.quoteStatus()).rejects.toThrow('行情服務不可用')
    expect(toast).toHaveBeenCalledTimes(2)
  })

  it('still raises a toast for every failed write the user triggers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failing(400, { detail: '無效的設定' })))

    await expect(api.intradayRefresh()).rejects.toThrow('無效的設定')
    await expect(api.intradayRefresh()).rejects.toThrow('無效的設定')
    expect(toast).toHaveBeenCalledTimes(2)
  })

  it('does not let a quiet failure mute the next toasted read of the same path', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failing(500, { detail: '個股資料讀取失敗' })))

    await expect(api.taiwanStockDetail('2330.TWSE', 120, true)).rejects.toThrow()
    expect(toast).not.toHaveBeenCalled()

    await expect(api.taiwanStockDetail('2330.TWSE', 120, false)).rejects.toThrow()
    expect(toast).toHaveBeenCalledTimes(1)
  })

  it('toasts every failure of a read the caller opted out of deduplication', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failing(404, { detail: '資料來源不存在' })))

    await expect(api.dataSource('my-source', { dedupe: false })).rejects.toThrow()
    await expect(api.dataSource('my-source', { dedupe: false })).rejects.toThrow()
    expect(toast).toHaveBeenCalledTimes(2)

    // Undeduped failures leave no record, so the next deduped read still gets its first toast.
    await expect(api.dataSource('my-source')).rejects.toThrow()
    expect(toast).toHaveBeenCalledTimes(3)
    await expect(api.dataSource('my-source')).rejects.toThrow()
    expect(toast).toHaveBeenCalledTimes(3)
  })

  it('lets the caller turn the toast off', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failing(503, { detail: { code: 'X', message: 'm' } })))

    await expect(api.screenerPickPerformance()).rejects.toThrow()
    expect(toast).not.toHaveBeenCalled()
  })
})

describe('request errors carry the HTTP status and error code', () => {
  it('throws an ApiError with status, code and path', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      failing(403, { detail: '面板尚未初始化訪問密碼', code: 'NOT_INITIALIZED' }),
    ))

    const err = await api.settings().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(apiModule.ApiError)
    expect(err).toMatchObject({ status: 403, code: 'NOT_INITIALIZED', path: '/api/settings' })
  })

  it('asks for a login only on a 401 outside the auth endpoints or an uninitialised panel', async () => {
    const { ApiError, isLoginRequiredError } = apiModule
    expect(isLoginRequiredError(new ApiError('未登录或会话已过期', 401, undefined, '/api/settings'))).toBe(true)
    expect(isLoginRequiredError(new ApiError('面板尚未初始化', 403, 'NOT_INITIALIZED', '/api/settings'))).toBe(true)
    // A business error whose message merely contains "401" is not an auth failure.
    expect(isLoginRequiredError(new ApiError('找不到代號 401 的標的', 404, undefined, '/api/kline/daily'))).toBe(false)
    expect(isLoginRequiredError(new ApiError('拒絕跨來源請求', 403, 'CROSS_ORIGIN_FORBIDDEN', '/api/settings'))).toBe(false)
    expect(isLoginRequiredError(new ApiError('旧密码错误', 401, undefined, '/api/auth/change-password'))).toBe(false)
    expect(isLoginRequiredError(new Error('401'))).toBe(false)
  })
})

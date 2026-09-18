import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, LogOut, ShieldAlert, ShieldOff } from 'lucide-react'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

const INPUT_CLASS =
  'h-9 w-full rounded-btn border border-border bg-base px-3 text-xs font-mono text-foreground focus:outline-none focus:border-accent/50'
const BUTTON_CLASS =
  'shrink-0 px-3 py-1.5 rounded-btn bg-accent text-white text-xs font-medium hover:bg-accent/90 disabled:opacity-40 transition-colors'
// Mirrors the max_length of the backend password models.
const PASSWORD_MAX_LENGTH = 128

export function SettingsAccountPanel(_props: { highlight?: string } = {}) {
  const qc = useQueryClient()
  const authStatus = useQuery({ queryKey: QK.authStatus, queryFn: () => api.authStatus() })

  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [localError, setLocalError] = useState('')
  const [loggingOut, setLoggingOut] = useState(false)

  const leaveSession = async (redirectTo: string) => {
    await api.authLogout().catch(() => {})
    qc.clear()
    window.location.href = redirectTo
  }

  const handleLogout = async () => {
    setLoggingOut(true)
    await leaveSession('/login')
  }

  const changePassword = useMutation({
    mutationFn: () => api.authChangePassword(oldPassword, newPassword),
    // set_password wipes every session but the handler cannot clear this browser's
    // cookie, so the stale cookie has to be dropped by an explicit logout call.
    onSuccess: async () => {
      await leaveSession('/login?changed=1')
    },
    // Matched verbatim against the detail strings in backend/app/api/auth.py.
    onError: (err: Error) => {
      if (err.message === '请先登录') {
        window.location.href = '/login'
        return
      }
      if (err.message === '旧密码错误') {
        setLocalError('舊密碼錯誤')
        return
      }
      if (err.message === '尚未设置访问密码') {
        setLocalError('面板尚未設定存取密碼,無法修改')
        qc.invalidateQueries({ queryKey: QK.authStatus })
        return
      }
      setLocalError(err.message)
    },
  })

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setLocalError('')
    if (!oldPassword) { setLocalError('請輸入舊密碼'); return }
    if (oldPassword.length > PASSWORD_MAX_LENGTH) { setLocalError(`舊密碼最多 ${PASSWORD_MAX_LENGTH} 位`); return }
    if (newPassword.length < 6) { setLocalError('密碼至少 6 位'); return }
    if (newPassword.length > PASSWORD_MAX_LENGTH) { setLocalError(`密碼最多 ${PASSWORD_MAX_LENGTH} 位`); return }
    if (newPassword !== confirmPassword) { setLocalError('兩次密碼不一致'); return }
    changePassword.mutate()
  }

  const configured = authStatus.data?.configured === true

  return (
    <div className="space-y-4">
      <section className="rounded-card border border-border bg-surface p-5">
        <div className="flex items-center gap-2 mb-4">
          <KeyRound className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-medium text-foreground">修改存取密碼</h3>
        </div>

        {authStatus.isLoading ? (
          <p className="text-[11px] text-muted">讀取帳戶狀態中…</p>
        ) : configured ? (
          <form onSubmit={handleSubmit}>
            <label className="block mt-2 space-y-1.5">
              <span className="text-[11px] text-muted">舊密碼</span>
              <input
                type="password"
                autoComplete="current-password"
                maxLength={PASSWORD_MAX_LENGTH}
                value={oldPassword}
                onChange={e => setOldPassword(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="block mt-2 space-y-1.5">
              <span className="text-[11px] text-muted">新密碼</span>
              <input
                type="password"
                autoComplete="new-password"
                maxLength={PASSWORD_MAX_LENGTH}
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="block mt-2 space-y-1.5">
              <span className="text-[11px] text-muted">確認新密碼</span>
              <input
                type="password"
                autoComplete="new-password"
                maxLength={PASSWORD_MAX_LENGTH}
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>

            {localError && (
              <div className="mt-2 flex items-start gap-1.5 rounded-btn bg-danger/10 px-3 py-2 text-[11px] text-danger">
                <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0" />
                <span>{localError}</span>
              </div>
            )}

            <div className="mt-3">
              <button type="submit" disabled={changePassword.isPending} className={BUTTON_CLASS}>
                {changePassword.isPending ? '儲存中…' : '更新密碼'}
              </button>
            </div>

            <p className="mt-2 text-[10px] text-muted leading-relaxed">
              新密碼長度 6 至 {PASSWORD_MAX_LENGTH} 位。更新成功後所有裝置的登入狀態都會失效,包含目前這個瀏覽器,需要重新登入。
            </p>
            <p className="mt-1 text-[10px] text-muted leading-relaxed">
              舊密碼與登入共用同一組失敗計數:連續輸錯 5 次會讓這台機器的 IP 被鎖定 5 分鐘,期間無法登入面板。
            </p>
          </form>
        ) : (
          <div className="flex items-start gap-2.5">
            <ShieldOff className="h-4 w-4 text-muted mt-0.5 shrink-0" />
            <div className="space-y-1.5 text-[11px] text-muted leading-relaxed">
              <p>目前尚未設定面板存取密碼,任何連得到這個網址的人都能直接使用面板。</p>
              <p>
                首次設定密碼只能從伺服器本機或內網操作(公網連線會被拒絕),設定完成後就能在這裡修改密碼。
              </p>
            </div>
          </div>
        )}
      </section>

      {configured && (
        <section className="rounded-card border border-border bg-surface p-5">
          <div className="flex items-center gap-2 mb-4">
            <LogOut className="h-4 w-4 text-accent" />
            <h3 className="text-sm font-medium text-foreground">登出</h3>
          </div>
          <div className="flex items-start justify-between gap-4">
            <p className="text-[11px] text-muted leading-relaxed">
              只結束目前這個瀏覽器的登入狀態,其他裝置的登入不受影響。
            </p>
            <button onClick={handleLogout} disabled={loggingOut} className={BUTTON_CLASS}>
              {loggingOut ? '登出中…' : '登出'}
            </button>
          </div>
        </section>
      )}
    </div>
  )
}

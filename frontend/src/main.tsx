import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider, QueryCache } from '@tanstack/react-query'
import { initializeFrontendExtensions } from './extensions/bootstrap'
import { isLoginRequiredError } from './lib/api'
import './index.css'

// 全局認證攔截: 任何 query/mutation 收到 401 (未登錄/會話過期) → 跳登錄頁。
// api.ts 的 request() 已對 401 靜默 (不彈 toast), 這裡統一負責跳轉。
// 排除 /login 自身的請求, 避免登錄頁請求失敗又跳登錄形成死循環。
const _redirectToLogin = (() => {
  let redirecting = false
  return (err: unknown) => {
    if (redirecting) return
    if (!isLoginRequiredError(err)) return
    // 已在登錄頁則不跳(避免死循環)
    if (window.location.pathname === '/login') return
    redirecting = true
    const redirect = encodeURIComponent(window.location.pathname + window.location.search)
    window.location.href = `/login?redirect=${redirect}`
  }
})()

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (err) => _redirectToLogin(err),
  }),
  defaultOptions: {
    queries: {
      staleTime: 5_000,           // 5s 內複用,與 §4.2 Repository 不變量一致
      refetchOnWindowFocus: false,
    },
    mutations: {
      onError: (err) => _redirectToLogin(err),
    },
  },
})

async function bootstrap() {
  await initializeFrontendExtensions()
  const { router } = await import('./router')
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </React.StrictMode>,
  )
}

void bootstrap()

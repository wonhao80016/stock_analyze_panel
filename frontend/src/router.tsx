import { lazy } from 'react'
import { createBrowserRouter, isRouteErrorResponse, Link, Navigate, useRouteError } from 'react-router-dom'
import { Layout } from './components/Layout'
import { isOnboardingSkippedForSession, Onboarding } from './pages/Onboarding'
import { Auth } from './pages/Auth'
import { useSettings } from './lib/useSharedQueries'
import { Logo } from './components/Logo'
import { ExtensionBoundary } from './extensions/ExtensionBoundary'
import {
  finalizeFrontendExtensions,
  getFrontendExtensionLoadErrors,
  getFrontendExtensionRoutes,
} from './extensions/registry'

// 代码分割: 页面全部 lazy 加载, 避免首屏打包所有页面 (ECharts / framer-motion
// 等重库) → 大幅减小首屏 bundle。命名导出用 .then 映射为 default。
// Layout / Onboarding / Auth 为应用外壳与入口, 保持同步加载。
const Watchlist = lazy(() => import('./pages/Watchlist').then(m => ({ default: m.Watchlist })))
const Monitor = lazy(() => import('./pages/Monitor').then(m => ({ default: m.Monitor })))
const Dashboard = lazy(() => import('./pages/Dashboard').then(m => ({ default: m.Dashboard })))
const Branding = lazy(() => import('./pages/Branding').then(m => ({ default: m.Branding })))
const Settings = lazy(() => import('./pages/Settings').then(m => ({ default: m.Settings })))
const Dev = lazy(() => import('./pages/Dev').then(m => ({ default: m.Dev })))
const TaiwanStockDetail = lazy(() => import('./pages/TaiwanStockDetail').then(m => ({ default: m.TaiwanStockDetail })))
const TaiwanStockCompare = lazy(() => import('./pages/TaiwanStockCompare').then(m => ({ default: m.TaiwanStockCompare })))
const TaiwanScreener = lazy(() => import('./pages/TaiwanScreener').then(m => ({ default: m.TaiwanScreener })))
const Strategies = lazy(() => import('./pages/Strategies').then(m => ({ default: m.Strategies })))
const StrategyBacktest = lazy(() => import('./pages/StrategyBacktest').then(m => ({ default: m.StrategyBacktest })))

const CORE_ROUTE_PATHS = new Set([
  '/',
  '/onboarding',
  '/login',
  '/overview',
  '/stocks/:symbol',
  '/stocks/compare',
  '/watchlist',
  '/taiwan-screener',
  '/strategies',
  '/backtest',
  '/monitor',
  '/branding',
  '/settings',
  '/dev',
  '/settings/keys',
  '/settings/ai',
])

finalizeFrontendExtensions(CORE_ROUTE_PATHS)
const frontendExtensionRoutes = getFrontendExtensionRoutes()
const frontendExtensionErrors = getFrontendExtensionLoadErrors()
if (frontendExtensionErrors.length > 0) {
  console.error('部分前端扩展加载失败', frontendExtensionErrors)
}

// 首次使用守卫 —— 未完成向导则重定向到 /onboarding
// 只挂在根路由上;/onboarding 本身不被守卫,避免循环重定向。
// settings 由 Layout 预取,守卫判定不产生额外请求。
export function OnboardingGuard({ children }: { children: React.ReactNode }) {
  const settings = useSettings()

  // 仅首次加载(本地无缓存)时显示占位。
  // 后台重取 (isFetching) 时本地已有上一份缓存可用, 直接放行, 避免切页时整屏 logo 闪烁。
  // 防误重定向已由 Onboarding/AI 等处 invalidate 前的 setQueryData 同步缓存兜底。
  if (settings.isLoading) {
    return (
      <div className="min-h-screen bg-base grid place-items-center">
        <div className="flex flex-col items-center gap-3 text-muted">
          <Logo size={28} className="text-foreground" />
          <div className="text-xs">載入中…</div>
        </div>
      </div>
    )
  }

  // 查询出错或字段缺失时不拦截 —— 宁可放行,也不把用户卡在空白页
  if (settings.data && settings.data.onboarding_completed === false && !isOnboardingSkippedForSession()) {
    return <Navigate to="/onboarding" replace />
  }

  return <>{children}</>
}

// Browsers word a failed dynamic import differently; all of them mean the page chunk did not load.
const CHUNK_LOAD_ERROR = /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError/i

function RouteErrorPage() {
  const error = useRouteError()
  const notFound = isRouteErrorResponse(error) && error.status === 404
  const message = error instanceof Error ? error.message : ''
  const chunkFailed = CHUNK_LOAD_ERROR.test(message) || (error instanceof Error && error.name === 'ChunkLoadError')
  const title = notFound ? '找不到這個頁面' : chunkFailed ? '頁面載入失敗' : '頁面發生錯誤'
  const detail = chunkFailed
    ? '頁面資源可能已隨版本更新而變動,請重新整理後再試。'
    : notFound
      ? '網址可能有誤,或頁面已經移除。'
      : message || '請重新整理後再試。'
  return (
    <div className="min-h-screen bg-base grid place-items-center px-4">
      <div className="max-w-sm text-center space-y-3">
        <Logo size={28} className="mx-auto text-foreground" />
        <h1 className="text-base font-semibold text-foreground">{title}</h1>
        <p className="text-xs text-muted break-words">{detail}</p>
        <div className="flex items-center justify-center gap-2">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90"
          >
            重新整理
          </button>
          <a href="/" className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:text-foreground">
            回到看板
          </a>
        </div>
      </div>
    </div>
  )
}

function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
      <h1 className="text-base font-semibold text-foreground">找不到這個頁面</h1>
      <p className="text-xs text-muted">網址可能有誤,或頁面已經移除。</p>
      <Link to="/" className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:text-foreground">
        回到看板
      </Link>
    </div>
  )
}

export const router = createBrowserRouter([
  { path: '/onboarding', element: <Onboarding />, errorElement: <RouteErrorPage /> },
  { path: '/login', element: <Auth />, errorElement: <RouteErrorPage /> },
  {
    path: '/',
    element: (
      <OnboardingGuard>
        <Layout />
      </OnboardingGuard>
    ),
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'overview', element: <Navigate to="/" replace /> },
      { path: 'stocks/compare', element: <TaiwanStockCompare /> },
      { path: 'stocks/:symbol', element: <TaiwanStockDetail /> },
      { path: 'watchlist', element: <Watchlist /> },
      { path: 'taiwan-screener', element: <TaiwanScreener /> },
      { path: 'strategies', element: <Strategies /> },
      { path: 'backtest', element: <StrategyBacktest /> },
      { path: 'monitor', element: <Monitor /> },
      { path: 'branding', element: <Branding /> },
      { path: 'settings', element: <Settings /> },
      // 隐藏路由：开发者工具（不暴露在菜单，仅供调试）
      ...(import.meta.env.DEV ? [{ path: 'dev', element: <Dev /> }] : []),
      // 旧路由兼容重定向
      { path: 'settings/keys', element: <Navigate to="/settings?tab=data-sources" replace /> },
      { path: 'settings/ai', element: <Navigate to="/settings?tab=ai" replace /> },
      ...frontendExtensionRoutes.map(route => {
        const ExtensionPage = route.component
        return {
          path: route.path.slice(1),
          element: (
            <ExtensionBoundary extensionId={route.extensionId}>
              <ExtensionPage />
            </ExtensionBoundary>
          ),
        }
      }),
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

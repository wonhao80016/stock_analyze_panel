// Single source of navigation metadata for the sidebar and the menu settings page.
// CORE_NAV entries follow the nav_order / nav_hidden preferences.
import {
  Star,
  LayoutDashboard,
  RadioTower,
  Filter,
  Scale,
  Target,
  BarChart3,
  type LucideIcon,
} from 'lucide-react'

export interface NavMeta {
  to: string
  label: string
  icon: LucideIcon
}

export const CORE_NAV: readonly NavMeta[] = [
  { to: '/',                label: '看板',     icon: LayoutDashboard },
  { to: '/taiwan-screener', label: '台股選股', icon: Filter },
  { to: '/strategies',      label: '策略庫',   icon: Target },
  { to: '/backtest',        label: '策略回測', icon: BarChart3 },
  { to: '/watchlist',  label: '自選股',   icon: Star },
  { to: '/stocks/compare', label: '多股比較', icon: Scale },
  { to: '/monitor', label: '監控中心', icon: RadioTower },
] as const

import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router-dom'

vi.mock('./components/Layout', () => ({ Layout: () => null }))

// Each test re-imports the whole route table, which is slow when the full suite runs in parallel.
vi.setConfig({ testTimeout: 30_000 })

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

async function loadRoutes(): Promise<RouteObject[]> {
  const { router } = await import('./router')
  return router.routes as RouteObject[]
}

function rootRoute(routes: RouteObject[]): RouteObject {
  return routes.find(route => route.path === '/')!
}

function ChunkFailure(): never {
  throw new TypeError('Failed to fetch dynamically imported module: /assets/Watchlist-abc123.js')
}

describe('router error handling', () => {
  it('gives every top-level route an error element', async () => {
    const routes = await loadRoutes()
    for (const route of routes) {
      expect(route.errorElement, route.path).toBeTruthy()
    }
  })

  it('offers a reload when a lazy page chunk fails to load', async () => {
    const root = rootRoute(await loadRoutes())
    const memory = createMemoryRouter(
      [{ path: '/', errorElement: root.errorElement, children: [{ path: 'watchlist', element: <ChunkFailure /> }] }],
      { initialEntries: ['/watchlist'] },
    )
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<RouterProvider router={memory} />)

    expect(await screen.findByRole('button', { name: '重新整理' })).toBeInTheDocument()
    expect(screen.getByText(/頁面載入失敗/)).toBeInTheDocument()
  })

  it('renders an in-app 404 page for unknown paths inside the layout', async () => {
    const root = rootRoute(await loadRoutes())
    const notFound = root.children?.find(route => route.path === '*')
    expect(notFound).toBeTruthy()

    const memory = createMemoryRouter([{ path: '*', element: notFound!.element }], { initialEntries: ['/no-such-page'] })
    render(<RouterProvider router={memory} />)
    expect(await screen.findByText('找不到這個頁面')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '回到看板' })).toHaveAttribute('href', '/')
  })
})

describe('/dev route', () => {
  function childPaths(routes: RouteObject[]): (string | undefined)[] {
    return (rootRoute(routes).children ?? []).map(route => route.path)
  }

  it('is registered in development builds', async () => {
    vi.stubEnv('DEV', true)
    expect(childPaths(await loadRoutes())).toContain('dev')
  })

  it('is not registered in production builds', async () => {
    vi.stubEnv('DEV', false)
    expect(childPaths(await loadRoutes())).not.toContain('dev')
  })
})

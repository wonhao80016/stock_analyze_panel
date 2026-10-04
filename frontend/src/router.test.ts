import { describe, expect, it } from 'vitest'
import { router } from './router'

function collectPaths(routes: typeof router.routes): string[] {
  const paths: string[] = []
  for (const r of routes) {
    if (r.path) paths.push(r.path)
    if (r.children) paths.push(...collectPaths(r.children as typeof router.routes))
  }
  return paths
}

describe('router', () => {
  const allPaths = collectPaths(router.routes)

  it('registers the Taiwan backtest workspace without restoring legacy routes', () => {
    expect(allPaths).not.toContain('screener')
    expect(allPaths).toContain('backtest')
    expect(allPaths).not.toContain('mining')
  })

  it('still registers the 5 Taiwan core routes', () => {
    expect(allPaths).toContain('watchlist')
    expect(allPaths).toContain('taiwan-screener')
    expect(allPaths).toContain('monitor')
    expect(allPaths).toContain('stocks/compare')
  })
})

import { createContext, lazy, Suspense, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

// The dialog pulls in the stock detail page and ECharts, so it stays out of the first-screen bundle.
const StockPreviewDialog = lazy(() =>
  import('@/components/StockPreviewDialog').then(m => ({ default: m.StockPreviewDialog })),
)

interface PreviewTarget {
  symbol: string
  name?: string
}

interface StockPreviewContextValue {
  openPreview: (symbol: string, name?: string) => void
  closePreview: () => void
}

// Rendering a card-opening component outside the provider (tests, storybooks)
// must not crash the tree; the click simply does nothing.
const FALLBACK: StockPreviewContextValue = {
  openPreview: () => {},
  closePreview: () => {},
}

const StockPreviewContext = createContext<StockPreviewContextValue>(FALLBACK)

/** Mounted once so any page can open the preview card instead of routing away. */
export function StockPreviewProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<PreviewTarget | null>(null)
  // Stays mounted after the first open so the close animation still plays.
  const [dialogRequested, setDialogRequested] = useState(false)

  const openPreview = useCallback((symbol: string, name?: string) => {
    if (!symbol) return
    setDialogRequested(true)
    setTarget({ symbol, name })
  }, [])

  const closePreview = useCallback(() => setTarget(null), [])

  const value = useMemo(() => ({ openPreview, closePreview }), [openPreview, closePreview])

  return (
    <StockPreviewContext.Provider value={value}>
      {children}
      {dialogRequested && (
        <Suspense fallback={null}>
          <StockPreviewDialog
            symbol={target?.symbol ?? null}
            name={target?.name ?? ''}
            onClose={closePreview}
          />
        </Suspense>
      )}
    </StockPreviewContext.Provider>
  )
}

export function useStockPreview(): StockPreviewContextValue {
  return useContext(StockPreviewContext)
}

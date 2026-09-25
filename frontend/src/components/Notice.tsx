import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { Symbol } from './Symbol'

const NoticeContext = createContext<(text: string) => void>(() => {})

/** A short sentence at the bottom center that disappears again on its own. */
export function NoticeProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null)
  const notify = useCallback((text: string) => setMessage(text), [])

  useEffect(() => {
    if (!message) return
    const timer = window.setTimeout(() => setMessage(null), 2600)
    return () => window.clearTimeout(timer)
  }, [message])

  return (
    <NoticeContext.Provider value={notify}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center px-4" role="status" aria-live="polite">
        {message && (
          <p className="flex items-center gap-2 rounded-full border border-accent-500/40 bg-ink-850 px-4 py-2 text-sm text-mist-200 shadow-2xl shadow-black/50">
            <Symbol name="info" className="h-4 w-4 text-accent-400" />
            {message}
          </p>
        )}
      </div>
    </NoticeContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useNotice(): (text: string) => void {
  return useContext(NoticeContext)
}

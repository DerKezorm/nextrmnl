import { useCallback, useEffect, useRef, useState } from 'react'

import { errorMessage } from '../api/client'

/**
 * Loads something from the server and holds the result, error and loading state. If a value in `deps`
 * changes, it reloads. `reload` fetches again by hand, `set` replaces the state without a request.
 */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const loadRef = useRef(load)
  loadRef.current = load
  const key = JSON.stringify(deps)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      setData(await loadRef.current())
      setError(null)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload, key])

  return { data, error, loading, reload, set: setData }
}

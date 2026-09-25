/**
 * Signed in or not, and who. The session cookie cannot be read by this script, so it asks
 * `/api/setup` and `/api/auth/me` at startup. The vault state (`account.vault`) is tied to this: if the
 * server locks the vault after the idle timeout, `refresh` picks that up.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { ApiError, api, setSignedOutHandler } from './api/client'
import type { Account, SetupState, VaultState } from './api/types'

export type AuthState = 'loading' | 'setup' | 'signed_out' | 'signed_in' | 'unreachable'

type Auth = {
  state: AuthState
  account: Account | null
  refresh: () => Promise<void>
  setup: (name: string, password: string) => Promise<void>
  signIn: (name: string, password: string) => Promise<void>
  acceptInvite: (token: string, name: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  setVault: (state: VaultState) => void
  setAccount: (account: Account) => void
}

const AuthContext = createContext<Auth | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>('loading')
  const [account, setAccount] = useState<Account | null>(null)

  const refresh = useCallback(async () => {
    try {
      const setupState = await api.get<SetupState>('/api/setup')
      if (setupState.needs_setup) {
        setAccount(null)
        setState('setup')
        return
      }
    } catch {
      setState('unreachable')
      return
    }
    try {
      setAccount(await api.get<Account>('/api/auth/me'))
      setState('signed_in')
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setAccount(null)
        setState('signed_out')
      } else {
        setState('unreachable')
      }
    }
  }, [])

  useEffect(() => {
    void refresh()
    setSignedOutHandler(() => {
      setAccount(null)
      setState('signed_out')
    })
    return () => setSignedOutHandler(null)
  }, [refresh])

  // The vault locks itself on the server; checking once a minute is enough.
  useEffect(() => {
    if (state !== 'signed_in') return
    const timer = window.setInterval(() => void refresh(), 60_000)
    return () => window.clearInterval(timer)
  }, [state, refresh])

  const signedIn = useCallback((next: Account) => {
    setAccount(next)
    setState('signed_in')
  }, [])

  const value = useMemo<Auth>(
    () => ({
      state,
      account,
      refresh,
      setup: async (name, password) => signedIn(await api.post<Account>('/api/setup', { name, password })),
      signIn: async (name, password) => signedIn(await api.post<Account>('/api/auth/login', { name, password })),
      acceptInvite: async (token, name, password) =>
        signedIn(await api.post<Account>(`/api/invites/${encodeURIComponent(token)}`, { name, password })),
      signOut: async () => {
        try {
          await api.post('/api/auth/logout')
        } finally {
          setAccount(null)
          setState('signed_out')
        }
      },
      setVault: (vault) => setAccount((current) => (current ? { ...current, vault } : current)),
      setAccount: signedIn,
    }),
    [state, account, refresh, signedIn],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): Auth {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth outside AuthProvider')
  return value
}

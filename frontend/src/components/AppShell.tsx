import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { api, errorMessage } from '../api/client'
import type { AboutInfo } from '../api/types'
import { useAuth } from '../auth'
import { adoptTerminalPrefs } from '../lib/terminalPrefs'
import { useLoad } from '../lib/useLoad'
import { UnlockScreen, VaultSetupScreen } from '../pages/UnlockScreen'
import { useWorkspace } from '../state/workspace'
import { LanguageSwitcher } from './LanguageSwitcher'
import { Logo } from './Logo'
import { useNotice } from './Notice'
import { Symbol, type SymbolName } from './Symbol'
import { ThemeSwitcher } from './ThemeSwitcher'

type NavItem = { to: string; label: string; symbol: SymbolName; end: boolean; right?: boolean }

function navClass(isActive: boolean, compact: boolean, right = false): string {
  return (
    (compact ? 'shrink-0 px-3 ' : 'px-3.5 ') +
    (right ? 'ml-auto ' : '') +
    'inline-flex items-center gap-2 rounded-full py-1.5 text-sm font-medium transition-colors ' +
    (isActive ? 'bg-accent-500/15 text-accent-400' : 'text-mist-500 hover:bg-ink-850 hover:text-mist-100')
  )
}

/**
 * Shell like nexcrate, Nexview and nexpulse: header with pills, settings on the right, everything in a
 * centered column. On the workspace, the list and terminal fill the window height, with no footer.
 */
export function AppShell() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { account, setVault, signOut } = useAuth()
  const { sessions } = useWorkspace()
  const location = useLocation()
  const about = useLoad(() => api.get<AboutInfo>('/api/about'))
  const workspace = location.pathname === '/'
  const vault = account?.vault ?? 'locked'

  // The terminal settings are tied to the account; another device gets them this way at sign-in.
  const remotePrefs = account?.prefs
  useEffect(() => {
    adoptTerminalPrefs(remotePrefs)
  }, [remotePrefs])

  async function lockVault() {
    try {
      await api.post('/api/vault/lock')
      setVault('locked')
    } catch (error) {
      notify(errorMessage(error))
    }
  }
  const openCount = sessions.filter((s) => s.status === 'open' || s.status === 'connecting').length

  const items: NavItem[] = [
    { to: '/', label: t('nav.connections'), symbol: 'terminal', end: true },
    { to: '/sessions', label: t('nav.sessions'), symbol: 'sessions', end: false },
    { to: '/vault', label: t('nav.vault'), symbol: 'vault', end: false },
    { to: '/settings', label: t('nav.settings'), symbol: 'settings', end: false, right: true },
  ]

  const render = (item: NavItem, compact: boolean) => (
    <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => navClass(isActive, compact, item.right)}>
      <span className={compact ? 'hidden min-[441px]:inline' : ''}>
        <Symbol name={item.symbol} />
      </span>
      {item.label}
      {item.to === '/' && openCount > 0 && <span className="rounded-full bg-accent-500 px-1.5 text-[10px] leading-4 font-bold text-on-accent tabular-nums">{openCount}</span>}
    </NavLink>
  )

  return (
    <div className={'nt-glow flex flex-col ' + (workspace ? 'h-dvh overflow-hidden' : 'min-h-dvh')}>
      <header className="sticky top-0 z-20 shrink-0 border-b border-ink-700/80 bg-ink-950/80 backdrop-blur-xl">
        {/* Header, pages and workspace share the same centered column, so nothing jumps when switching. */}
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3 sm:px-6">
          <NavLink to="/" className="shrink-0" aria-label={t('nav.home')}>
            <Logo withWordmark />
          </NavLink>
          <nav className="hidden flex-1 items-center gap-1 lg:flex" aria-label={t('nav.main')}>
            {items.map((item) => render(item, false))}
          </nav>
          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            {vault === 'open' ? (
              <button
                type="button"
                onClick={() => void lockVault()}
                className="inline-flex items-center gap-1.5 rounded-full border border-ok-500/30 bg-ok-500/10 px-2.5 py-1.5 text-xs font-medium text-ok-500 hover:bg-ok-500/20"
                title={t('vault.lockNow')}
              >
                <Symbol name="unlocked" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{t('vault.openShort')}</span>
              </button>
            ) : (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-xs font-medium text-mist-500">
                <Symbol name="vault" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{t('vault.lockedShort')}</span>
              </span>
            )}
            <ThemeSwitcher />
            <LanguageSwitcher />
            <span className="flex h-8 w-8 items-center justify-center rounded-full border border-ink-700 bg-ink-850 text-xs font-semibold text-mist-300 uppercase" title={account?.name}>
              {account?.name.slice(0, 1)}
            </span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-full border border-ink-700 bg-ink-850 p-1.5 text-mist-500 hover:text-mist-100"
              title={t('auth.signOut')}
              aria-label={t('auth.signOut')}
            >
              <Symbol name="logout" />
            </button>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto border-t border-ink-700/60 px-4 py-2 lg:hidden" aria-label={t('nav.main')}>
          {items.map((item) => render(item, true))}
        </nav>
      </header>

      {workspace ? (
        <main className="relative z-10 mx-auto flex min-h-0 w-full max-w-7xl flex-1 px-4 py-4 sm:px-6 sm:py-6">
          <Outlet />
        </main>
      ) : (
        <>
          <main className="relative z-10 mx-auto w-full max-w-7xl flex-1 px-4 pt-8 pb-16 sm:px-6">
            <Outlet />
          </main>
          <footer className="relative z-10 border-t border-ink-700/60">
            <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-x-4 gap-y-2 px-4 py-5 text-xs text-mist-600 sm:px-6">
              <NavLink to="/about" className="transition-colors hover:text-mist-300">
                {t('about.title')}
              </NavLink>
              <span aria-hidden="true">·</span>
              <span className="tabular-nums">v{about.data?.version ?? ''}</span>
              {about.data?.update_available && (
                <NavLink
                  to="/about"
                  className="inline-flex items-center gap-1.5 rounded-full bg-accent-500/15 px-2.5 py-1 font-medium text-accent-400 transition-colors hover:bg-accent-500/25"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-accent-400" aria-hidden="true" />
                  {t('about.updateShort')}
                </NavLink>
              )}
            </div>
          </footer>
        </>
      )}

      {vault === 'locked' && <UnlockScreen />}
      {vault === 'unset' && <VaultSetupScreen />}
    </div>
  )
}

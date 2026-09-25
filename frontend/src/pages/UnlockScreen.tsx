import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../api/client'
import { useAuth } from '../auth'
import { Logo } from '../components/Logo'
import { Symbol } from '../components/Symbol'
import { Banner, Button, Card, Field } from '../components/ui'
import { MIN_PASSWORD } from '../lib/rules'
import { useWorkspace } from '../state/workspace'

/** Locked vault. Open sessions keep running, only the UI is covered. */
export function UnlockScreen() {
  const { t } = useTranslation()
  const { setVault, signOut } = useAuth()
  const { sessions } = useWorkspace()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const running = sessions.filter((s) => s.status === 'open').length

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!password) return
    setBusy(true)
    try {
      await api.post('/api/vault/unlock', { password })
      setPassword('')
      setVault('open')
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="nt-glow fixed inset-0 z-40 flex items-center justify-center bg-ink-950 p-4">
      <Card className="relative z-10 flex w-full max-w-sm flex-col items-center gap-5 text-center">
        <Logo className="h-12 w-12" />
        <div>
          <h1 className="flex items-center justify-center gap-2 text-xl font-bold">
            <Symbol name="vault" className="h-5 w-5 text-accent-400" />
            {t('unlock.title')}
          </h1>
          <p className="mt-1 text-sm text-mist-500">{t('unlock.lead')}</p>
        </div>
        <form className="flex w-full flex-col gap-3 text-left" onSubmit={(event) => void submit(event)}>
          <Field
            label={t('unlock.password')}
            type="password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value)
              setError(null)
            }}
            autoFocus
            autoComplete="current-password"
          />
          {error && <Banner tone="bad">{error}</Banner>}
          <Button type="submit" className="w-full" loading={busy}>
            {t('unlock.open')}
          </Button>
        </form>
        {running > 0 && <p className="text-xs text-mist-500">{t('unlock.running', { count: running })}</p>}
        <button type="button" onClick={() => void signOut()} className="text-xs text-mist-600 hover:text-mist-300">
          {t('auth.signOut')}
        </button>
      </Card>
    </div>
  )
}

/** OIDC accounts set their own vault password the first time. Without a vault there are no keys. */
export function VaultSetupScreen() {
  const { t } = useTranslation()
  const { setVault, signOut } = useAuth()
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const valid = password.length >= MIN_PASSWORD && repeat === password

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    setBusy(true)
    try {
      await api.post('/api/vault/setup', { password })
      setVault('open')
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="nt-glow fixed inset-0 z-40 flex items-center justify-center bg-ink-950 p-4">
      <Card className="relative z-10 flex w-full max-w-md flex-col gap-5">
        <div className="flex items-center gap-3">
          <Logo className="h-10 w-10" />
          <div>
            <h1 className="text-xl font-bold">{t('vaultSetup.title')}</h1>
            <p className="mt-0.5 text-sm text-mist-500">{t('vaultSetup.lead')}</p>
          </div>
        </div>
        <form className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
          <Field
            label={t('vaultSetup.password')}
            type="password"
            value={password}
            autoFocus
            autoComplete="new-password"
            hint={t('auth.passwordHint', { min: MIN_PASSWORD })}
            error={password.length > 0 && password.length < MIN_PASSWORD ? t('auth.passwordTooShort', { min: MIN_PASSWORD }) : null}
            onChange={(event) => setPassword(event.target.value)}
          />
          <Field label={t('auth.passwordRepeat')} type="password" value={repeat} autoComplete="new-password" error={repeat.length > 0 && repeat !== password ? t('auth.passwordMismatch') : null} onChange={(event) => setRepeat(event.target.value)} />
          <Banner tone="warn">{t('vaultSetup.warning')}</Banner>
          {error && <Banner tone="bad">{error}</Banner>}
          <Button type="submit" loading={busy} disabled={!valid}>
            {t('vaultSetup.submit')}
          </Button>
        </form>
        <button type="button" onClick={() => void signOut()} className="self-center text-xs text-mist-600 hover:text-mist-300">
          {t('auth.signOut')}
        </button>
      </Card>
    </div>
  )
}

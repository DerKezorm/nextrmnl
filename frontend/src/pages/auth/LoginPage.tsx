import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { api, errorMessage, errorText } from '../../api/client'
import type { OidcState } from '../../api/types'
import { useAuth } from '../../auth'
import { Symbol } from '../../components/Symbol'
import { Banner, Button, Field } from '../../components/ui'
import { useLoad } from '../../lib/useLoad'
import { AuthFrame } from './AuthFrame'

export function LoginPage() {
  const { t } = useTranslation()
  const { signIn } = useAuth()
  const [params] = useSearchParams()
  const oidc = useLoad(() => api.get<OidcState>('/api/oidc/state').catch(() => ({ enabled: false, provider_name: '' })))
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(params.get('error') ? errorText(params.get('error')) || t('errors.generic') : null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || !password) {
      setError(t('auth.required'))
      return
    }
    setBusy(true)
    try {
      await signIn(name.trim(), password)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthFrame>
      <form className="flex flex-col gap-4" onSubmit={(event) => void submit(event)} noValidate>
        <h1 className="text-xl font-bold">{t('auth.title')}</h1>
        <Field label={t('auth.name')} autoComplete="username" autoFocus value={name} spellCheck={false} onChange={(event) => setName(event.target.value)} />
        <Field
          label={t('auth.password')}
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => {
            setPassword(event.target.value)
            setError(null)
          }}
        />
        {error && <Banner tone="bad">{error}</Banner>}
        <Button type="submit" loading={busy}>
          {t('auth.signIn')}
        </Button>
        {oidc.data?.enabled && (
          <>
            <p className="text-center text-xs text-mist-600">{t('auth.or')}</p>
            <a href="/api/oidc/start" className="inline-flex items-center justify-center gap-2 rounded-full border border-ink-700 bg-ink-850 px-5 py-2.5 text-sm font-semibold text-mist-300 hover:bg-ink-800 hover:text-mist-100">
              <Symbol name="shield" />
              {t('auth.oidc', { provider: oidc.data.provider_name || 'OIDC' })}
            </a>
          </>
        )}
      </form>
    </AuthFrame>
  )
}

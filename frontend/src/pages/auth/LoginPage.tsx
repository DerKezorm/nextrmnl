import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { ApiError, api, errorMessage, errorText } from '../../api/client'
import type { OidcState } from '../../api/types'
import { useAuth } from '../../auth'
import { Symbol } from '../../components/Symbol'
import { Banner, Button, Field } from '../../components/ui'
import { useLoad } from '../../lib/useLoad'
import { AuthFrame } from './AuthFrame'

export function LoginPage() {
  const { t } = useTranslation()
  const { signIn, signInCode, cancelSecondFactor } = useAuth()
  const [params] = useSearchParams()
  const oidc = useLoad(() => api.get<OidcState>('/api/oidc/state').catch(() => ({ enabled: false, provider_name: '' })))
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  // The second step, when the account has a second factor: a code from the app or a recovery code.
  const [step, setStep] = useState<'password' | 'code'>('password')
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [error, setError] = useState<string | null>(params.get('error') ? errorText(params.get('error')) || t('errors.generic') : null)
  const [busy, setBusy] = useState(false)

  async function submitPassword() {
    if (!name.trim() || !password) {
      setError(t('auth.required'))
      return
    }
    setBusy(true)
    try {
      const outcome = await signIn(name.trim(), password)
      if (outcome === 'second_factor') {
        // The password is not needed any more and does not stay in the page.
        setPassword('')
        setStep('code')
        setError(null)
      }
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function submitCode() {
    if (!code.trim()) return
    setBusy(true)
    try {
      await signInCode(code)
    } catch (caught) {
      setError(errorMessage(caught))
      setCode('')
      // Too many wrong codes or too slow: the password step starts over.
      if (caught instanceof ApiError && caught.code === 'second_factor_expired') setStep('password')
    } finally {
      setBusy(false)
    }
  }

  async function back() {
    await cancelSecondFactor()
    setStep('password')
    setCode('')
    setRecovery(false)
    setError(null)
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void (step === 'code' ? submitCode() : submitPassword())
  }

  if (step === 'code') {
    return (
      <AuthFrame>
        <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
          <h1 className="text-xl font-bold">{t('twofactor.stepTitle')}</h1>
          <p className="text-sm text-mist-500">{t('twofactor.stepLead', { name: name.trim() })}</p>
          <Field
            key={recovery ? 'recovery' : 'code'}
            label={recovery ? t('twofactor.recoveryLabel') : t('twofactor.codeLabel')}
            hint={recovery ? t('twofactor.recoveryHint') : t('twofactor.codeHint')}
            autoComplete="one-time-code"
            inputMode={recovery ? 'text' : 'numeric'}
            autoFocus
            spellCheck={false}
            value={code}
            onChange={(event) => {
              setCode(event.target.value)
              setError(null)
            }}
          />
          {error && <Banner tone="bad">{error}</Banner>}
          <Button type="submit" loading={busy} disabled={!code.trim()}>
            {t('auth.signIn')}
          </Button>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <button
              type="button"
              className="text-mist-500 hover:text-mist-100"
              onClick={() => {
                setRecovery(!recovery)
                setCode('')
                setError(null)
              }}
            >
              {recovery ? t('twofactor.useApp') : t('twofactor.useRecovery')}
            </button>
            <button type="button" className="text-mist-500 hover:text-mist-100" onClick={() => void back()}>
              {t('twofactor.back')}
            </button>
          </div>
        </form>
      </AuthFrame>
    )
  }

  return (
    <AuthFrame>
      <form className="flex flex-col gap-4" onSubmit={submit} noValidate>
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

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useSearchParams } from 'react-router-dom'

import { ApiError, api, errorMessage } from '../../api/client'
import type { SecondFactorMethod, SignInMethods } from '../../api/types'
import { useAuth } from '../../auth'
import { ProviderButtons } from '../../components/ProviderButtons'
import { Symbol } from '../../components/Symbol'
import { Banner, Button, Field } from '../../components/ui'
import { useLoad } from '../../lib/useLoad'
import { passkeysAvailable } from '../../lib/webauthn'
import { oidcErrorKey } from '../../vendor/nexoidc/oidc'
import { AuthFrame } from './AuthFrame'

const NO_METHODS: SignInMethods = { password: true, providers: [] }

export function LoginPage() {
  const { t } = useTranslation()
  const { signIn, signInCode, signInPasskey, cancelSecondFactor } = useAuth()
  const [params, setParams] = useSearchParams()
  // Signed out, every address shows this page; the address is the page the person came for.
  const location = useLocation()
  const available = useLoad(() => api.get<SignInMethods>('/api/auth/methods').catch(() => NO_METHODS))
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  // The second step, when the account has a second factor: a code from the app or a recovery code.
  const [step, setStep] = useState<'password' | 'code'>('password')
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState(false)
  const [methods, setMethods] = useState<SecondFactorMethod[]>([])
  // Only codes of the shared sign-in module come through the address, never shown raw.
  const shownError = (code: string | null) => {
    const key = oidcErrorKey(code)
    return key ? t(key) : code ? t('errors.generic') : null
  }
  const [error, setError] = useState<string | null>(shownError(params.get('error')))
  const [busy, setBusy] = useState(false)
  // Password sign-in off: only the buttons, and below them small the operator's way in.
  const [operatorWay, setOperatorWay] = useState(false)
  const methodsOf = available.data ?? NO_METHODS
  const passwordShown = methodsOf.password || operatorWay

  // Back from a provider that does not check the second factor itself: the code of nextrmnl's own is still due.
  const codeStep = params.get('step') === 'code'
  useEffect(() => {
    if (!codeStep) return
    let current = true
    api
      .get<{ name: string; methods: SecondFactorMethod[] }>('/api/auth/login/pending')
      .then((waiting) => {
        if (!current) return
        setName(waiting.name)
        setMethods(waiting.methods)
        setRecovery(!waiting.methods.includes('totp'))
        setStep('code')
      })
      .catch((caught: unknown) => {
        if (current) setError(errorMessage(caught))
      })
      .finally(() => {
        if (current) setParams({}, { replace: true })
      })
    return () => {
      current = false
    }
  }, [codeStep, setParams])

  async function submitPassword() {
    if (!name.trim() || !password) {
      setError(t('auth.required'))
      return
    }
    setBusy(true)
    try {
      const outcome = await signIn(name.trim(), password)
      if (!outcome.done) {
        // The password is not needed any more and does not stay in the page.
        setPassword('')
        setMethods(outcome.methods)
        // Without an app, the field is for a recovery code.
        setRecovery(!outcome.methods.includes('totp'))
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

  async function submitPasskey() {
    setBusy(true)
    setError(null)
    try {
      await signInPasskey()
    } catch (caught) {
      // The person closed the browser's prompt: no message, the buttons stay.
      if (caught instanceof DOMException && caught.name === 'NotAllowedError') setError(t('passkeys.cancelled'))
      else setError(errorMessage(caught))
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
          {methods.includes('passkey') && (
            <>
              <Button type="button" loading={busy} onClick={() => void submitPasskey()} disabled={!passkeysAvailable()}>
                <Symbol name="key" />
                {t('passkeys.signIn')}
              </Button>
              {!passkeysAvailable() && <p className="text-xs text-mist-500">{t('passkeys.notHere')}</p>}
              <p className="text-center text-xs text-mist-600">{t('auth.or')}</p>
            </>
          )}
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
          <Button type="submit" variant={methods.includes('passkey') ? 'ghost' : 'primary'} loading={busy} disabled={!code.trim()}>
            {t('auth.signIn')}
          </Button>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            {methods.includes('totp') ? (
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
            ) : (
              <span />
            )}
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
        {error && !passwordShown && <Banner tone="bad">{error}</Banner>}
        {passwordShown && (
          <>
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
          </>
        )}
        <ProviderButtons providers={methodsOf.providers} next={location.pathname} withOr={passwordShown} />
        {!passwordShown && available.data && (
          <button type="button" className="text-center text-xs text-mist-500 hover:text-mist-100" onClick={() => setOperatorWay(true)}>
            {t('oidc.login.operator')}
          </button>
        )}
      </form>
    </AuthFrame>
  )
}

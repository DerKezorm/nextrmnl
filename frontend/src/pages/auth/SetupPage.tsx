import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorMessage } from '../../api/client'
import { useAuth } from '../../auth'
import { Banner, Button, Field } from '../../components/ui'
import { MIN_PASSWORD } from '../../lib/rules'
import { AuthFrame } from './AuthFrame'

/** Form for a new account with a password: the first start and accepting an invite. */
export function AccountForm({
  title,
  lead,
  submitLabel,
  initialName = '',
  onSubmit,
}: {
  title: string
  lead: string
  submitLabel: string
  initialName?: string
  onSubmit: (name: string, password: string) => Promise<void>
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(initialName)
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const tooShort = password.length > 0 && password.length < MIN_PASSWORD
  const mismatch = repeat.length > 0 && repeat !== password
  const valid = name.trim().length >= 2 && password.length >= MIN_PASSWORD && repeat === password

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!valid) return
    setBusy(true)
    try {
      await onSubmit(name.trim(), password)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="flex flex-col gap-4" onSubmit={(event) => void submit(event)} noValidate>
      <div>
        <h1 className="text-xl font-bold">{title}</h1>
        <p className="mt-1 text-sm text-mist-500">{lead}</p>
      </div>
      <Field label={t('auth.name')} autoComplete="username" autoFocus value={name} spellCheck={false} hint={t('auth.nameHint')} onChange={(event) => setName(event.target.value)} />
      <Field
        label={t('auth.password')}
        type="password"
        autoComplete="new-password"
        value={password}
        hint={t('auth.passwordHint', { min: MIN_PASSWORD })}
        error={tooShort ? t('auth.passwordTooShort', { min: MIN_PASSWORD }) : null}
        onChange={(event) => setPassword(event.target.value)}
      />
      <Field label={t('auth.passwordRepeat')} type="password" autoComplete="new-password" value={repeat} error={mismatch ? t('auth.passwordMismatch') : null} onChange={(event) => setRepeat(event.target.value)} />
      <Banner>{t('auth.vaultNote')}</Banner>
      {error && <Banner tone="bad">{error}</Banner>}
      <Button type="submit" loading={busy} disabled={!valid}>
        {submitLabel}
      </Button>
    </form>
  )
}

export function SetupPage() {
  const { t } = useTranslation()
  const { setup } = useAuth()
  return (
    <AuthFrame wide>
      <AccountForm title={t('auth.setupTitle')} lead={t('auth.setupLead')} submitLabel={t('auth.setupSubmit')} initialName="admin" onSubmit={setup} />
    </AuthFrame>
  )
}

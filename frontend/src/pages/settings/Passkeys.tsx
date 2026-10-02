import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../../api/client'
import type { Account, PasskeyInfo } from '../../api/types'
import { useAuth } from '../../auth'
import { Dialog } from '../../components/Dialog'
import { useNotice } from '../../components/Notice'
import { Symbol } from '../../components/Symbol'
import { Banner, Button, Field } from '../../components/ui'
import { formatDateTime, formatRelative } from '../../lib/format'
import { useLoad } from '../../lib/useLoad'
import { createPasskey, passkeysAvailable } from '../../lib/webauthn'

type Added = { passkey: PasskeyInfo; recovery_codes: string[] | null; account: Account }

/**
 * Passkeys and security keys as the second factor, next to the app or instead of it. Adding asks the browser for
 * a new key and the password for the change; removing asks for the password. The first second factor brings
 * recovery codes, handed to `onCodes` to show them once.
 */
export function PasskeysPart({ onCodes }: { onCodes: (codes: string[]) => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const { setAccount } = useAuth()
  const keys = useLoad(() => api.get<PasskeyInfo[]>('/api/auth/passkeys'))
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<PasskeyInfo | null>(null)
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const available = passkeysAvailable()

  function close() {
    setAdding(false)
    setRemoving(null)
    setName('')
    setPassword('')
    setError(null)
  }

  async function add() {
    setBusy(true)
    setError(null)
    try {
      const begun = await api.post<{ options: string }>('/api/auth/passkeys/begin')
      const credential = await createPasskey(begun.options)
      const result = await api.post<Added>('/api/auth/passkeys/finish', { name: name.trim(), password, credential })
      setAccount(result.account)
      close()
      void keys.reload()
      notify(t('passkeys.added', { name: result.passkey.name }))
      if (result.recovery_codes) onCodes(result.recovery_codes)
    } catch (caught) {
      setError(caught instanceof DOMException ? t('passkeys.cancelled') : errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!removing) return
    setBusy(true)
    setError(null)
    try {
      setAccount(await api.post<Account>(`/api/auth/passkeys/${removing.id}/remove`, { password }))
      notify(t('passkeys.removed', { name: removing.name }))
      close()
      void keys.reload()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 border-t border-ink-700 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-mist-100">{t('passkeys.title')}</p>
          <p className="text-xs text-mist-500">{t('passkeys.lead')}</p>
        </div>
        <Button size="sm" variant="ghost" disabled={!available} onClick={() => setAdding(true)}>
          <Symbol name="key" className="h-3.5 w-3.5" />
          {t('passkeys.add')}
        </Button>
      </div>
      {!available && <Banner>{t('passkeys.notHere')}</Banner>}
      {(keys.data ?? []).length > 0 && (
        <ul className="flex flex-col divide-y divide-ink-700 rounded-xl border border-ink-700">
          {(keys.data ?? []).map((key) => (
            <li key={key.id} className="flex items-center gap-3 px-3 py-2">
              <Symbol name="key" className="h-4 w-4 text-mist-500" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-mist-100">{key.name}</p>
                <p className="text-xs text-mist-500">
                  {t('passkeys.since', { when: formatDateTime(key.created_at) })} ·{' '}
                  {key.last_used_at ? t('passkeys.lastUsed', { when: formatRelative(key.last_used_at) }) : t('passkeys.neverUsed')}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setRemoving(key)}
                className="rounded-full p-1.5 text-mist-500 hover:bg-bad-500/10 hover:text-bad-500"
                aria-label={t('passkeys.removeNamed', { name: key.name })}
              >
                <Symbol name="trash" />
              </button>
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={adding}
        title={t('passkeys.addTitle')}
        onClose={close}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button loading={busy} disabled={!password} onClick={() => void add()}>
              {t('passkeys.addNow')}
            </Button>
          </>
        }
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (password) void add()
          }}
        >
          <p className="text-sm text-mist-300">{t('passkeys.addLead')}</p>
          <Field label={t('passkeys.name')} hint={t('passkeys.nameHint')} value={name} maxLength={64} onChange={(event) => setName(event.target.value)} placeholder="YubiKey" autoFocus />
          <Field label={t('twofactor.setupPassword')} type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          {error && <Banner tone="bad">{error}</Banner>}
        </form>
      </Dialog>

      <Dialog
        open={removing !== null}
        title={t('passkeys.removeNamed', { name: removing?.name ?? '' })}
        onClose={close}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} disabled={!password} onClick={() => void remove()}>
              {t('passkeys.remove')}
            </Button>
          </>
        }
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (password) void remove()
          }}
        >
          <p className="text-sm text-mist-300">{t('passkeys.removeText')}</p>
          <Field label={t('twofactor.setupPassword')} type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus />
          {error && <Banner tone="bad">{error}</Banner>}
        </form>
      </Dialog>
    </div>
  )
}

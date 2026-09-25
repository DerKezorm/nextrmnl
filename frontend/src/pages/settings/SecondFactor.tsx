import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../../api/client'
import type { Account, RecoveryCodes, TotpEnrolment } from '../../api/types'
import { useAuth } from '../../auth'
import { Dialog } from '../../components/Dialog'
import { useNotice } from '../../components/Notice'
import { Symbol } from '../../components/Symbol'
import { Banner, Button, Field, Section } from '../../components/ui'
import { writeClipboard } from '../../lib/clipboard'

/** Below this many recovery codes the account is told to make new ones. */
const LOW_CODES = 3

function saveAsFile(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/** The second factor of the own account: set up, turn off, new recovery codes. */
export function SecondFactorSection({ account, oidcOnly, provider }: { account: Account; oidcOnly: boolean; provider: string }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const { setAccount } = useAuth()
  const [enrolment, setEnrolment] = useState<TotpEnrolment | null>(null)
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const [asking, setAsking] = useState<'disable' | 'renew' | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function closeAll() {
    setEnrolment(null)
    setAsking(null)
    setCode('')
    setPassword('')
    setError(null)
  }

  async function begin() {
    setBusy(true)
    try {
      setEnrolment(await api.post<TotpEnrolment>('/api/auth/totp/begin'))
      setError(null)
    } catch (caught) {
      notify(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<RecoveryCodes>('/api/auth/totp/confirm', { code: code.trim(), password })
      closeAll()
      setAccount(result.account)
      setCodes(result.recovery_codes)
      notify(t('twofactor.enabled'))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function withPassword() {
    setBusy(true)
    setError(null)
    try {
      if (asking === 'disable') {
        setAccount(await api.post<Account>('/api/auth/totp/disable', { password }))
        notify(t('twofactor.disabled'))
      } else {
        const result = await api.post<RecoveryCodes>('/api/auth/totp/recovery', { password })
        setAccount(result.account)
        setCodes(result.recovery_codes)
      }
      closeAll()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const codesText = (codes ?? []).join('\n')

  return (
    <Section title={t('twofactor.title')} intro={oidcOnly ? t('twofactor.oidcOnly', { provider }) : t('twofactor.lead')}>
      {account.second_factor_setup_required && <Banner tone="warn">{t('twofactor.requiredBanner')}</Banner>}
      {!oidcOnly && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-mist-300">{account.two_factor ? t('twofactor.on', { count: account.two_factor_recovery_left }) : t('twofactor.off')}</p>
          {account.two_factor ? (
            <>
              <Button variant="ghost" size="sm" onClick={() => setAsking('renew')}>
                {t('twofactor.newCodes')}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setAsking('disable')}>
                {t('twofactor.disable')}
              </Button>
            </>
          ) : (
            <Button size="sm" loading={busy && enrolment === null} onClick={() => void begin()}>
              <Symbol name="shield" className="h-3.5 w-3.5" />
              {t('twofactor.enable')}
            </Button>
          )}
        </div>
      )}
      {account.two_factor && account.two_factor_recovery_left < LOW_CODES && <Banner tone="warn">{t('twofactor.lowCodes')}</Banner>}

      <Dialog
        open={enrolment !== null}
        title={t('twofactor.setupTitle')}
        onClose={closeAll}
        footer={
          <>
            <Button variant="ghost" onClick={closeAll}>
              {t('common.cancel')}
            </Button>
            <Button loading={busy} disabled={code.trim().length !== 6 || !password} onClick={() => void confirm()}>
              {t('twofactor.confirm')}
            </Button>
          </>
        }
      >
        {enrolment && (
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault()
              if (code.trim().length === 6 && password) void confirm()
            }}
          >
            <p className="text-sm text-mist-300">{t('twofactor.setupScan')}</p>
            <div className="flex justify-center rounded-2xl border border-ink-700 bg-ink-900 p-4">
              <img src={'data:image/svg+xml;utf8,' + encodeURIComponent(enrolment.qr_svg)} alt="" width={196} height={196} />
            </div>
            <div className="flex flex-col gap-1">
              <p className="text-xs font-medium text-mist-500">{t('twofactor.setupSecret')}</p>
              <div className="flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2">
                <code className="min-w-0 flex-1 font-mono text-xs break-all text-mist-200">{enrolment.secret.replace(/(.{4})/g, '$1 ').trim()}</code>
                <button
                  type="button"
                  onClick={() => void writeClipboard(enrolment.secret).then((ok) => notify(ok ? t('vault.copied') : t('clipboard.copyFailed')))}
                  className="rounded-full p-1 text-mist-500 hover:text-mist-100"
                  aria-label={t('vault.copied')}
                >
                  <Symbol name="copy" className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            <Field label={t('twofactor.setupCode')} inputMode="numeric" autoComplete="one-time-code" spellCheck={false} value={code} onChange={(event) => setCode(event.target.value)} autoFocus />
            <Field label={t('twofactor.setupPassword')} type="password" autoComplete="current-password" hint={t('twofactor.setupPasswordHint')} value={password} onChange={(event) => setPassword(event.target.value)} />
            {error && <Banner tone="bad">{error}</Banner>}
          </form>
        )}
      </Dialog>

      <Dialog
        open={asking !== null}
        title={asking === 'disable' ? t('twofactor.disableTitle') : t('twofactor.newCodesTitle')}
        onClose={closeAll}
        footer={
          <>
            <Button variant="ghost" onClick={closeAll}>
              {t('common.cancel')}
            </Button>
            <Button variant={asking === 'disable' ? 'danger' : 'primary'} loading={busy} disabled={!password} onClick={() => void withPassword()}>
              {asking === 'disable' ? t('twofactor.disable') : t('twofactor.newCodes')}
            </Button>
          </>
        }
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (password) void withPassword()
          }}
        >
          <p className="text-sm text-mist-300">{asking === 'disable' ? t('twofactor.disableText') : t('twofactor.newCodesText')}</p>
          <Field label={t('twofactor.setupPassword')} type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus />
          {error && <Banner tone="bad">{error}</Banner>}
        </form>
      </Dialog>

      <Dialog open={codes !== null} title={t('twofactor.codesTitle')} onClose={() => setCodes(null)} footer={<Button onClick={() => setCodes(null)}>{t('twofactor.codesDone')}</Button>}>
        <p className="text-sm text-mist-300">{t('twofactor.codesLead')}</p>
        <ol className="grid grid-cols-2 gap-2 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 font-mono text-sm text-mist-100">
          {(codes ?? []).map((entry) => (
            <li key={entry}>{entry}</li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" size="sm" onClick={() => void writeClipboard(codesText).then((ok) => notify(ok ? t('vault.copied') : t('clipboard.copyFailed')))}>
            <Symbol name="copy" className="h-3.5 w-3.5" />
            {t('twofactor.codesCopy')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => saveAsFile(`nextrmnl-recovery-codes-${account.name}.txt`, codesText + '\n')}>
            <Symbol name="download" className="h-3.5 w-3.5" />
            {t('twofactor.codesDownload')}
          </Button>
        </div>
      </Dialog>
    </Section>
  )
}

/**
 * My account, sign-in providers (shared sign-in blueprint, part 04): one line per active provider with its state and
 * "Link" or "Unlink". Linking asks the own password once more, then the browser goes to the provider and comes back
 * to `/account?linked=<slug>` (the account tab), or with `?error=<code>`.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { api, errorMessage } from '../../api/client'
import type { Account, MyProvider } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { useNotice } from '../../components/Notice'
import { Symbol } from '../../components/Symbol'
import { Badge, Banner, Button, Field, Section } from '../../components/ui'
import { oidcErrorKey } from '../../vendor/nexoidc/oidc'

export function AccountProviders({
  account,
  providers,
  reload,
  refresh,
}: {
  account: Account
  providers: MyProvider[]
  reload: () => Promise<void>
  refresh: () => Promise<void>
}) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [params, setParams] = useSearchParams()
  // Only codes of the shared sign-in module come back through the address, never shown raw.
  const [error, setError] = useState<string | null>(() => {
    const code = params.get('error')
    const key = oidcErrorKey(code)
    return key ? t(key) : code ? t('errors.generic') : null
  })
  const [linkedSlug] = useState(params.get('linked'))
  const [linking, setLinking] = useState<MyProvider | null>(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const oidcOnly = account.sign_in === 'oidc'
  const linked = providers.filter((entry) => entry.linked)
  // The provider's name after linking, never its short name.
  const linkedNow = providers.find((entry) => entry.slug === linkedSlug)

  useEffect(() => {
    // After the way back from a provider: fetch the account state fresh and remove the marker from the address.
    if (!params.get('linked') && !params.get('error')) return
    void refresh()
    setParams({ tab: 'account' }, { replace: true })
  }, [params, refresh, setParams])

  async function unlink(entry: MyProvider) {
    setBusy(true)
    setError(null)
    try {
      await api.delete(`/api/oidc/${encodeURIComponent(entry.slug)}/link`)
      await Promise.all([refresh(), reload()])
      notify(t('account.unlinked'))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function startLink() {
    if (!linking) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<{ url: string }>(`/api/oidc/${encodeURIComponent(linking.slug)}/link`, { password })
      setPassword('')
      window.location.assign(result.url)
    } catch (caught) {
      setError(errorMessage(caught))
      setBusy(false)
    }
  }

  async function seen() {
    try {
      await api.delete('/api/auth/oidc-notice')
      await refresh()
    } catch (caught) {
      notify(errorMessage(caught))
    }
  }

  function closeDialog() {
    setLinking(null)
    setPassword('')
    setError(null)
  }

  if (providers.length === 0 && account.oidc_unlinked.length === 0) return null

  return (
    <Section title={t('oidc.account.title')} intro={oidcOnly ? undefined : t('oidc.account.text')}>
      {account.oidc_unlinked.length > 0 && (
        <Banner tone="warn">
          <span className="flex flex-wrap items-center gap-3">
            {t('account.unlinkedByOperator', { names: account.oidc_unlinked.join(', ') })}
            <Button variant="ghost" size="sm" onClick={() => void seen()}>
              {t('account.seen')}
            </Button>
          </span>
        </Banner>
      )}
      {error && !linking && <Banner tone="bad">{error}</Banner>}
      {linkedNow && <Banner tone="ok">{t('account.linkedNow', { name: linkedNow.label })}</Banner>}
      {oidcOnly && linked.length === 1 && <p className="text-sm text-mist-400">{t('oidc.account.only', { name: linked[0].label })}</p>}
      <ul className="flex flex-col divide-y divide-ink-700" data-testid="my-providers">
        {providers.map((entry) => (
          <li key={entry.slug} className="flex flex-wrap items-center gap-3 py-2.5">
            <span className="min-w-0 flex-1 text-sm font-medium text-mist-100">{entry.label}</span>
            <Badge tone={entry.linked ? 'ok' : 'neutral'}>{entry.linked ? t('oidc.account.linked') : t('oidc.account.notLinked')}</Badge>
            {entry.linked ? (
              // An account without a password keeps its last link (the server says so too).
              <Button variant="ghost" size="sm" loading={busy} disabled={oidcOnly && linked.length <= 1} onClick={() => void unlink(entry)}>
                {t('oidc.account.unlink')}
              </Button>
            ) : (
              !oidcOnly && (
                <Button size="sm" onClick={() => setLinking(entry)}>
                  <Symbol name="shield" className="h-3.5 w-3.5" />
                  {t('oidc.account.link')}
                </Button>
              )
            )}
          </li>
        ))}
      </ul>
      <Dialog
        open={linking !== null}
        title={linking ? `${t('oidc.account.link')}: ${linking.label}` : ''}
        onClose={closeDialog}
        footer={
          <>
            <Button variant="ghost" onClick={closeDialog}>
              {t('common.cancel')}
            </Button>
            <Button loading={busy} disabled={!password} onClick={() => void startLink()}>
              {t('oidc.account.link')}
            </Button>
          </>
        }
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (password) void startLink()
          }}
        >
          <p className="text-sm text-mist-300">{t('account.linkPasswordLead', { provider: linking?.label ?? '' })}</p>
          <Field label={t('account.linkPassword')} type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus />
          {error && <Banner tone="bad">{error}</Banner>}
        </form>
      </Dialog>
    </Section>
  )
}

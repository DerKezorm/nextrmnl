import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { api, downloadFile, errorMessage, errorText } from '../api/client'
import type { Account, AuthentikResult, AuthentikStepKey, InviteInfo, OidcConfig, OidcState, Settings, TargetsMode } from '../api/types'
import { useAuth } from '../auth'
import { Dialog } from '../components/Dialog'
import { useNotice } from '../components/Notice'
import { Symbol } from '../components/Symbol'
import { TabRow } from '../components/TabRow'
import { Badge, Banner, Button, Field, PageHeader, PageLoading, Section, SelectField, Spinner, Switch } from '../components/ui'
import { writeClipboard } from '../lib/clipboard'
import { formatDateTime, formatRelative } from '../lib/format'
import { MIN_PASSWORD } from '../lib/rules'
import { PREFS_EVENT, setTerminalPref, terminalPrefs, type TerminalPrefs } from '../lib/terminalPrefs'
import { useLoad } from '../lib/useLoad'
import { BackupTab } from './settings/BackupTab'
import { LogTab } from './settings/LogTab'

const TABS = ['terminal', 'account', 'accounts', 'signin', 'security', 'backup', 'logs'] as const
type Tab = (typeof TABS)[number]
const OPERATOR_TABS: readonly Tab[] = ['accounts', 'signin', 'security', 'backup', 'logs']

function isTab(value: string | null): value is Tab {
  return (TABS as readonly string[]).includes(value ?? '')
}

function TerminalSettings() {
  const { t } = useTranslation()
  const [prefs, setPrefs] = useState(terminalPrefs())

  useEffect(() => {
    const refresh = () => setPrefs(terminalPrefs())
    window.addEventListener(PREFS_EVENT, refresh)
    return () => window.removeEventListener(PREFS_EVENT, refresh)
  }, [])

  // First in the browser, then on the account: another device fetches it from there.
  const change = <K extends keyof TerminalPrefs>(key: K, value: TerminalPrefs[K]) => {
    const next = setTerminalPref(key, value)
    void api.put('/api/me/prefs', { prefs: next }).catch(() => undefined)
  }

  const shortcuts: [string, string][] = [
    [t('settings.keysCopy'), t('settings.keysCopyWhich')],
    [t('settings.keysPaste'), t('settings.keysPasteWhich')],
    [t('settings.keysWord'), t('settings.keysWordWhich')],
    [t('settings.keysColumn'), t('settings.keysColumnWhich')],
    [t('settings.keysInterrupt'), t('settings.keysInterruptWhich')],
  ]

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[1fr_22rem]">
      <div className="flex min-w-0 flex-col gap-6">
        <Section title={t('settings.terminalTitle')} intro={t('settings.terminalLead')}>
          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField label={t('settings.fontSize')} value={String(prefs.fontSize)} onChange={(value) => change('fontSize', Number(value))}>
              {['12', '13', '14', '15', '16', '18'].map((value) => (
                <option key={value} value={value}>
                  {value} px
                </option>
              ))}
            </SelectField>
            <SelectField label={t('settings.cursor')} value={prefs.cursorStyle} onChange={(value) => change('cursorStyle', value as TerminalPrefs['cursorStyle'])}>
              <option value="block">{t('settings.cursorBlock')}</option>
              <option value="bar">{t('settings.cursorBar')}</option>
              <option value="underline">{t('settings.cursorUnderline')}</option>
            </SelectField>
            <SelectField label={t('settings.scrollback')} value={String(prefs.scrollback)} onChange={(value) => change('scrollback', Number(value))}>
              <option value="1000">1.000</option>
              <option value="5000">5.000</option>
              <option value="20000">20.000</option>
            </SelectField>
            <SelectField label={t('settings.bell')} value={prefs.bell} onChange={(value) => change('bell', value as TerminalPrefs['bell'])}>
              <option value="off">{t('settings.bellOff')}</option>
              <option value="flash">{t('settings.bellFlash')}</option>
              <option value="sound">{t('settings.bellSound')}</option>
            </SelectField>
          </div>
          <div className="flex flex-col gap-4 border-t border-ink-700 pt-4">
            <Switch label={t('settings.blink')} checked={prefs.cursorBlink} onChange={(value) => change('cursorBlink', value)} />
          </div>
        </Section>

        <Section title={t('settings.clipboardTitle')} intro={t('settings.clipboardLead')}>
          <Switch label={t('settings.copyOnSelect')} hint={t('settings.copyOnSelectHint')} checked={prefs.copyOnSelect} onChange={(value) => change('copyOnSelect', value)} />
          <Switch label={t('settings.rightClick')} hint={t('settings.rightClickHint')} checked={prefs.rightClick} onChange={(value) => change('rightClick', value)} />
          <Switch label={t('settings.confirmMultiline')} hint={t('settings.confirmMultilineHint')} checked={prefs.confirmMultiline} onChange={(value) => change('confirmMultiline', value)} />
          <dl className="grid gap-x-6 gap-y-2 border-t border-ink-700 pt-4 text-sm sm:grid-cols-[10rem_1fr]">
            {shortcuts.map(([what, keys]) => (
              <div key={what} className="contents">
                <dt className="text-mist-400">{what}</dt>
                <dd className="font-mono text-xs leading-6 text-mist-200">{keys}</dd>
              </div>
            ))}
          </dl>
          <Banner>{t('settings.clipboardHttps')}</Banner>
        </Section>
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-xs font-semibold tracking-wide text-mist-500 uppercase">{t('settings.preview')}</p>
        <div className="overflow-hidden rounded-2xl border border-ink-700 bg-term-bg p-4 font-mono leading-snug whitespace-pre text-term-fg" style={{ fontSize: `${prefs.fontSize}px` }}>
          <p>
            <span className="font-bold text-ok-500">admin@web-01</span>:<span className="font-bold text-accent-400">~</span>$ docker ps
          </p>
          <p className="text-mist-500">NAMES   STATUS</p>
          <p>web     Up 41 days</p>
          <p>db      Up 41 days</p>
          <p>
            <span className="font-bold text-ok-500">admin@web-01</span>:<span className="font-bold text-accent-400">~</span>${' '}
            <span className={'inline-block bg-accent-500 align-middle ' + (prefs.cursorStyle === 'bar' ? 'h-[1.1em] w-[2px]' : prefs.cursorStyle === 'underline' ? 'h-[2px] w-[0.6em]' : 'h-[1.1em] w-[0.6em]') + (prefs.cursorBlink ? ' animate-pulse' : '')} />
          </p>
        </div>
        <p className="text-xs text-mist-500">{t('settings.previewNote')}</p>
      </div>
    </div>
  )
}

/** The own account: sign-in, link to the OIDC provider, password. For every role. */
function AccountTab() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { account, refresh } = useAuth()
  const [params, setParams] = useSearchParams()
  const oidc = useLoad(() => api.get<OidcState>('/api/oidc/state').catch(() => ({ enabled: false, provider_name: '' })))
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(params.get('error') ? errorText(params.get('error')) || t('errors.generic') : null)
  const provider = oidc.data?.provider_name || 'OIDC'
  const linkedNow = params.get('linked') === '1'
  const valid = current.length > 0 && next.length >= MIN_PASSWORD && repeat === next

  useEffect(() => {
    // After the way back from the provider: fetch the account state fresh and remove the marker from the address.
    if (!linkedNow && !params.get('error')) return
    void refresh()
    setParams({ tab: 'account' }, { replace: true })
  }, [linkedNow, params, refresh, setParams])

  async function unlink() {
    setBusy(true)
    try {
      await api.delete('/api/oidc/link')
      await refresh()
      notify(t('account.unlinked'))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function changePassword() {
    setBusy(true)
    setError(null)
    try {
      await api.put('/api/auth/password', { current, new: next })
      setCurrent('')
      setNext('')
      setRepeat('')
      notify(t('account.passwordChanged'))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  if (!account) return null
  const oidcOnly = account.sign_in === 'oidc'

  return (
    <div className="flex flex-col gap-6">
      <Section title={t('account.title')} intro={t('account.lead')}>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-mist-600">{t('auth.name')}</dt>
          <dd className="text-mist-100">{account.name}</dd>
          <dt className="text-mist-600">{t('settings.inviteRole')}</dt>
          <dd>
            <Badge tone={account.role === 'operator' ? 'accent' : 'neutral'}>{t(`settings.role.${account.role}`)}</Badge>
          </dd>
          <dt className="text-mist-600">{t('settings.tabSignin')}</dt>
          <dd className="text-mist-100">{oidcOnly ? t('account.signInOidc', { provider }) : t('account.signInPassword')}</dd>
        </dl>
        {error && <Banner tone="bad">{error}</Banner>}
        {linkedNow && <Banner tone="ok">{t('account.linkedNow')}</Banner>}
      </Section>

      {oidc.data?.enabled && !oidcOnly && (
        <Section title={t('account.linkTitle', { provider })} intro={t('account.linkLead', { provider })}>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-mist-300">{account.oidc_linked ? t('account.linked', { provider }) : t('account.notLinked')}</p>
            {account.oidc_linked ? (
              <Button variant="ghost" size="sm" loading={busy} onClick={() => void unlink()}>
                {t('account.unlink')}
              </Button>
            ) : (
              <a href="/api/oidc/start?link=1" className="inline-flex items-center gap-2 rounded-full bg-accent-500 px-3.5 py-1.5 text-xs font-semibold text-on-accent hover:bg-accent-400">
                <Symbol name="shield" className="h-3.5 w-3.5" />
                {t('account.link', { provider })}
              </a>
            )}
          </div>
        </Section>
      )}

      <Section title={t('account.passwordTitle')} intro={oidcOnly ? t('account.oidcOnly', { provider }) : t('account.passwordLead')}>
        {!oidcOnly && (
          <form
            className="grid gap-4 sm:grid-cols-3"
            onSubmit={(event) => {
              event.preventDefault()
              if (valid) void changePassword()
            }}
          >
            <Field label={t('account.passwordCurrent')} type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} />
            <Field
              label={t('account.passwordNew')}
              type="password"
              autoComplete="new-password"
              value={next}
              error={next.length > 0 && next.length < MIN_PASSWORD ? t('auth.passwordTooShort', { min: MIN_PASSWORD }) : null}
              onChange={(event) => setNext(event.target.value)}
            />
            <Field label={t('auth.passwordRepeat')} type="password" autoComplete="new-password" value={repeat} error={repeat.length > 0 && repeat !== next ? t('auth.passwordMismatch') : null} onChange={(event) => setRepeat(event.target.value)} />
            <div className="sm:col-span-3">
              <Button type="submit" disabled={!valid} loading={busy}>
                {t('common.save')}
              </Button>
            </div>
          </form>
        )}
      </Section>
    </div>
  )
}

function AccountSettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { account: me } = useAuth()
  const accounts = useLoad(() => api.get<Account[]>('/api/accounts'))
  const invites = useLoad(() => api.get<InviteInfo[]>('/api/accounts/invites'))
  const [inviting, setInviting] = useState(false)
  const [inviteName, setInviteName] = useState('')
  const [inviteRole, setInviteRole] = useState('member')
  const [link, setLink] = useState<InviteInfo | null>(null)
  const [removing, setRemoving] = useState<Account | null>(null)
  const [busy, setBusy] = useState(false)

  async function invite() {
    setBusy(true)
    try {
      setLink(await api.post<InviteInfo>('/api/accounts/invites', { name: inviteName.trim(), role: inviteRole }))
      setInviting(false)
      setInviteName('')
      void invites.reload()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function withdraw(id: number) {
    try {
      await api.delete(`/api/accounts/invites/${id}`)
      void invites.reload()
    } catch (error) {
      notify(errorMessage(error))
    }
  }

  async function setRole(account: Account, role: string) {
    try {
      await api.put(`/api/accounts/${account.id}/role`, { role })
      void accounts.reload()
    } catch (error) {
      notify(errorMessage(error))
    }
  }

  async function remove() {
    if (!removing) return
    setBusy(true)
    try {
      await api.delete(`/api/accounts/${removing.id}`)
      setRemoving(null)
      void accounts.reload()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Section
        title={t('settings.accountsTitle')}
        intro={t('settings.accountsLead')}
        aside={
          <Button size="sm" onClick={() => setInviting(true)}>
            <Symbol name="plus" className="h-3.5 w-3.5" />
            {t('settings.invite')}
          </Button>
        }
      >
        {accounts.error && <Banner tone="bad">{accounts.error}</Banner>}
        <ul className="flex flex-col divide-y divide-ink-700">
          {(accounts.data ?? []).map((account) => {
            const isMe = account.id === me?.id
            return (
              <li key={account.id} className="flex flex-wrap items-center gap-3 py-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-ink-800 text-sm font-semibold text-mist-300 uppercase">{account.name.slice(0, 1)}</span>
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-mist-100">
                    {account.name}
                    {isMe && <span className="text-xs text-mist-500">({t('settings.you')})</span>}
                    <Badge tone={account.role === 'operator' ? 'accent' : 'neutral'}>{t(`settings.role.${account.role}`)}</Badge>
                  </p>
                  <p className="text-xs text-mist-500">
                    {account.sign_in === 'oidc' ? t('settings.viaOidc') : t('settings.viaPassword')} · {account.last_seen_at ? t('settings.lastSeen', { when: formatRelative(account.last_seen_at) }) : t('settings.neverSeen')}
                  </p>
                </div>
                {!isMe && (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => void setRole(account, account.role === 'operator' ? 'member' : 'operator')}>
                      {account.role === 'operator' ? t('settings.makeMember') : t('settings.makeOperator')}
                    </Button>
                    <button type="button" onClick={() => setRemoving(account)} className="rounded-full p-1.5 text-mist-500 hover:bg-bad-500/10 hover:text-bad-500" aria-label={t('settings.deleteAccount', { name: account.name })}>
                      <Symbol name="trash" />
                    </button>
                  </>
                )}
              </li>
            )
          })}
        </ul>
        {(invites.data?.length ?? 0) > 0 && (
          <div className="border-t border-ink-700 pt-4">
            <p className="mb-2 text-xs font-semibold tracking-wide text-mist-500 uppercase">{t('settings.openInvites')}</p>
            <ul className="flex flex-col gap-1">
              {(invites.data ?? []).map((entry) => (
                <li key={entry.id} className="flex items-center gap-3 text-sm">
                  <span className="text-mist-200">{entry.name || t('settings.inviteNoName')}</span>
                  <Badge>{t(`settings.role.${entry.role}`)}</Badge>
                  <span className="text-xs text-mist-500">{t('settings.inviteExpires', { when: formatDateTime(entry.expires_at) })}</span>
                  <button type="button" onClick={() => void withdraw(entry.id)} className="ml-auto text-xs text-mist-500 hover:text-bad-500">
                    {t('settings.inviteWithdraw')}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <Banner>{t('settings.operatorNote')}</Banner>
      </Section>

      <Dialog
        open={inviting}
        title={t('settings.invite')}
        onClose={() => setInviting(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setInviting(false)}>
              {t('common.cancel')}
            </Button>
            <Button loading={busy} onClick={() => void invite()}>
              {t('settings.inviteCreate')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-500">{t('settings.inviteLead')}</p>
        <Field label={t('auth.name')} value={inviteName} onChange={(event) => setInviteName(event.target.value)} placeholder="alex" hint={t('settings.inviteNameHint')} spellCheck={false} autoFocus />
        <SelectField label={t('settings.inviteRole')} value={inviteRole} onChange={setInviteRole}>
          <option value="member">{t('settings.role.member')}</option>
          <option value="operator">{t('settings.role.operator')}</option>
        </SelectField>
      </Dialog>

      <Dialog open={link !== null} title={t('settings.inviteLinkTitle')} onClose={() => setLink(null)} footer={<Button onClick={() => setLink(null)}>{t('common.done')}</Button>}>
        <p className="text-sm text-mist-300">{t('settings.inviteLinkLead')}</p>
        <div className="flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2">
          <code className="min-w-0 flex-1 truncate font-mono text-xs text-mist-200">{link?.link}</code>
          <button
            type="button"
            onClick={() => void writeClipboard(link?.link ?? '').then((ok) => notify(ok ? t('vault.copied') : t('clipboard.copyFailed')))}
            className="rounded-full p-1 text-mist-500 hover:text-mist-100"
            aria-label={t('settings.inviteCopy')}
          >
            <Symbol name="copy" className="h-3.5 w-3.5" />
          </button>
        </div>
        <Banner tone="warn">{t('settings.inviteLinkWarning')}</Banner>
      </Dialog>

      <Dialog
        open={removing !== null}
        title={t('settings.deleteAccount', { name: removing?.name ?? '' })}
        onClose={() => setRemoving(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void remove()}>
              {t('settings.deleteNow')}
            </Button>
          </>
        }
      >
        <Banner tone="warn">{t('settings.deleteAccountText')}</Banner>
      </Dialog>
    </div>
  )
}

const AUTHENTIK_STEPS: AuthentikStepKey[] = ['reached', 'signingKey', 'mapping', 'provider', 'application', 'filled']

/** The public address: invitation links, the OIDC return address and the WebSocket origin check hang on it. */
function PublicAddress({ value, configured, onSave }: { value: string; configured: boolean; onSave: (url: string) => Promise<void> }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(value)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await onSave(draft.trim())
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('signin.publicUrlTitle')} intro={t('signin.publicUrlHint')}>
      <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-start">
        <Field label={t('signin.publicUrl')} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="https://nextrmnl.example.com" spellCheck={false} error={error} />
        <Button className="sm:mt-6" loading={busy} disabled={draft.trim() === value} onClick={() => void save()}>
          {t('common.save')}
        </Button>
      </div>
      {configured && <Banner tone="warn">{t('signin.publicUrlNote')}</Banner>}
    </Section>
  )
}

function SignInSettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const config = useLoad(() => api.get<OidcConfig>('/api/oidc/config'))
  const settings = useLoad(() => api.get<Settings>('/api/settings'))
  // null until the person types: then the address of an authentik that is already set up is offered,
  // read from the issuer, so that running the setup again only needs a fresh token.
  const [url, setUrl] = useState<string | null>(null)
  const authentikBase = config.data?.provider_name === 'authentik' ? config.data.issuer.replace(/\/application\/o\/[^/]+\/?$/, '') : ''
  const urlValue = url ?? authentikBase
  const [token, setToken] = useState('')
  const [result, setResult] = useState<AuthentikResult | null>(null)
  const [running, setRunning] = useState(false)
  const [manual, setManual] = useState(false)
  const [issuer, setIssuer] = useState('')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [providerName, setProviderName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const redirect = config.data?.redirect_uri ?? ''

  async function setUp() {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      setResult(await api.post<AuthentikResult>('/api/oidc/authentik/setup', { url: urlValue.trim(), token }))
      setToken('')
      void config.reload()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setRunning(false)
    }
  }

  async function saveManual() {
    setBusy(true)
    setError(null)
    try {
      await api.put('/api/oidc/config', { issuer: issuer.trim(), client_id: clientId.trim(), client_secret: clientSecret, provider_name: providerName.trim() })
      setClientSecret('')
      setManual(false)
      void config.reload()
      notify(t('signin.saved'))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function removeOidc() {
    try {
      await api.delete('/api/oidc/config')
      void config.reload()
    } catch (caught) {
      notify(errorMessage(caught))
    }
  }

  async function saveRule(values: Partial<Settings>) {
    try {
      settings.set(await api.put<Settings>('/api/settings', values))
    } catch (caught) {
      notify(errorMessage(caught))
    }
  }

  // Errors stay in the field; the return address shown below follows the new value.
  async function savePublicUrl(publicUrl: string) {
    settings.set(await api.put<Settings>('/api/settings', { public_url: publicUrl }))
    void config.reload()
    notify(t('signin.publicUrlSaved'))
  }

  const configured = config.data?.configured ?? false

  return (
    <div className="flex flex-col gap-6">
      {settings.data && <PublicAddress key={settings.data.public_url} value={settings.data.public_url} configured={configured} onSave={savePublicUrl} />}
      {configured && (
        <Section
          title={t('signin.configuredTitle')}
          aside={
            <Button variant="ghost" size="sm" onClick={() => void removeOidc()}>
              {t('signin.remove')}
            </Button>
          }
        >
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-mist-600">{t('signin.provider')}</dt>
            <dd className="text-mist-100">{config.data?.provider_name || 'OIDC'}</dd>
            <dt className="text-mist-600">{t('signin.issuer')}</dt>
            <dd className="font-mono text-xs text-mist-200 break-all">{config.data?.issuer}</dd>
            <dt className="text-mist-600">{t('signin.clientId')}</dt>
            <dd className="font-mono text-xs text-mist-200 break-all">{config.data?.client_id}</dd>
          </dl>
        </Section>
      )}

      <Section title={t('signin.authentikTitle')} intro={t('signin.authentikLead')} aside={<Badge tone="accent">{t('signin.recommended')}</Badge>}>
        {result === null && !running ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t('signin.authentikUrl')} value={urlValue} onChange={(event) => setUrl(event.target.value)} placeholder="https://auth.example.com" spellCheck={false} />
              <Field label={t('signin.token')} type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" hint={t('signin.tokenHint')} />
            </div>
            {error && <Banner tone="bad">{error}</Banner>}
            <div className="flex flex-wrap items-center gap-3">
              <Button disabled={!token.trim() || !urlValue.trim()} onClick={() => void setUp()}>
                <Symbol name="check" />
                {t('signin.setUp')}
              </Button>
              <Button variant="link" onClick={() => void downloadFile('/api/oidc/authentik/blueprint', 'nextrmnl-authentik.yaml').catch((caught) => notify(errorMessage(caught)))}>
                <Symbol name="download" className="h-3.5 w-3.5" />
                {t('signin.blueprint')}
              </Button>
            </div>
          </>
        ) : (
          <ol className="flex flex-col gap-2.5">
            {AUTHENTIK_STEPS.map((step) => {
              const done = result?.steps.find((entry) => entry.key === step)
              const state = done ? (done.ok ? 'done' : 'failed') : running && !result ? 'running' : 'waiting'
              return (
                <li key={step} className="flex items-start gap-3 text-sm">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
                    {state === 'done' && <Symbol name="check" className="h-4 w-4 text-ok-500" />}
                    {state === 'failed' && <Symbol name="warn" className="h-4 w-4 text-bad-500" />}
                    {state === 'running' && <Spinner className="h-4 w-4 text-accent-400" />}
                    {state === 'waiting' && <span className="h-1.5 w-1.5 rounded-full bg-ink-600" />}
                  </span>
                  <span className={state === 'waiting' ? 'text-mist-600' : 'text-mist-200'}>
                    {t(`signin.step.${step}`, { redirect })}
                    {done?.detail && <span className="block text-xs text-mist-500">{done.detail}</span>}
                  </span>
                </li>
              )
            })}
          </ol>
        )}
        {result && result.steps.every((step) => step.ok) && <Banner tone="ok">{t('signin.doneText')}</Banner>}
        {result && result.steps.some((step) => !step.ok) && <Banner tone="bad">{t('signin.failedText')}</Banner>}
        {result && (
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => setResult(null)}>
              {t('signin.again')}
            </Button>
          </div>
        )}
      </Section>

      <Section
        title={t('signin.manualTitle')}
        intro={t('signin.manualLead')}
        aside={
          <Button variant="ghost" size="sm" onClick={() => setManual(!manual)} aria-expanded={manual}>
            {manual ? t('signin.hide') : t('signin.show')}
          </Button>
        }
      >
        {manual && (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t('signin.issuer')} value={issuer} onChange={(event) => setIssuer(event.target.value)} placeholder="https://id.example.com/realms/home" spellCheck={false} />
              <Field label={t('signin.provider')} value={providerName} onChange={(event) => setProviderName(event.target.value)} placeholder="Keycloak" />
              <Field label={t('signin.clientId')} value={clientId} onChange={(event) => setClientId(event.target.value)} placeholder="nextrmnl" spellCheck={false} />
              <Field label={t('signin.clientSecret')} type="password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} autoComplete="off" />
              <div className="flex flex-col gap-1.5 sm:col-span-2">
                <p className="text-sm font-medium text-mist-300">{t('signin.redirect')}</p>
                <div className="flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2">
                  <code className="min-w-0 flex-1 truncate font-mono text-xs text-mist-200">{redirect}</code>
                  <button type="button" onClick={() => void writeClipboard(redirect).then((ok) => notify(ok ? t('vault.copied') : t('clipboard.copyFailed')))} className="rounded-full p-1 text-mist-500 hover:text-mist-100" aria-label={t('signin.copyRedirect')}>
                    <Symbol name="copy" className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
            {error && <Banner tone="bad">{error}</Banner>}
            <div>
              <Button disabled={!issuer.trim() || !clientId.trim() || !clientSecret} loading={busy} onClick={() => void saveManual()}>
                {t('common.save')}
              </Button>
            </div>
          </>
        )}
      </Section>

      <Section title={t('signin.rulesTitle')}>
        {settings.data && (
          <>
            <Switch label={t('signin.autoCreate')} hint={t('signin.autoCreateHint')} checked={settings.data.oidc_auto_create} disabled={!configured} onChange={(value) => void saveRule({ oidc_auto_create: value })} />
            <Switch label={t('signin.passwordLogin')} hint={t('signin.passwordLoginHint')} checked={settings.data.password_login} disabled={!configured} onChange={(value) => void saveRule({ password_login: value })} />
          </>
        )}
        <Banner>{t('signin.vaultNote')}</Banner>
      </Section>
    </div>
  )
}

function SecuritySettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const settings = useLoad(() => api.get<Settings>('/api/settings'))
  const [error, setError] = useState<string | null>(null)

  async function save(values: Partial<Settings>) {
    setError(null)
    try {
      settings.set(await api.put<Settings>('/api/settings', values))
    } catch (caught) {
      setError(errorMessage(caught))
      notify(errorMessage(caught))
    }
  }

  const data = settings.data
  if (!data) return settings.error ? <Banner tone="bad">{settings.error}</Banner> : <PageLoading />

  const options: { value: TargetsMode; label: string; hint: string }[] = [
    { value: 'private', label: t('security.targetsPrivate'), hint: t('security.targetsPrivateHint') },
    { value: 'list', label: t('security.targetsList'), hint: t('security.targetsListHint') },
    { value: 'all', label: t('security.targetsAll'), hint: t('security.targetsAllHint') },
  ]

  return (
    <div className="flex flex-col gap-6">
      <Section title={t('security.targetsTitle')} intro={t('security.targetsLead')}>
        <div className="flex flex-col gap-2" role="radiogroup" aria-label={t('security.targetsTitle')}>
          {options.map((option) => (
            <label
              key={option.value}
              className={'flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ' + (data.targets_mode === option.value ? 'border-accent-500/60 bg-accent-500/8' : 'border-ink-700 hover:bg-ink-800')}
            >
              <input type="radio" name="targets" className="mt-1 accent-accent-500" checked={data.targets_mode === option.value} onChange={() => void save({ targets_mode: option.value })} />
              <span>
                <span className="block text-sm font-medium text-mist-100">{option.label}</span>
                <span className="block text-xs text-mist-500">{option.hint}</span>
              </span>
            </label>
          ))}
        </div>
        {data.targets_mode === 'list' && (
          <Field
            label={t('security.allowList')}
            key={data.targets_list.join(',')}
            defaultValue={data.targets_list.join(', ')}
            placeholder="192.0.2.0/24, *.example.com"
            hint={t('security.allowListHint')}
            spellCheck={false}
            onBlur={(event) => {
              const entries = event.target.value.split(',').map((entry) => entry.trim()).filter(Boolean)
              if (entries.join(',') !== data.targets_list.join(',')) void save({ targets_list: entries })
            }}
          />
        )}
        {error && <Banner tone="bad">{error}</Banner>}
        {data.targets_mode === 'all' && <Banner tone="warn">{t('security.targetsAllWarn')}</Banner>}
      </Section>

      {/* The second way out, next to the targets, which is why it is here and not on the About page. */}
      <Section title={t('security.updatesTitle')} intro={t('security.updatesLead')}>
        <Switch label={t('security.updateCheck')} hint={t('security.updateCheckHint')} checked={data.update_check} onChange={(value) => void save({ update_check: value })} />
      </Section>

      <Section title={t('security.otherTitle')}>
        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField label={t('security.lockAfter')} value={String(data.vault_lock_minutes)} onChange={(value) => void save({ vault_lock_minutes: Number(value) })}>
            <option value="15">{t('security.minutes', { count: 15 })}</option>
            <option value="30">{t('security.minutes', { count: 30 })}</option>
            <option value="60">{t('security.minutes', { count: 60 })}</option>
            <option value="240">{t('security.hours', { count: 4 })}</option>
          </SelectField>
          <SelectField label={t('security.keepHistory')} value={String(data.history_days)} onChange={(value) => void save({ history_days: Number(value) })}>
            <option value="30">{t('security.days', { count: 30 })}</option>
            <option value="90">{t('security.days', { count: 90 })}</option>
            <option value="365">{t('security.days', { count: 365 })}</option>
          </SelectField>
        </div>
        <p className="text-xs text-mist-500">{t('security.otherNote')}</p>
      </Section>
    </div>
  )
}

export function SettingsPage() {
  const { t } = useTranslation()
  const { account } = useAuth()
  // The tab is in the address (`?tab=security`), so other pages can link directly to it.
  const [params, setParams] = useSearchParams()
  const operator = account?.role === 'operator'
  const wanted = params.get('tab')
  const allowed = (value: Tab) => operator || !OPERATOR_TABS.includes(value)
  const tab: Tab = isTab(wanted) && allowed(wanted) ? wanted : 'terminal'
  const allTabs: { value: Tab; label: string }[] = [
    { value: 'terminal', label: t('settings.tabTerminal') },
    { value: 'account', label: t('settings.tabAccount') },
    { value: 'accounts', label: t('settings.tabAccounts') },
    { value: 'signin', label: t('settings.tabSignin') },
    { value: 'security', label: t('settings.tabSecurity') },
    { value: 'backup', label: t('settings.tabBackup') },
    { value: 'logs', label: t('settings.tabLogs') },
  ]
  const tabs = allTabs.filter((item) => allowed(item.value))
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t('settings.title')} />
      <TabRow tabs={tabs} active={tab} onChange={(value) => setParams(value === 'terminal' ? {} : { tab: value }, { replace: true })} label={t('settings.title')} />
      {tab === 'terminal' && <TerminalSettings />}
      {tab === 'account' && <AccountTab />}
      {tab === 'accounts' && <AccountSettings />}
      {tab === 'signin' && <SignInSettings />}
      {tab === 'security' && <SecuritySettings />}
      {tab === 'backup' && <BackupTab />}
      {tab === 'logs' && <LogTab />}
    </div>
  )
}

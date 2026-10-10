import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { api, errorMessage } from '../api/client'
import type { Account, InviteInfo, MyProvider, Settings, TargetsMode } from '../api/types'
import { useAuth } from '../auth'
import { Dialog } from '../components/Dialog'
import { useNotice } from '../components/Notice'
import { Symbol } from '../components/Symbol'
import { TabRow } from '../components/TabRow'
import { Badge, Banner, Button, Field, PageHeader, PageLoading, Section, SelectField, Switch } from '../components/ui'
import { writeClipboard } from '../lib/clipboard'
import { endOfDay, isPast, toDateInput, todayInput } from '../lib/dates'
import { formatDateTime, formatRelative } from '../lib/format'
import { MIN_PASSWORD } from '../lib/rules'
import { cleanFontName, PREFS_EVENT, setTerminalPref, terminalPrefs, type TerminalPrefs } from '../lib/terminalPrefs'
import { FIXED_SCHEMES, isScheme, SCHEME_NAMES, SCHEMES } from '../lib/terminalSchemes'
import { useLoad } from '../lib/useLoad'
import { AccountProviders } from './settings/AccountProviders'
import { ApiKeysSection } from './settings/ApiKeys'
import { BackupTab } from './settings/BackupTab'
import { LogTab } from './settings/LogTab'
import { NotificationsSection } from './settings/Notifications'
import { SecondFactorSection } from './settings/SecondFactor'
import { SignInCards } from './settings/SignInCards'

const TABS = ['terminal', 'account', 'accounts', 'signin', 'security', 'backup', 'logs'] as const
type Tab = (typeof TABS)[number]
const OPERATOR_TABS: readonly Tab[] = ['accounts', 'signin', 'security', 'backup', 'logs']

function isTab(value: string | null): value is Tab {
  return (TABS as readonly string[]).includes(value ?? '')
}

/** Common terminal fonts, Nerd Fonts among them. They only apply if installed on the device that shows the terminal. */
const FONT_PRESETS = ['', 'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Hack', 'Source Code Pro', 'MesloLGS NF', 'JetBrainsMono Nerd Font', 'FiraCode Nerd Font', 'Hack Nerd Font']
const CUSTOM_FONT = '__custom__'

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
    [t('settings.keysSearch'), t('settings.keysSearchWhich')],
  ]
  const presetFont = FONT_PRESETS.includes(prefs.fontFamily)
  const [customFont, setCustomFont] = useState(!presetFont)
  const [fontDraft, setFontDraft] = useState(presetFont ? '' : prefs.fontFamily)
  const previewBackground = isScheme(prefs.scheme) && prefs.scheme !== 'nex' ? FIXED_SCHEMES[prefs.scheme] : null

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
          <div className="grid gap-4 border-t border-ink-700 pt-4 sm:grid-cols-2">
            <SelectField label={t('settings.scheme')} value={isScheme(prefs.scheme) ? prefs.scheme : 'nex'} onChange={(value) => change('scheme', value)}>
              {SCHEMES.map((id) => (
                <option key={id} value={id}>
                  {id === 'nex' ? t('settings.schemeNex') : SCHEME_NAMES[id]}
                </option>
              ))}
            </SelectField>
            <SelectField
              label={t('settings.font')}
              value={customFont ? CUSTOM_FONT : prefs.fontFamily}
              onChange={(value) => {
                if (value === CUSTOM_FONT) {
                  setCustomFont(true)
                  return
                }
                setCustomFont(false)
                change('fontFamily', value)
              }}
            >
              <option value="">{t('settings.fontDefault')}</option>
              {FONT_PRESETS.filter(Boolean).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
              <option value={CUSTOM_FONT}>{t('settings.fontCustom')}</option>
            </SelectField>
            {customFont && (
              <div className="sm:col-span-2">
                <Field
                  label={t('settings.fontName')}
                  hint={t('settings.fontNameHint')}
                  value={fontDraft}
                  onChange={(event) => setFontDraft(event.target.value)}
                  onBlur={() => change('fontFamily', cleanFontName(fontDraft))}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') change('fontFamily', cleanFontName(fontDraft))
                  }}
                  placeholder="FiraCode Nerd Font Mono"
                />
              </div>
            )}
          </div>
          <p className="text-xs text-mist-500">{t('settings.fontNote')}</p>
          <div className="flex flex-col gap-4 border-t border-ink-700 pt-4">
            <Switch label={t('settings.blink')} checked={prefs.cursorBlink} onChange={(value) => change('cursorBlink', value)} />
            <Switch label={t('settings.warnOnClose')} hint={t('settings.warnOnCloseHint')} checked={prefs.warnOnClose} onChange={(value) => change('warnOnClose', value)} />
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
        <div
          className="overflow-hidden rounded-2xl border border-ink-700 bg-term-bg p-4 font-mono leading-snug whitespace-pre text-term-fg"
          style={{
            fontSize: `${prefs.fontSize}px`,
            ...(prefs.fontFamily ? { fontFamily: `"${cleanFontName(prefs.fontFamily)}", var(--font-mono)` } : {}),
            ...(previewBackground ? { backgroundColor: previewBackground.background, color: previewBackground.foreground } : {}),
          }}
        >
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

/** The own account: sign-in, its providers, second factor, password. For every role. */
function AccountTab() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { account, refresh } = useAuth()
  const mine = useLoad(() => api.get<MyProvider[]>('/api/oidc/me').catch(() => [] as MyProvider[]))
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const valid = current.length > 0 && next.length >= MIN_PASSWORD && repeat === next

  async function signOutEverywhere() {
    setBusy(true)
    try {
      await api.post('/api/auth/logout-all')
      notify(t('account.signedOutEverywhere'))
    } catch (caught) {
      notify(errorMessage(caught))
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
  const provider = (mine.data ?? []).filter((entry) => entry.linked).map((entry) => entry.label).join(', ') || 'OIDC'

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
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" loading={busy} onClick={() => void signOutEverywhere()}>
            {t('account.signOutEverywhere')}
          </Button>
          <p className="text-xs text-mist-500">{t('account.signOutEverywhereHint')}</p>
        </div>
      </Section>

      <AccountProviders account={account} providers={mine.data ?? []} reload={mine.reload} refresh={refresh} />

      <SecondFactorSection account={account} oidcOnly={oidcOnly} provider={provider} />

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
            {error && (
              <div className="sm:col-span-3">
                <Banner tone="bad">{error}</Banner>
              </div>
            )}
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
  const [inviteUntil, setInviteUntil] = useState('')
  const [ending, setEnding] = useState<Account | null>(null)
  const [endDay, setEndDay] = useState('')
  const [link, setLink] = useState<InviteInfo | null>(null)
  const [removing, setRemoving] = useState<Account | null>(null)
  const [resetting, setResetting] = useState<Account | null>(null)
  const [forcing, setForcing] = useState<Account | null>(null)
  const [unlinking, setUnlinking] = useState<{ account: Account; provider: { id: number; label: string } } | null>(null)
  const [unlinkPassword, setUnlinkPassword] = useState('')
  const [unlinkError, setUnlinkError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function closeUnlink() {
    setUnlinking(null)
    setUnlinkPassword('')
    setUnlinkError(null)
  }

  // The operator takes an account's link to a provider, with the own password; the account is told on its page.
  async function unlinkAccount() {
    if (!unlinking) return
    setBusy(true)
    setUnlinkError(null)
    try {
      await api.delete(`/api/oidc/admin/accounts/${unlinking.account.id}/links/${unlinking.provider.id}`, { password: unlinkPassword })
      notify(t('signin.unlinkDone', { name: unlinking.account.name, provider: unlinking.provider.label }))
      closeUnlink()
      void accounts.reload()
    } catch (error) {
      setUnlinkError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function forceSignOut() {
    if (!forcing) return
    setBusy(true)
    try {
      await api.post(`/api/accounts/${forcing.id}/sign-out`)
      notify(t('settings.forcedSignOut', { name: forcing.name }))
      setForcing(null)
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function resetSecondFactor() {
    if (!resetting) return
    setBusy(true)
    try {
      await api.post(`/api/accounts/${resetting.id}/totp/reset`)
      notify(t('twofactor.resetDone', { name: resetting.name }))
      setResetting(null)
      void accounts.reload()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function invite() {
    setBusy(true)
    try {
      setLink(
        await api.post<InviteInfo>('/api/accounts/invites', {
          name: inviteName.trim(),
          role: inviteRole,
          account_expires_at: inviteRole === 'member' ? endOfDay(inviteUntil) : null,
        }),
      )
      setInviting(false)
      setInviteName('')
      setInviteUntil('')
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

  async function saveEnd(day: string) {
    if (!ending) return
    setBusy(true)
    try {
      await api.put(`/api/accounts/${ending.id}/expiry`, { expires_at: endOfDay(day) })
      notify(day ? t('expiry.accountSet', { name: ending.name }) : t('expiry.accountCleared', { name: ending.name }))
      setEnding(null)
      void accounts.reload()
    } catch (error) {
      notify(errorMessage(error))
    } finally {
      setBusy(false)
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
                    {account.two_factor && <Badge tone="neutral">{t('twofactor.title')}</Badge>}
                    {(account.providers ?? []).map((provider) => (
                      <Badge key={provider.id} tone="accent">
                        {provider.label}
                      </Badge>
                    ))}
                    {account.expires_at &&
                      (isPast(account.expires_at) ? (
                        <Badge tone="bad">{t('expiry.ranOut')}</Badge>
                      ) : (
                        <Badge tone="warn">{t('expiry.guestUntil', { when: formatDateTime(account.expires_at) })}</Badge>
                      ))}
                  </p>
                  <p className="text-xs text-mist-500">
                    {account.sign_in === 'oidc' ? t('settings.viaOidc') : t('settings.viaPassword')} · {account.last_seen_at ? t('settings.lastSeen', { when: formatRelative(account.last_seen_at) }) : t('settings.neverSeen')}
                  </p>
                </div>
                {!isMe && (
                  <>
                    <Button variant="ghost" size="sm" onClick={() => setForcing(account)}>
                      {t('settings.forceSignOut')}
                    </Button>
                    {account.role === 'member' && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEnding(account)
                          setEndDay(toDateInput(account.expires_at))
                        }}
                      >
                        {t('expiry.button')}
                      </Button>
                    )}
                    {(account.providers ?? []).map((provider) => (
                      <Button key={provider.id} variant="ghost" size="sm" onClick={() => setUnlinking({ account, provider })}>
                        {t('signin.unlinkTitle')}: {provider.label}
                      </Button>
                    ))}
                    {account.two_factor && (
                      <Button variant="ghost" size="sm" onClick={() => setResetting(account)}>
                        {t('twofactor.reset')}
                      </Button>
                    )}
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
                  {entry.account_expires_at && <Badge tone="warn">{t('expiry.guestUntil', { when: formatDateTime(entry.account_expires_at) })}</Badge>}
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
        {inviteRole === 'member' && (
          <Field label={t('expiry.inviteUntil')} hint={t('expiry.inviteUntilHint')} type="date" min={todayInput()} value={inviteUntil} onChange={(event) => setInviteUntil(event.target.value)} />
        )}
      </Dialog>

      <Dialog
        open={ending !== null}
        title={t('expiry.accountTitle', { name: ending?.name ?? '' })}
        onClose={() => setEnding(null)}
        footer={
          <>
            {ending?.expires_at && (
              <Button variant="ghost" loading={busy} onClick={() => void saveEnd('')}>
                {t('expiry.noEnd')}
              </Button>
            )}
            <Button loading={busy} disabled={!endDay} onClick={() => void saveEnd(endDay)}>
              {t('common.save')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{t('expiry.accountLead')}</p>
        <Field label={t('expiry.until')} type="date" value={endDay} onChange={(event) => setEndDay(event.target.value)} />
        <Banner tone="warn">{t('expiry.accountWarn')}</Banner>
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

      <Dialog
        open={resetting !== null}
        title={t('twofactor.resetTitle', { name: resetting?.name ?? '' })}
        onClose={() => setResetting(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setResetting(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void resetSecondFactor()}>
              {t('twofactor.resetNow')}
            </Button>
          </>
        }
      >
        <Banner tone="warn">{t('twofactor.resetText', { name: resetting?.name ?? '' })}</Banner>
      </Dialog>

      <Dialog
        open={unlinking !== null}
        title={unlinking ? `${t('signin.unlinkTitle')}: ${unlinking.provider.label}` : ''}
        onClose={closeUnlink}
        footer={
          <>
            <Button variant="ghost" onClick={closeUnlink}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} disabled={!unlinkPassword} onClick={() => void unlinkAccount()}>
              {t('signin.unlinkTitle')}
            </Button>
          </>
        }
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (unlinkPassword) void unlinkAccount()
          }}
        >
          <p className="text-sm text-mist-300">{t('signin.unlinkLead', { name: unlinking?.account.name ?? '', provider: unlinking?.provider.label ?? '' })}</p>
          <Field label={t('signin.unlinkPassword')} type="password" autoComplete="current-password" value={unlinkPassword} onChange={(event) => setUnlinkPassword(event.target.value)} autoFocus />
          {unlinkError && <Banner tone="bad">{unlinkError}</Banner>}
        </form>
      </Dialog>

      <Dialog
        open={forcing !== null}
        title={t('settings.forceSignOutTitle', { name: forcing?.name ?? '' })}
        onClose={() => setForcing(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setForcing(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void forceSignOut()}>
              {t('settings.forceSignOut')}
            </Button>
          </>
        }
      >
        <Banner tone="warn">{t('settings.forceSignOutText', { name: forcing?.name ?? '' })}</Banner>
      </Dialog>
    </div>
  )
}

function SecuritySettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const settings = useLoad(() => api.get<Settings>('/api/settings'))
  const [error, setError] = useState<string | null>(null)

  /** True when the server took it; the caller may undo what it showed early. */
  async function save(values: Partial<Settings> & { notify_token?: string }): Promise<boolean> {
    setError(null)
    try {
      settings.set(await api.put<Settings>('/api/settings', values))
      return true
    } catch (caught) {
      setError(errorMessage(caught))
      notify(errorMessage(caught))
      return false
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

      {/* The third way out: what a dashboard may read. */}
      <ApiKeysSection allowed={data.api_keys_allowed} onToggle={(value) => void save({ api_keys_allowed: value })} />

      {/* The fourth way out: messages to the operator's inbox. */}
      <NotificationsSection data={data} onSave={save} />

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
          <SelectField label={t('security.detach')} value={String(data.detach_minutes)} onChange={(value) => void save({ detach_minutes: Number(value) })}>
            <option value="0">{t('security.detachOff')}</option>
            {[1, 5, 15, 30, 60, 120].map((minutes) => (
              <option key={minutes} value={String(minutes)}>
                {minutes >= 60 ? t('security.hours', { count: minutes / 60 }) : t('security.minutes', { count: minutes })}
              </option>
            ))}
          </SelectField>
        </div>
        <p className="text-xs text-mist-500">{t('security.detachNote')}</p>
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
      {tab === 'signin' && <SignInCards />}
      {tab === 'security' && <SecuritySettings />}
      {tab === 'backup' && <BackupTab />}
      {tab === 'logs' && <LogTab />}
    </div>
  )
}

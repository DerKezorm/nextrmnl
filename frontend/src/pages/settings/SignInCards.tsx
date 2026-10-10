/**
 * Settings, Sign-in (operator only): three cards one below the other, as the shared sign-in blueprint lays them out
 * for every nex app (part 04). Order, wording and behaviour come from there; the parts (section, switch, field,
 * button, dialog) are nextrmnl's own.
 *
 * 1. Sign-in: password sign-in (locked on while no provider is active), the public address, the second factor.
 * 2. Sign-in providers: the list with its marks, the drag handle, and the form as a dialog.
 * 3. authentik in one step: address, token, "Set up", the blueprint, and the steps with the reason of a failure.
 */
import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, api, downloadFile, errorMessage } from '../../api/client'
import type { AuthentikResult, OidcImpact, OidcProvider, OidcProviderForm, Settings } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { useNotice } from '../../components/Notice'
import { Symbol } from '../../components/Symbol'
import { Badge, Banner, Button, Field, PageLoading, Section, Spinner, Switch } from '../../components/ui'
import { writeClipboard } from '../../lib/clipboard'
import { useLoad } from '../../lib/useLoad'
import { authentikIssuerChange, authentikReasonKey, authentikStepKey, AUTHENTIK_STEPS, oidcErrorKey, oidcFormErrorKey, slugFromLabel } from '../../vendor/nexoidc/oidc'

/** The whole tab: the list is loaded once and shared, the button changes it too. */
export function SignInCards() {
  const settings = useLoad(() => api.get<Settings>('/api/settings'))
  const [providers, setProviders] = useState<OidcProvider[] | null>(null)
  const load = useCallback(async () => setProviders(await api.get<OidcProvider[]>('/api/oidc/admin/providers')), [])
  useEffect(() => {
    void load().catch(() => setProviders([]))
  }, [load])
  if (!settings.data) return settings.error ? <Banner tone="bad">{settings.error}</Banner> : <PageLoading />
  const active = (providers ?? []).some((entry) => entry.enabled)
  const managed = (providers ?? []).find((entry) => entry.managed === 'authentik')
  return (
    <div className="flex flex-col gap-6">
      <SignInCard settings={settings.data} onChange={settings.set} anyActive={active} anyEntry={(providers ?? []).length > 0} />
      <ProvidersCard providers={providers} reload={load} publicUrl={settings.data.public_url} />
      <AuthentikCard publicUrl={settings.data.public_url} managed={managed} reload={load} />
    </div>
  )
}

// --- Card 1: sign-in -------------------------------------------------------------------------------------------------

function SignInCard({
  settings,
  onChange,
  anyActive,
  anyEntry,
}: {
  settings: Settings
  onChange: (next: Settings) => void
  anyActive: boolean
  anyEntry: boolean
}) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [address, setAddress] = useState(settings.public_url)
  const [addressError, setAddressError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // The switch moves at once; the server's answer confirms it, a refusal puts it back.
  async function save(change: Partial<Settings>): Promise<boolean> {
    onChange({ ...settings, ...change })
    try {
      onChange(await api.put<Settings>('/api/settings', change))
      return true
    } catch (caught) {
      onChange(settings)
      notify(errorMessage(caught))
      return false
    }
  }

  async function saveAddress(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setAddressError(null)
    try {
      onChange(await api.put<Settings>('/api/settings', { public_url: address.trim() }))
      notify(t('signin.publicUrlSaved'))
    } catch (caught) {
      setAddressError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('oidc.admin.signInTitle')}>
      <Switch
        label={t('oidc.admin.password')}
        hint={t('oidc.admin.passwordHint')}
        checked={settings.password_login}
        // Without an active provider nobody but the operator could come in: the switch stays on.
        disabled={settings.password_login && !anyActive}
        onChange={(value) => void save({ password_login: value })}
      />
      <form id="sign-in-address" className="grid gap-3 border-t border-ink-700 pt-4 sm:grid-cols-[1fr_auto] sm:items-start" onSubmit={(event) => void saveAddress(event)}>
        <Field
          label={t('oidc.admin.address')}
          hint={t('oidc.admin.addressHint')}
          value={address}
          onChange={(event) => setAddress(event.target.value)}
          placeholder="https://nextrmnl.example.com"
          spellCheck={false}
          error={addressError}
        />
        <Button type="submit" className="sm:mt-6" loading={busy} disabled={address.trim() === settings.public_url}>
          {t('common.save')}
        </Button>
      </form>
      {anyEntry && <Banner tone="warn">{t('signin.publicUrlNote')}</Banner>}
      <div className="border-t border-ink-700 pt-4">
        <Switch
          label={t('twofactor.requiredSwitch')}
          hint={t('twofactor.requiredHint')}
          checked={settings.two_factor_required}
          onChange={(value) => void save({ two_factor_required: value })}
        />
      </div>
    </Section>
  )
}

// --- Card 2: the provider list ---------------------------------------------------------------------------------------

/** The same issuer, however written at the end (the server's `same_issuer` for an issuer without a placeholder). */
function sameIssuer(a: string, b: string): boolean {
  return a.trim().replace(/\/+$/, '') === b.trim().replace(/\/+$/, '')
}

function ProvidersCard({ providers, reload, publicUrl }: { providers: OidcProvider[] | null; reload: () => Promise<void>; publicUrl: string }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [editing, setEditing] = useState<OidcProvider | 'new' | null>(null)
  const [removing, setRemoving] = useState<{ entry: OidcProvider; count: number; only: number } | null>(null)
  const [dragged, setDragged] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const list = providers ?? []
  const readOnly = list.some((entry) => !entry.editable)

  async function act(work: () => Promise<void>) {
    setBusy(true)
    try {
      await work()
    } catch (caught) {
      notify(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const move = (from: number, to: number) => {
    if (from === to || to < 0 || to >= list.length) return
    const ids = list.map((entry) => entry.id)
    const [taken] = ids.splice(from, 1)
    ids.splice(to, 0, taken)
    void act(async () => {
      await api.put('/api/oidc/admin/providers/order', { ids })
      await reload()
    })
  }

  return (
    <Section title={t('oidc.admin.providersTitle')} intro={t('oidc.admin.providersText')}>
      {providers !== null && list.length === 0 && <p className="text-sm text-mist-400">{t('oidc.admin.empty')}</p>}
      {list.length > 0 && (
        <ul className="flex flex-col divide-y divide-ink-700 rounded-xl border border-ink-700" data-testid="provider-list">
          {list.map((entry, index) => (
            <li
              key={entry.id}
              className={'flex flex-wrap items-center gap-2 px-3 py-3 text-sm ' + (dragged === index ? 'opacity-50' : '')}
              draggable={!readOnly && list.length > 1}
              onDragStart={() => setDragged(index)}
              onDragEnd={() => setDragged(null)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                if (dragged !== null) move(dragged, index)
                setDragged(null)
              }}
            >
              {!readOnly && list.length > 1 && (
                <button
                  type="button"
                  className="cursor-grab rounded p-1 text-mist-500 hover:text-mist-200"
                  aria-label={t('signin.order', { name: entry.label })}
                  onKeyDown={(event) => {
                    if (event.key === 'ArrowUp') move(index, index - 1)
                    if (event.key === 'ArrowDown') move(index, index + 1)
                  }}
                >
                  <Symbol name="grip" className="h-4 w-4" />
                </button>
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-mist-100">{entry.label}</span>
                <span className="block truncate font-mono text-xs text-mist-500">{entry.issuer}</span>
              </span>
              <span className="flex flex-wrap items-center gap-1.5">
                <Badge tone={entry.enabled ? 'ok' : 'neutral'}>{entry.enabled ? t('oidc.admin.active') : t('oidc.admin.inactive')}</Badge>
                {entry.managed === 'authentik' && <Badge tone="accent">{t('oidc.admin.managed')}</Badge>}
              </span>
              {entry.editable && (
                <span className="flex gap-1.5">
                  <Button variant="ghost" size="sm" onClick={() => setEditing(entry)}>
                    {t('oidc.admin.edit')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    loading={busy}
                    onClick={() =>
                      void act(async () => {
                        const impact = await api.get<OidcImpact>(`/api/oidc/admin/providers/${entry.id}/impact`)
                        setRemoving({ entry, count: impact.count, only: impact.only })
                      })
                    }
                  >
                    {t('oidc.admin.remove')}
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {!readOnly && (
        <div>
          <Button size="sm" onClick={() => setEditing('new')}>
            <Symbol name="plus" className="h-3.5 w-3.5" />
            {t('oidc.admin.add')}
          </Button>
        </div>
      )}
      <Banner>{t('signin.vaultNote')}</Banner>
      {editing !== null && (
        <ProviderDialog
          entry={editing === 'new' ? null : editing}
          publicUrl={publicUrl}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null)
            notify(t('signin.saved'))
            await reload()
          }}
        />
      )}
      <Dialog
        open={removing !== null}
        title={t('oidc.admin.remove')}
        onClose={() => setRemoving(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={busy}
              onClick={() =>
                void act(async () => {
                  if (!removing) return
                  await api.delete(`/api/oidc/admin/providers/${removing.entry.id}`)
                  setRemoving(null)
                  notify(t('signin.removed'))
                  await reload()
                })
              }
            >
              {t('oidc.admin.remove')}
            </Button>
          </>
        }
      >
        {removing && <p className="text-sm text-mist-300">{t('oidc.admin.removeConfirm', { name: removing.entry.label, count: removing.count, only: removing.only })}</p>}
      </Dialog>
    </Section>
  )
}

const EMPTY: OidcProviderForm = {
  label: '',
  slug: '',
  issuer: '',
  client_id: '',
  client_secret: '',
  scopes: 'openid profile email',
  enabled: true,
  auto_create: false,
  trusts_second_factor: true,
}

type FieldName = 'label' | 'slug' | 'issuer' | 'client_id' | 'client_secret' | 'scopes' | 'id'

/** The provider form as a dialog, fields in the blueprint's order. */
function ProviderDialog({
  entry,
  publicUrl,
  onClose,
  onSaved,
}: {
  entry: OidcProvider | null
  publicUrl: string
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [form, setForm] = useState<OidcProviderForm>(
    entry
      ? {
          label: entry.label,
          issuer: entry.issuer,
          client_id: entry.client_id,
          client_secret: '',
          scopes: entry.scopes,
          enabled: entry.enabled,
          auto_create: entry.auto_create,
          trusts_second_factor: entry.trusts_second_factor,
        }
      : EMPTY,
  )
  // The short name follows the name on the button until it is typed itself.
  const [slugTyped, setSlugTyped] = useState(false)
  const [busy, setBusy] = useState(false)
  const [fieldError, setFieldError] = useState<{ field: FieldName; text: string } | null>(null)
  const [confirmIssuer, setConfirmIssuer] = useState<number | null>(null)

  const slug = entry ? entry.slug : slugTyped ? (form.slug ?? '') : slugFromLabel(form.label)
  const base = (publicUrl || window.location.origin).replace(/\/+$/, '')
  const redirect = entry ? entry.redirect_uri : `${base}/api/oidc/${slug || '…'}/callback`
  const set = (change: Partial<OidcProviderForm>) => setForm((current) => ({ ...current, ...change }))

  const errorFor = (error: unknown): { field: FieldName; text: string } => {
    if (!(error instanceof ApiError)) return { field: 'id', text: errorMessage(error) }
    const field = (typeof error.data?.field === 'string' ? error.data.field : 'id') as FieldName
    const key = oidcFormErrorKey(error.code) ?? oidcErrorKey(error.code)
    return { field, text: key ? t(key) : error.message }
  }

  async function save() {
    setBusy(true)
    setFieldError(null)
    try {
      const body = { ...form, slug: entry ? undefined : slug }
      if (entry) await api.put(`/api/oidc/admin/providers/${entry.id}`, body)
      else await api.post('/api/oidc/admin/providers', body)
      await onSaved()
    } catch (error) {
      setFieldError(errorFor(error))
    } finally {
      setBusy(false)
    }
  }

  async function submit() {
    // Another issuer is another provider: say first how many accounts will have to link again.
    if (entry && entry.links > 0 && !sameIssuer(entry.issuer, form.issuer)) {
      try {
        const impact = await api.get<OidcImpact>(`/api/oidc/admin/providers/${entry.id}/impact?issuer=${encodeURIComponent(form.issuer.trim())}`)
        if (impact.issuer_change > 0) {
          setConfirmIssuer(impact.issuer_change)
          return
        }
      } catch (error) {
        setFieldError(errorFor(error))
        return
      }
    }
    await save()
  }

  const problem = (field: FieldName) => (fieldError?.field === field ? fieldError.text : null)

  return (
    <>
      <Dialog
        open
        title={entry ? `${t('oidc.admin.edit')}: ${entry.label}` : t('oidc.admin.add')}
        onClose={() => {
          if (!busy) onClose()
        }}
        footer={
          <>
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" form="provider-form" loading={busy}>
              {t('common.save')}
            </Button>
          </>
        }
      >
        <form
          id="provider-form"
          className="flex flex-col gap-4"
          data-testid="provider-form"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <Field label={t('oidc.admin.label')} value={form.label} onChange={(event) => set({ label: event.target.value })} error={problem('label')} autoFocus />
          {entry ? (
            <p className="text-xs text-mist-500">
              {t('oidc.admin.slug')}: <code className="font-mono text-mist-300">{entry.slug}</code>
            </p>
          ) : (
            <Field
              label={t('oidc.admin.slug')}
              hint={t('oidc.admin.slugHint')}
              value={slug}
              spellCheck={false}
              error={problem('slug')}
              onChange={(event) => {
                setSlugTyped(true)
                set({ slug: event.target.value })
              }}
            />
          )}
          <Field
            label={t('oidc.admin.issuer')}
            hint={t('oidc.admin.issuerHint')}
            value={form.issuer}
            spellCheck={false}
            placeholder="https://auth.example.com/application/o/nextrmnl/"
            error={problem('issuer')}
            onChange={(event) => set({ issuer: event.target.value })}
          />
          <Field label={t('oidc.admin.clientId')} value={form.client_id} spellCheck={false} error={problem('client_id')} onChange={(event) => set({ client_id: event.target.value })} />
          <Field
            label={t('oidc.admin.secret')}
            type="password"
            autoComplete="off"
            value={form.client_secret}
            placeholder={entry?.has_secret ? t('oidc.admin.secretKept') : ''}
            error={problem('client_secret')}
            onChange={(event) => set({ client_secret: event.target.value })}
          />
          <div className="flex flex-col gap-1.5">
            <p className="text-sm font-medium text-mist-300">{t('oidc.admin.redirect')}</p>
            <div className="flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2">
              <code className="min-w-0 flex-1 truncate font-mono text-xs text-mist-200">{redirect}</code>
              <button
                type="button"
                onClick={() => void writeClipboard(redirect).then((ok) => notify(ok ? t('vault.copied') : t('clipboard.copyFailed')))}
                className="rounded-full p-1 text-mist-500 hover:text-mist-100"
                aria-label={t('signin.copyRedirect')}
              >
                <Symbol name="copy" className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          <Switch label={t('oidc.admin.enabled')} checked={form.enabled} onChange={(enabled) => set({ enabled })} />
          <Switch label={t('oidc.admin.autoCreate')} hint={t('oidc.admin.autoCreateHint')} checked={form.auto_create} onChange={(auto_create) => set({ auto_create })} />
          <Switch
            label={t('oidc.admin.trustsSecondFactor')}
            hint={t('oidc.admin.trustsSecondFactorHint')}
            checked={form.trusts_second_factor}
            onChange={(trusts_second_factor) => set({ trusts_second_factor })}
          />
          <details open={fieldError?.field === 'scopes' ? true : undefined}>
            <summary className="cursor-pointer text-xs text-mist-400">{t('signin.more')}</summary>
            <div className="mt-2">
              <Field label={t('oidc.admin.scopes')} value={form.scopes} spellCheck={false} error={problem('scopes')} onChange={(event) => set({ scopes: event.target.value })} />
            </div>
          </details>
          {fieldError?.field === 'id' && <Banner tone="bad">{fieldError.text}</Banner>}
        </form>
      </Dialog>
      <Dialog
        open={confirmIssuer !== null}
        title={t('oidc.admin.issuer')}
        onClose={() => setConfirmIssuer(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmIssuer(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              loading={busy}
              onClick={() => {
                setConfirmIssuer(null)
                void save()
              }}
            >
              {t('common.save')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{t('oidc.admin.issuerChangeConfirm', { count: confirmIssuer ?? 0 })}</p>
      </Dialog>
    </>
  )
}

// --- Card 3: authentik in one step ------------------------------------------------------------------------------------

/** The address of the authentik behind an entry the button made: its issuer without `/application/o/<slug>`. */
function authentikBase(entry: OidcProvider | undefined): string {
  return entry ? entry.issuer.replace(/\/application\/o\/[^/]+\/?$/, '') : ''
}

function AuthentikCard({ publicUrl, managed, reload }: { publicUrl: string; managed: OidcProvider | undefined; reload: () => Promise<void> }) {
  const { t } = useTranslation()
  const notify = useNotice()
  // null until the person types: then the address of an authentik that is set up already is offered, so that
  // running the button again only needs a fresh token.
  const [url, setUrl] = useState<string | null>(null)
  const [token, setToken] = useState('')
  const [result, setResult] = useState<AuthentikResult | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [askIssuer, setAskIssuer] = useState<number | null>(null)
  const urlValue = url ?? authentikBase(managed)

  async function run(confirmIssuerChange = false) {
    setRunning(true)
    setError(null)
    setResult(null)
    try {
      const answer = await api.post<AuthentikResult>('/api/oidc/authentik/setup', {
        url: urlValue.trim(),
        token,
        ...(confirmIssuerChange ? { confirm_issuer_change: true } : {}),
      })
      const accounts = authentikIssuerChange(answer)
      if (accounts !== null) {
        // The same question as the form: another issuer drops the links. The token stays for the second run.
        setAskIssuer(accounts)
        return
      }
      setResult(answer)
      setToken('')
      await reload()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Section title={t('oidc.authentik.title')} intro={t('oidc.authentik.text')}>
      {!publicUrl && (
        <Banner tone="warn">
          <span data-testid="authentik-no-address">
            {t('oidc.authentik.noAddress')}{' '}
            <a href="#sign-in-address" className="underline">
              {t('oidc.admin.address')}
            </a>
          </span>
        </Banner>
      )}
      <form
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (token.trim() && urlValue.trim()) void run()
        }}
      >
        <Field label={t('oidc.authentik.url')} value={urlValue} onChange={(event) => setUrl(event.target.value)} placeholder="https://auth.example.com" spellCheck={false} />
        <Field label={t('oidc.authentik.token')} type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" hint={t('signin.tokenHint')} />
        <div className="flex flex-wrap items-center gap-3 sm:col-span-2">
          <Button type="submit" loading={running} disabled={!token.trim() || !urlValue.trim()}>
            <Symbol name="check" />
            {t('oidc.authentik.run')}
          </Button>
          <Button
            type="button"
            variant="link"
            onClick={() => void downloadFile('/api/oidc/authentik/blueprint', 'nextrmnl-authentik.yaml').catch((caught) => notify(errorMessage(caught)))}
          >
            <Symbol name="download" className="h-3.5 w-3.5" />
            {t('oidc.authentik.blueprint')}
          </Button>
        </div>
      </form>
      {running && (
        <p className="flex items-center gap-2 text-xs text-mist-400" aria-live="polite">
          <Spinner className="h-3.5 w-3.5 text-accent-400" />
          {t('oidc.authentik.asking')}
        </p>
      )}
      {error && <Banner tone="bad">{error}</Banner>}
      {result && (
        <ol className="flex flex-col gap-2.5" data-testid="authentik-steps">
          {AUTHENTIK_STEPS.map((key) => {
            const step = result.steps.find((entry) => entry.key === key)
            const name = authentikStepKey(key)
            const why = step && !step.ok ? authentikReasonKey(step.reason) : null
            const state = step ? (step.ok ? 'done' : 'failed') : 'waiting'
            return (
              <li key={key} className="flex items-start gap-3 text-sm">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
                  {state === 'done' && <Symbol name="check" className="h-4 w-4 text-ok-500" />}
                  {state === 'failed' && <Symbol name="close" className="h-4 w-4 text-bad-500" />}
                  {state === 'waiting' && <span className="h-1.5 w-1.5 rounded-full bg-ink-600" />}
                </span>
                <span className={state === 'waiting' ? 'text-mist-600' : state === 'failed' ? 'font-medium text-bad-500' : 'text-mist-200'}>
                  {name ? t(name) : key}
                  {why && <span className="block text-xs font-normal text-mist-400">{t(why, { status: step?.status ?? 0, count: result.count ?? 0 })}</span>}
                </span>
              </li>
            )
          })}
        </ol>
      )}
      {result?.ok && <Banner tone="ok">{t('signin.doneText')}</Banner>}
      {result && (
        <div>
          <Button variant="ghost" size="sm" onClick={() => setResult(null)}>
            {t('signin.again')}
          </Button>
        </div>
      )}
      <Dialog
        open={askIssuer !== null}
        title={t('oidc.admin.issuer')}
        onClose={() => setAskIssuer(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setAskIssuer(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                setAskIssuer(null)
                void run(true)
              }}
            >
              {t('oidc.authentik.run')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{t('oidc.admin.issuerChangeConfirm', { count: askIssuer ?? 0 })}</p>
      </Dialog>
    </Section>
  )
}

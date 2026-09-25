import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../../api/client'
import type { AccountName, AuthMethod, Connection, ConnectionIn, VaultKey } from '../../api/types'
import { useAuth } from '../../auth'
import { useLoad } from '../../lib/useLoad'
import { useWorkspace } from '../../state/workspace'
import { Dialog } from '../Dialog'
import { Symbol } from '../Symbol'
import { TabRow } from '../TabRow'
import { Banner, Button, Field, SelectField, Switch } from '../ui'

type Tab = 'basics' | 'auth' | 'advanced' | 'share'

function draftOf(connection: Connection | null, group: string): ConnectionIn {
  if (!connection) {
    return { name: '', group, host: '', port: 22, user: '', auth: 'key', key_id: null, jump_id: null, keepalive: true, start_command: '' }
  }
  const { name, host, port, user, auth, key_id, jump_id, keepalive, start_command } = connection
  return { name, group: connection.group, host, port, user, auth, key_id, jump_id, keepalive, start_command }
}

/** Create and edit. A connection shared from another account is read-only. */
export function ConnectionDialog({ connection, onClose, startTab = 'basics' }: { connection: Connection | null; onClose: () => void; startTab?: Tab }) {
  const { t } = useTranslation()
  const { account } = useAuth()
  const { connections, reloadConnections, openConnection } = useWorkspace()
  const ownGroups = [...new Set(connections.filter((c) => !c.shared_by && c.group).map((c) => c.group))]
  const [draft, setDraft] = useState<ConnectionIn>(() => draftOf(connection, ownGroups[0] ?? ''))
  const [shareIds, setShareIds] = useState<number[]>(connection?.shared_with ?? [])
  const [tab, setTab] = useState<Tab>(startTab)
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const keys = useLoad(() => api.get<VaultKey[]>('/api/vault/keys'))
  const names = useLoad(() => api.get<AccountName[]>('/api/accounts/names'))
  const readOnly = Boolean(connection?.shared_by)
  const isNew = connection === null
  const keyMissing = draft.auth === 'key' && (keys.data?.length ?? 0) === 0
  const valid = draft.name.trim() !== '' && draft.host.trim() !== '' && draft.user.trim() !== '' && !keyMissing

  const set = <K extends keyof ConnectionIn>(key: K, value: ConnectionIn[K]) => setDraft((current) => ({ ...current, [key]: value }))

  // Without a chosen key, it takes the first one from the vault; otherwise the server rejects it.
  const effective: ConnectionIn = {
    ...draft,
    name: draft.name.trim(),
    host: draft.host.trim(),
    user: draft.user.trim(),
    group: draft.group.trim(),
    key_id: draft.auth === 'key' ? (draft.key_id ?? keys.data?.[0]?.id ?? null) : null,
  }

  async function save(connect: boolean) {
    setBusy(true)
    setError(null)
    try {
      // On a connection shared by someone else, only my own login belongs to me: user, method, own key.
      const saved =
        connection && readOnly
          ? await api.put<Connection>(`/api/connections/${connection.id}/my-access`, { user: effective.user, auth: effective.auth, key_id: effective.key_id, start_command: effective.start_command.trim() })
          : connection
            ? await api.put<Connection>(`/api/connections/${connection.id}`, effective)
            : await api.post<Connection>('/api/connections', effective)
      if (draft.auth === 'password' && password) await api.put(`/api/vault/passwords/${saved.id}`, { password })
      const before = connection?.shared_with ?? []
      if ([...shareIds].sort().join(',') !== [...before].sort().join(',')) {
        await api.put(`/api/connections/${saved.id}/share`, { account_ids: shareIds })
      }
      await reloadConnections()
      onClose()
      if (connect) openConnection(saved.id, saved)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function forgetHostKey() {
    if (!connection) return
    try {
      await api.post(`/api/connections/${connection.id}/host-key/forget`)
      await reloadConnections()
      onClose()
    } catch (caught) {
      setError(errorMessage(caught))
    }
  }

  const tabs: { value: Tab; label: string }[] = [
    { value: 'basics', label: t('edit.tabBasics') },
    { value: 'auth', label: t('edit.tabAuth') },
    { value: 'advanced', label: t('edit.tabAdvanced') },
    { value: 'share', label: t('edit.tabShare') },
  ]

  const methods: { value: AuthMethod; label: string; hint: string }[] = [
    { value: 'key', label: t('edit.authKey'), hint: t('edit.authKeyHint') },
    { value: 'password', label: t('edit.authPassword'), hint: t('edit.authPasswordHint') },
    { value: 'ask', label: t('edit.authAsk'), hint: t('edit.authAskHint') },
  ]

  return (
    <Dialog
      open
      title={isNew ? t('edit.newTitle') : readOnly ? draft.name : t('edit.editTitle', { name: connection?.name ?? '' })}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button variant="ghost" disabled={!valid} loading={busy} onClick={() => void save(false)}>
            {t('common.save')}
          </Button>
          <Button disabled={!valid} loading={busy} onClick={() => void save(true)}>
            {t('edit.saveConnect')}
          </Button>
        </>
      }
    >
      <TabRow tabs={tabs.filter((item) => item.value !== 'share' || !readOnly)} active={tab} onChange={setTab} small label={t('edit.tabsLabel')} />

      {readOnly && <Banner>{t(tab === 'auth' ? 'edit.sharedOwnAccess' : 'edit.sharedReadOnly', { owner: connection?.shared_by })}</Banner>}
      {error && <Banner tone="bad">{error}</Banner>}

      {/* On a shared connection, only the login is mine: host, port and settings stay with the owner. */}
      <fieldset disabled={readOnly && tab !== 'auth'} className="flex flex-col gap-4">
        {tab === 'basics' && (
          <>
            <Field label={t('edit.name')} value={draft.name} onChange={(event) => set('name', event.target.value)} placeholder="web-01" autoFocus={isNew} />
            <div className="flex flex-col gap-1.5">
              <label htmlFor="group" className="text-sm font-medium text-mist-300">
                {t('edit.group')}
              </label>
              <input
                id="group"
                list="groups"
                value={draft.group}
                onChange={(event) => set('group', event.target.value)}
                className="w-full rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 focus:border-accent-500 focus:outline-none"
              />
              <datalist id="groups">
                {ownGroups.map((group) => (
                  <option key={group} value={group} />
                ))}
              </datalist>
            </div>
            <div className="grid grid-cols-[1fr_6.5rem] gap-3">
              <Field label={t('edit.host')} value={draft.host} onChange={(event) => set('host', event.target.value)} placeholder="192.0.2.21" spellCheck={false} />
              <Field label={t('edit.port')} type="number" min={1} max={65535} value={draft.port} onChange={(event) => set('port', Number(event.target.value) || 22)} />
            </div>
            <Field label={t('edit.user')} value={draft.user} onChange={(event) => set('user', event.target.value)} placeholder="admin" spellCheck={false} autoComplete="off" />
          </>
        )}

        {tab === 'auth' && (
          <>
            {readOnly && (
              <Field
                label={t('edit.user')}
                value={draft.user}
                onChange={(event) => set('user', event.target.value)}
                placeholder={connection?.owner_user}
                hint={t('edit.ownUserHint', { owner: connection?.shared_by, user: connection?.owner_user })}
                spellCheck={false}
                autoComplete="off"
              />
            )}
            <div className="flex flex-col gap-2" role="radiogroup" aria-label={t('edit.tabAuth')}>
              {methods.map((method) => (
                <label
                  key={method.value}
                  className={
                    'flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ' +
                    (draft.auth === method.value ? 'border-accent-500/60 bg-accent-500/8' : 'border-ink-700 hover:bg-ink-800')
                  }
                >
                  <input type="radio" name="auth" className="mt-1 accent-accent-500" checked={draft.auth === method.value} onChange={() => set('auth', method.value)} />
                  <span>
                    <span className="block text-sm font-medium text-mist-100">{method.label}</span>
                    <span className="block text-xs text-mist-500">{method.hint}</span>
                  </span>
                </label>
              ))}
            </div>
            {draft.auth === 'key' &&
              (keyMissing ? (
                <Banner tone="warn">{t('edit.noKeys')}</Banner>
              ) : (
                <SelectField label={t('edit.key')} value={String(effective.key_id ?? '')} onChange={(value) => set('key_id', Number(value))}>
                  {(keys.data ?? []).map((key) => (
                    <option key={key.id} value={key.id}>
                      {key.name} ({key.key_type})
                    </option>
                  ))}
                </SelectField>
              ))}
            {draft.auth === 'password' && (
              <Field
                label={t('edit.password')}
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={isNew ? '' : '••••••••'}
                hint={isNew ? undefined : t('edit.passwordHint')}
                autoComplete="new-password"
              />
            )}
            <p className="flex items-center gap-2 text-xs text-mist-500">
              <Symbol name="vault" className="h-4 w-4 text-accent-400" />
              {t('edit.vaultNote')}
            </p>
          </>
        )}

        {tab === 'advanced' && (
          <>
            <SelectField label={t('edit.jump')} value={draft.jump_id === null ? '' : String(draft.jump_id)} onChange={(value) => set('jump_id', value ? Number(value) : null)}>
              <option value="">{t('edit.jumpNone')}</option>
              {connections
                .filter((c) => c.id !== connection?.id && c.jump_id === null)
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.host})
                  </option>
                ))}
            </SelectField>
            <p className="-mt-2 text-xs text-mist-500">{t('edit.jumpHint')}</p>

            <div className="rounded-xl border border-ink-700 bg-ink-900 p-4">
              <p className="mb-1 flex items-center gap-2 text-sm font-medium text-mist-200">
                <Symbol name="fingerprint" className="h-4 w-4 text-mist-500" />
                {t('edit.hostKey')}
              </p>
              {!connection || connection.host_key.state !== 'known' ? (
                <p className="text-xs text-mist-500">{t('edit.hostKeyUnknown')}</p>
              ) : (
                <div className="flex items-center gap-3">
                  <p className="min-w-0 flex-1 font-mono text-xs break-all text-mist-300">
                    {connection.host_key.key_type.replace('ssh-', '').toUpperCase()} {connection.host_key.fingerprint}
                  </p>
                  {!readOnly && (
                    <Button variant="ghost" size="sm" onClick={() => void forgetHostKey()}>
                      {t('edit.hostKeyForget')}
                    </Button>
                  )}
                </div>
              )}
            </div>

            <Switch label={t('edit.keepalive')} hint={t('edit.keepaliveHint')} checked={draft.keepalive} onChange={(value) => set('keepalive', value)} disabled={readOnly} />
            <Field label={t('edit.startCommand')} value={draft.start_command} onChange={(event) => set('start_command', event.target.value)} placeholder="tmux new -A -s main" hint={readOnly ? t('edit.ownStartCommandHint') : t('edit.startCommandHint')} spellCheck={false} />
          </>
        )}

        {tab === 'share' && !readOnly && (
          <>
            <p className="text-sm text-mist-300">{t('edit.shareLead')}</p>
            <div className="flex flex-col gap-2">
              {(names.data ?? [])
                .filter((other) => other.id !== account?.id)
                .map((other) => {
                  const checked = shareIds.includes(other.id)
                  return (
                    <label key={other.id} className="flex cursor-pointer items-center gap-3 rounded-xl border border-ink-700 p-3 hover:bg-ink-800">
                      <input
                        type="checkbox"
                        className="accent-accent-500"
                        checked={checked}
                        onChange={() => setShareIds(checked ? shareIds.filter((id) => id !== other.id) : [...shareIds, other.id])}
                      />
                      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-ink-800 text-xs font-semibold text-mist-300 uppercase">{other.name.slice(0, 1)}</span>
                      <span className="text-sm text-mist-100">{other.name}</span>
                    </label>
                  )
                })}
              {(names.data?.length ?? 0) <= 1 && <p className="text-sm text-mist-500">{t('edit.nobodyToShare')}</p>}
            </div>
            <Banner>{t('edit.shareNote')}</Banner>
          </>
        )}
      </fieldset>
    </Dialog>
  )
}

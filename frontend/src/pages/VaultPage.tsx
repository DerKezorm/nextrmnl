import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../api/client'
import type { VaultKey, VaultPassword } from '../api/types'
import { useAuth } from '../auth'
import { Dialog } from '../components/Dialog'
import { useNotice } from '../components/Notice'
import { Symbol } from '../components/Symbol'
import { Badge, Banner, Button, Card, Field, PageHeader, SelectField } from '../components/ui'
import { VaultBackupCard } from '../components/vault/VaultBackup'
import { writeClipboard } from '../lib/clipboard'
import { formatDate } from '../lib/format'
import { useLoad } from '../lib/useLoad'
import { useWorkspace } from '../state/workspace'

function GenerateDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [name, setName] = useState('')
  const [type, setType] = useState('ed25519')
  const [passphrase, setPassphrase] = useState('')
  const [created, setCreated] = useState<VaultKey | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function generate() {
    setBusy(true)
    try {
      setCreated(await api.post<VaultKey>('/api/vault/keys/generate', { name: name.trim(), key_type: type, passphrase }))
      onDone()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  if (created) {
    return (
      <Dialog open title={t('vault.generatedTitle', { name: created.name })} onClose={onClose} footer={<Button onClick={onClose}>{t('common.done')}</Button>}>
        <p className="text-sm text-mist-300">{t('vault.generatedLead')}</p>
        <div className="flex items-start gap-2 rounded-xl border border-ink-700 bg-ink-900 p-3">
          <code className="min-w-0 flex-1 font-mono text-xs break-all text-mist-200">{created.public_key}</code>
          <button type="button" onClick={() => void writeClipboard(created.public_key).then((ok) => notify(ok ? t('vault.copied') : t('clipboard.copyFailed')))} className="rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100" aria-label={t('vault.copyPublic')}>
            <Symbol name="copy" />
          </button>
        </div>
        <p className="text-xs text-mist-500">{t('vault.generatedHint')}</p>
      </Dialog>
    )
  }

  return (
    <Dialog
      open
      title={t('vault.generate')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!name.trim()} loading={busy} onClick={() => void generate()}>
            {t('vault.generateNow')}
          </Button>
        </>
      }
    >
      <Field label={t('vault.keyName')} value={name} onChange={(event) => setName(event.target.value)} placeholder="homelab" autoFocus />
      <SelectField label={t('vault.keyType')} value={type} onChange={setType}>
        <option value="ed25519">{t('vault.typeEd25519')}</option>
        <option value="rsa">{t('vault.typeRsa')}</option>
      </SelectField>
      <Field label={t('vault.passphrase')} type="password" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} hint={t('vault.passphraseHint')} autoComplete="new-password" />
      {error && <Banner tone="bad">{error}</Banner>}
    </Dialog>
  )
}

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    try {
      await api.post('/api/vault/keys/import', { name: name.trim(), private_key: privateKey, passphrase })
      onDone()
      onClose()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      title={t('vault.import')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!name.trim() || !privateKey.trim()} loading={busy} onClick={() => void run()}>
            {t('vault.importNow')}
          </Button>
        </>
      }
    >
      <Field label={t('vault.keyName')} value={name} onChange={(event) => setName(event.target.value)} placeholder="alter-laptop" autoFocus />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="private-key" className="text-sm font-medium text-mist-300">
          {t('vault.privateKey')}
        </label>
        <textarea
          id="private-key"
          rows={6}
          value={privateKey}
          onChange={(event) => setPrivateKey(event.target.value)}
          spellCheck={false}
          placeholder={'-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----'}
          className="w-full rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 font-mono text-xs text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
        />
        <p className="text-xs text-mist-500">{t('vault.privateKeyHint')}</p>
      </div>
      <Field label={t('vault.passphrase')} type="password" value={passphrase} onChange={(event) => setPassphrase(event.target.value)} hint={t('vault.importPassphraseHint')} autoComplete="off" />
      {error && <Banner tone="bad">{error}</Banner>}
    </Dialog>
  )
}

function PasswordDialog({ connectionId, name, onClose, onDone }: { connectionId: number; name: string; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    try {
      await api.put(`/api/vault/passwords/${connectionId}`, { password })
      onDone()
      onClose()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      title={t('vault.passwordFor', { name })}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button disabled={!password} loading={busy} onClick={() => void run()}>
            {t('common.save')}
          </Button>
        </>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (password) void run()
        }}
      >
        <Field label={t('password.label')} type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus autoComplete="off" />
      </form>
      {error && <Banner tone="bad">{error}</Banner>}
    </Dialog>
  )
}

/** Keys and passwords of the own account. Only the own password opens the vault. */
export function VaultPage() {
  const { t } = useTranslation()
  const notify = useNotice()
  const { account, setVault } = useAuth()
  const { connections } = useWorkspace()
  const keys = useLoad(() => api.get<VaultKey[]>('/api/vault/keys'))
  const passwords = useLoad(() => api.get<VaultPassword[]>('/api/vault/passwords'))
  const [dialog, setDialog] = useState<'generate' | 'import' | null>(null)
  const [passwordFor, setPasswordFor] = useState<{ id: number; name: string } | null>(null)
  const [deleting, setDeleting] = useState<VaultKey | null>(null)

  const reload = () => {
    void keys.reload()
    void passwords.reload()
  }

  async function lockVault() {
    try {
      await api.post('/api/vault/lock')
      setVault('locked')
    } catch (error) {
      notify(errorMessage(error))
    }
  }

  async function deleteKey() {
    if (!deleting) return
    try {
      await api.delete(`/api/vault/keys/${deleting.id}`)
      setDeleting(null)
      void keys.reload()
    } catch (error) {
      notify(errorMessage(error))
    }
  }

  async function forgetPassword(connectionId: number) {
    try {
      await api.delete(`/api/vault/passwords/${connectionId}`)
      void passwords.reload()
    } catch (error) {
      notify(errorMessage(error))
    }
  }

  const copyPublic = async (key: VaultKey) => notify((await writeClipboard(key.public_key)) ? t('vault.copied') : t('clipboard.copyFailed'))
  const passwordConnections = connections.filter((c) => !c.shared_by && c.auth === 'password')

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t('vault.title')} lead={t('vault.lead')} />

      <Card className="flex flex-wrap items-center gap-4">
        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-ok-500/12 text-ok-500">
          <Symbol name="unlocked" className="h-6 w-6" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-mist-100">{t('vault.openState')}</p>
          <p className="text-sm text-mist-500">{t('vault.openHint')}</p>
        </div>
        <Button variant="ghost" onClick={() => void lockVault()}>
          <Symbol name="vault" />
          {t('vault.lockNow')}
        </Button>
      </Card>

      <Card className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">{t('vault.keys')}</h2>
            <p className="text-sm text-mist-500">{t('vault.keysLead')}</p>
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={() => setDialog('import')}>
              {t('vault.import')}
            </Button>
            <Button size="sm" onClick={() => setDialog('generate')}>
              <Symbol name="plus" className="h-3.5 w-3.5" />
              {t('vault.generate')}
            </Button>
          </div>
        </div>
        {keys.error && <Banner tone="bad">{keys.error}</Banner>}
        {keys.data?.length === 0 && <p className="text-sm text-mist-500">{t('vault.noKeys')}</p>}
        <ul className="flex flex-col gap-2">
          {(keys.data ?? []).map((key) => (
            <li key={key.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3.5">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-500/12 text-accent-400">
                <Symbol name="key" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-mist-100">
                  {key.name}
                  <Badge>{key.key_type === 'rsa' ? `RSA ${key.bits}` : key.key_type === 'ecdsa' ? `ECDSA P-${key.bits}` : 'Ed25519'}</Badge>
                  {key.has_passphrase && <Badge tone="accent">{t('vault.withPassphrase')}</Badge>}
                  {key.key_type === 'rsa' && <Badge tone="warn">{t('vault.oldType')}</Badge>}
                </p>
                <p className="truncate font-mono text-xs text-mist-500">{key.fingerprint}</p>
                <p className="text-xs text-mist-600">
                  {key.used_by === 0 ? t('vault.unused') : t('vault.usedBy', { count: key.used_by })} · {t('vault.created', { date: formatDate(key.created_at) })}
                </p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => void copyPublic(key)}>
                <Symbol name="copy" className="h-3.5 w-3.5" />
                {t('vault.copyPublic')}
              </Button>
              <button type="button" onClick={() => setDeleting(key)} className="rounded-full p-1.5 text-mist-500 hover:bg-bad-500/10 hover:text-bad-500" aria-label={t('vault.deleteKey', { name: key.name })}>
                <Symbol name="trash" />
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('vault.passwords')}</h2>
          <p className="text-sm text-mist-500">{t('vault.passwordsLead')}</p>
        </div>
        {passwordConnections.length === 0 && <p className="text-sm text-mist-500">{t('vault.noPasswords')}</p>}
        <ul className="flex flex-col gap-2">
          {passwordConnections.map((connection) => {
            const entry = passwords.data?.find((p) => p.connection_id === connection.id)
            return (
              <li key={connection.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3.5">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-ink-800 text-mist-400">
                  <Symbol name="password" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-mist-100">{connection.name}</p>
                  <p className="font-mono text-xs text-mist-500">
                    {connection.user}@{connection.host}
                  </p>
                </div>
                <span className="text-xs text-mist-600">{entry ? t('vault.changed', { date: formatDate(entry.changed_at) }) : t('vault.notStored')}</span>
                <Button variant="ghost" size="sm" onClick={() => setPasswordFor({ id: connection.id, name: connection.name })}>
                  {entry ? t('vault.change') : t('vault.store')}
                </Button>
                {entry && (
                  <button type="button" onClick={() => void forgetPassword(connection.id)} className="rounded-full p-1.5 text-mist-500 hover:bg-bad-500/10 hover:text-bad-500" aria-label={t('vault.forgetPassword', { name: connection.name })}>
                    <Symbol name="trash" />
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      </Card>

      <VaultBackupCard account={account?.name ?? ''} keys={keys.data?.length ?? 0} passwords={passwords.data?.length ?? 0} onImported={reload} />

      <Banner tone="warn">
        <p className="font-medium">{t('vault.forgotTitle')}</p>
        <p className="mt-0.5 text-mist-400">{t('vault.forgotText')}</p>
      </Banner>

      {dialog === 'generate' && <GenerateDialog onClose={() => setDialog(null)} onDone={() => void keys.reload()} />}
      {dialog === 'import' && <ImportDialog onClose={() => setDialog(null)} onDone={() => void keys.reload()} />}
      {passwordFor && <PasswordDialog connectionId={passwordFor.id} name={passwordFor.name} onClose={() => setPasswordFor(null)} onDone={() => void passwords.reload()} />}
      <Dialog
        open={deleting !== null}
        title={t('vault.deleteKey', { name: deleting?.name ?? '' })}
        onClose={() => setDeleting(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" onClick={() => void deleteKey()}>
              {t('vault.deleteNow')}
            </Button>
          </>
        }
      >
        <p className="text-sm text-mist-300">{deleting && (deleting.used_by > 0 ? t('vault.deleteUsed', { count: deleting.used_by }) : t('vault.deleteText'))}</p>
      </Dialog>
    </div>
  )
}

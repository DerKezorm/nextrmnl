import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, downloadFile, errorMessage } from '../../api/client'
import type { VaultFileBrief, VaultImportResult } from '../../api/types'
import { formatDateTime } from '../../lib/format'
import { Dialog } from '../Dialog'
import { FileField } from '../FileField'
import { useNotice } from '../Notice'
import { Symbol } from '../Symbol'
import { Banner, Button, Card, Field } from '../ui'

/**
 * The own vault as a file, encrypted with the vault password. Reason: if the operator restores a
 * server backup, all vaults revert to the old state, and it cannot reach into other accounts' vaults.
 * So each account backs up its own.
 */
export function VaultBackupCard({ account, keys, passwords, onImported }: { account: string; keys: number; passwords: number; onImported: () => void }) {
  const { t } = useTranslation()
  const [dialog, setDialog] = useState<'export' | 'import' | null>(null)

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">{t('vaultBackup.title')}</h2>
          <p className="mt-1 text-sm text-mist-500">{t('vaultBackup.lead')}</p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="ghost" size="sm" onClick={() => setDialog('import')}>
            <Symbol name="upload" className="h-3.5 w-3.5" />
            {t('vaultBackup.import')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setDialog('export')}>
            <Symbol name="download" className="h-3.5 w-3.5" />
            {t('vaultBackup.export')}
          </Button>
        </div>
      </div>
      {dialog === 'export' && <ExportDialog account={account} keys={keys} passwords={passwords} onClose={() => setDialog(null)} />}
      {dialog === 'import' && (
        <ImportDialog
          onClose={() => setDialog(null)}
          onImported={() => {
            setDialog(null)
            onImported()
          }}
        />
      )}
    </Card>
  )
}

/** Asks for the vault password again, even if the vault is open: whoever is sitting at the open screen should not be able to take the vault with them. */
function ExportDialog({ account, keys, passwords, onClose }: { account: string; keys: number; passwords: number; onClose: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const day = new Date().toISOString().slice(0, 10)

  async function run() {
    setBusy(true)
    try {
      await downloadFile('/api/vault/file/export', `tresor-${account}-${day}.nextrmnl-vault`, { password })
      notify(t('vaultBackup.exported'))
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
      title={t('vaultBackup.exportTitle')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button disabled={password.length === 0} loading={busy} onClick={() => void run()}>
            <Symbol name="download" />
            {t('vaultBackup.exportNow')}
          </Button>
        </>
      }
    >
      <p className="text-sm leading-relaxed text-mist-300">{t('vaultBackup.exportWhat', { keys, passwords })}</p>
      <p className="font-mono text-xs text-mist-500">{`tresor-${account}-${day}.nextrmnl-vault`}</p>
      <Banner>{t('vaultBackup.exportKeep')}</Banner>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (password) void run()
        }}
      >
        <Field label={t('vaultBackup.exportPassword')} type="password" autoComplete="current-password" hint={t('vaultBackup.exportConfirm')} value={password} onChange={(event) => setPassword(event.target.value)} autoFocus />
      </form>
      {error && <Banner tone="bad">{error}</Banner>}
    </Dialog>
  )
}

/** Two steps like on the server: first look at what is in the file, then decide how it comes in. */
function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [file, setFile] = useState<File | null>(null)
  const [password, setPassword] = useState('')
  const [brief, setBrief] = useState<VaultFileBrief | null>(null)
  const [mode, setMode] = useState<'merge' | 'replace'>('merge')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function form(): FormData {
    const data = new FormData()
    data.append('file', file as File)
    data.append('password', password)
    return data
  }

  async function check() {
    setBusy(true)
    setError(null)
    try {
      setBrief(await api.post<VaultFileBrief>('/api/vault/file/check', form()))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const data = form()
      data.append('mode', mode)
      const result = await api.post<VaultImportResult>('/api/vault/file/import', data)
      notify(t('vaultBackup.imported', { ...result }))
      onImported()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const options: { value: typeof mode; label: string; hint: string }[] = [
    { value: 'merge', label: t('vaultBackup.modeMerge'), hint: t('vaultBackup.modeMergeHint') },
    { value: 'replace', label: t('vaultBackup.modeReplace'), hint: t('vaultBackup.modeReplaceHint') },
  ]

  return (
    <Dialog
      open
      title={t('vaultBackup.importTitle')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          {brief === null ? (
            <Button disabled={!file || password.length === 0} loading={busy} onClick={() => void check()}>
              {t('vaultBackup.check')}
            </Button>
          ) : (
            <Button variant={mode === 'replace' ? 'danger' : 'primary'} loading={busy} onClick={() => void run()}>
              {t('vaultBackup.importNow')}
            </Button>
          )}
        </>
      }
    >
      <p className="text-sm text-mist-500">{t('vaultBackup.importLead')}</p>
      <FileField
        label={t('vaultBackup.importFile')}
        accept=".nextrmnl-vault,application/json"
        onChange={(next) => {
          setFile(next)
          setBrief(null)
        }}
      />
      <Field
        label={t('vaultBackup.importPassword')}
        type="password"
        autoComplete="off"
        hint={t('vaultBackup.importPasswordHint')}
        value={password}
        onChange={(event) => {
          setPassword(event.target.value)
          setBrief(null)
        }}
      />
      {error && <Banner tone="bad">{error}</Banner>}
      {brief && (
        <div className="flex flex-col gap-4 rounded-xl border border-ink-700 bg-ink-900 p-4">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-mist-600">{t('vaultBackup.fromAccount')}</dt>
            <dd className="text-mist-100">
              {brief.account} <span className="text-mist-500">({brief.server})</span>
            </dd>
            <dt className="text-mist-600">{t('vaultBackup.fromWhen')}</dt>
            <dd className="text-mist-100">{formatDateTime(brief.created)}</dd>
            <dt className="text-mist-600">{t('vaultBackup.contents')}</dt>
            <dd className="text-mist-100">{t('vaultBackup.contentsText', { keys: brief.keys, passwords: brief.passwords })}</dd>
          </dl>
          <div className="flex flex-col gap-2" role="radiogroup" aria-label={t('vaultBackup.modeLabel')}>
            {options.map((option) => (
              <label
                key={option.value}
                className={'flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ' + (mode === option.value ? 'border-accent-500/60 bg-accent-500/8' : 'border-ink-700 hover:bg-ink-800')}
              >
                <input type="radio" name="vault-import-mode" className="mt-1 accent-accent-500" checked={mode === option.value} onChange={() => setMode(option.value)} />
                <span>
                  <span className="block text-sm font-medium text-mist-100">{option.label}</span>
                  <span className="block text-xs text-mist-500">{option.hint}</span>
                </span>
              </label>
            ))}
          </div>
          <p className="text-xs leading-relaxed text-mist-500">{t('vaultBackup.importNote')}</p>
        </div>
      )}
    </Dialog>
  )
}

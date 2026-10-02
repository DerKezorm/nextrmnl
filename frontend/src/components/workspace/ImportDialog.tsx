import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../../api/client'
import type { Connection, VaultKey } from '../../api/types'
import { creationOrder, decodeImportFile, parseImport, type ImportResult } from '../../lib/importConnections'
import { useLoad } from '../../lib/useLoad'
import { useWorkspace } from '../../state/workspace'
import { Dialog } from '../Dialog'
import { FileField } from '../FileField'
import { useNotice } from '../Notice'
import { Badge, Banner, Button, Field, SelectField } from '../ui'

const MAX_FILE = 2 * 1024 * 1024

function sameTarget(a: { host: string; port: number; user: string }, b: { host: string; port: number; user: string }): boolean {
  return a.host.toLowerCase() === b.host.toLowerCase() && a.port === b.port && a.user === b.user
}

/**
 * Connections from `~/.ssh/config` or a PuTTY export. Read in the browser, shown for a check, then created one by
 * one like hand-made ones. Keys and passwords stay where they are; the sign-in is chosen here for all of them.
 */
export function ImportDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const { connections, reloadConnections } = useWorkspace()
  const keys = useLoad(() => api.get<VaultKey[]>('/api/vault/keys').catch(() => [] as VaultKey[]))
  const [text, setText] = useState('')
  const [result, setResult] = useState<ImportResult | null>(null)
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [group, setGroup] = useState('')
  const [auth, setAuth] = useState('ask')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const read = (source: string) => {
    const parsed = parseImport(source)
    setResult(parsed)
    setError(parsed.connections.length === 0 ? t('import.nothing') : null)
    // What is already there stays unticked; everything else is ticked.
    setChosen(new Set(parsed.connections.filter((entry) => !connections.some((c) => !c.shared_by && sameTarget(c, entry))).map((entry) => entry.name)))
  }

  const readFile = async (file: File | null) => {
    if (!file) return
    if (file.size > MAX_FILE) {
      setError(t('import.tooLarge'))
      return
    }
    const decoded = decodeImportFile(new Uint8Array(await file.arrayBuffer()))
    setText(decoded)
    read(decoded)
  }

  const toggle = (name: string) =>
    setChosen((current) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })

  const run = async () => {
    if (!result) return
    setBusy(true)
    setError(null)
    const created = new Map<string, number>()
    const selected = result.connections.filter((entry) => chosen.has(entry.name))
    // A jump host that was not ticked but is needed comes along, otherwise the jump would point nowhere.
    const needed = new Set(selected.map((entry) => entry.name))
    for (const entry of selected) {
      let jump = entry.jump
      while (jump && !needed.has(jump)) {
        needed.add(jump)
        jump = result.connections.find((other) => other.name === jump)?.jump ?? null
      }
    }
    const keyId = auth.startsWith('key:') ? Number(auth.slice(4)) : null
    let failed = 0
    for (const entry of creationOrder(result.connections.filter((e) => needed.has(e.name)))) {
      try {
        const saved = await api.post<Connection>('/api/connections', {
          name: entry.name,
          group: group.trim(),
          host: entry.host,
          port: entry.port,
          user: entry.user,
          auth: keyId !== null ? 'key' : 'ask',
          key_id: keyId,
          jump_id: entry.jump ? (created.get(entry.jump) ?? null) : null,
        })
        created.set(entry.name, saved.id)
      } catch (caught) {
        failed += 1
        setError(errorMessage(caught))
      }
    }
    await reloadConnections()
    setBusy(false)
    if (failed === 0) {
      notify(t('import.done', { count: created.size }))
      onClose()
    }
  }

  const noteText = (note: string) => t(`import.note.${note}`, { defaultValue: note })

  return (
    <Dialog
      open
      title={t('import.title')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          {result ? (
            <Button loading={busy} disabled={chosen.size === 0} onClick={() => void run()}>
              {t('import.run', { count: chosen.size })}
            </Button>
          ) : (
            <Button disabled={!text.trim()} onClick={() => read(text)}>
              {t('import.read')}
            </Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {!result && (
          <>
            <p className="text-sm text-mist-300">{t('import.lead')}</p>
            <FileField label={t('import.file')} hint={t('import.fileHint')} onChange={(file) => void readFile(file)} />
            <div className="flex flex-col gap-1.5">
              <label htmlFor="import-text" className="text-sm font-medium text-mist-300">
                {t('import.paste')}
              </label>
              <textarea
                id="import-text"
                value={text}
                rows={7}
                spellCheck={false}
                onChange={(event) => setText(event.target.value)}
                placeholder={'Host web-01\n    HostName 192.0.2.10\n    User admin'}
                className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-xs text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
              />
            </div>
            <p className="text-xs text-mist-500">{t('import.secretsNote')}</p>
          </>
        )}

        {result && (
          <>
            <p className="text-sm text-mist-300">{t(result.format === 'putty' ? 'import.foundPutty' : 'import.foundSsh', { count: result.connections.length })}</p>
            <ul className="nt-scroll flex max-h-72 flex-col divide-y divide-ink-700 overflow-y-auto rounded-xl border border-ink-700">
              {result.connections.map((entry) => {
                const exists = connections.some((c) => !c.shared_by && sameTarget(c, entry))
                return (
                  <li key={entry.name}>
                    <label className="flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-ink-800/60">
                      <input type="checkbox" className="mt-1 accent-[var(--color-accent-500)]" checked={chosen.has(entry.name)} onChange={() => toggle(entry.name)} />
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-mist-100">
                          {entry.name}
                          {exists && <Badge tone="warn">{t('import.exists')}</Badge>}
                          {entry.notes.map((note) => (
                            <Badge key={note}>{noteText(note)}</Badge>
                          ))}
                        </span>
                        <span className="block truncate font-mono text-[11px] text-mist-500">
                          {entry.user}@{entry.host}
                          {entry.port !== 22 ? `:${entry.port}` : ''}
                          {entry.jump ? ` · ${t('import.via', { jump: entry.jump })}` : ''}
                        </span>
                      </span>
                    </label>
                  </li>
                )
              })}
            </ul>
            {result.skipped.length > 0 && (
              <p className="text-xs text-mist-500">
                {t('import.skipped', { count: result.skipped.length })} {result.skipped.map((s) => `${s.name} (${t(`import.reason.${s.reason}`, { defaultValue: s.reason })})`).join(', ')}
              </p>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t('import.group')} hint={t('import.groupHint')} value={group} maxLength={64} onChange={(event) => setGroup(event.target.value)} />
              <SelectField label={t('import.signIn')} value={auth} onChange={setAuth}>
                <option value="ask">{t('import.signInAsk')}</option>
                {(keys.data ?? []).map((key) => (
                  <option key={key.id} value={`key:${key.id}`}>
                    {t('import.signInKey', { name: key.name })}
                  </option>
                ))}
              </SelectField>
            </div>
          </>
        )}
        {error && <Banner tone="bad">{error}</Banner>}
      </div>
    </Dialog>
  )
}

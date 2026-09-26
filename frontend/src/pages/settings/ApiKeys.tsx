import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, errorMessage } from '../../api/client'
import type { ApiKeyCreated, ApiKeyList } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { useNotice } from '../../components/Notice'
import { Symbol } from '../../components/Symbol'
import { Banner, Button, Field, Section, Switch } from '../../components/ui'
import { writeClipboard } from '../../lib/clipboard'
import { formatRelative } from '../../lib/format'
import { useLoad } from '../../lib/useLoad'

/**
 * Read-only keys for dashboards such as nexdeck, behind the operator's switch.
 *
 * ⚠️ The key is shown once, right after it is made. nextrmnl keeps only its
 * hash, so there is no way to show it again later, and the dialog says so.
 */
export function ApiKeysSection({ allowed, onToggle }: { allowed: boolean; onToggle: (value: boolean) => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const keys = useLoad(() => api.get<ApiKeyList>('/api/api-keys'), [allowed])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)
  const [removing, setRemoving] = useState<{ id: number; name: string } | null>(null)

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      setCreated(await api.post<ApiKeyCreated>('/api/api-keys', { name: name.trim() }))
      setName('')
      await keys.reload()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!removing) return
    setBusy(true)
    try {
      await api.delete(`/api/api-keys/${removing.id}`)
      setRemoving(null)
      await keys.reload()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={t('apiKeys.title')} intro={t('apiKeys.lead')}>
      <Switch label={t('apiKeys.allow')} hint={t('apiKeys.allowHint')} checked={allowed} onChange={onToggle} />
      {(keys.data?.keys.length ?? 0) > 0 && (
        <ul className="flex flex-col divide-y divide-ink-800 rounded-xl border border-ink-700">
          {keys.data?.keys.map((key) => (
            <li key={key.id} className="flex items-center gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-mist-100">{key.name}</div>
                <div className="truncate text-xs text-mist-500">
                  <code className="font-mono">{key.prefix}…</code> · {t('apiKeys.madeBy', { name: key.created_by })} ·{' '}
                  {key.last_used_at ? t('apiKeys.lastUsed', { when: formatRelative(key.last_used_at) }) : t('apiKeys.neverUsed')}
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setRemoving({ id: key.id, name: key.name })}>
                {t('apiKeys.delete')}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <Field
          className="flex-1"
          label={t('apiKeys.name')}
          placeholder="nexdeck"
          maxLength={64}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <Button variant="ghost" loading={busy} disabled={!name.trim()} onClick={() => void create()}>
          {t('apiKeys.create')}
        </Button>
      </div>
      {!allowed && <p className="text-xs text-mist-500">{t('apiKeys.offNote')}</p>}
      {error && <Banner tone="bad">{error}</Banner>}

      <Dialog open={created !== null} title={t('apiKeys.createdTitle')} onClose={() => setCreated(null)} footer={<Button onClick={() => setCreated(null)}>{t('common.done')}</Button>}>
        <p className="text-sm text-mist-300">{t('apiKeys.createdLead')}</p>
        <div className="flex items-center gap-2 rounded-xl border border-ink-700 bg-ink-900 px-3 py-2">
          <code className="min-w-0 flex-1 break-all font-mono text-xs text-mist-200">{created?.key}</code>
          <button
            type="button"
            onClick={() => void writeClipboard(created?.key ?? '').then((ok) => notify(ok ? t('vault.copied') : t('clipboard.copyFailed')))}
            className="rounded-full p-1 text-mist-500 hover:text-mist-100"
            aria-label={t('apiKeys.copy')}
          >
            <Symbol name="copy" className="h-3.5 w-3.5" />
          </button>
        </div>
        <Banner tone="warn">{t('apiKeys.createdWarning')}</Banner>
      </Dialog>

      <Dialog
        open={removing !== null}
        title={t('apiKeys.deleteTitle', { name: removing?.name ?? '' })}
        onClose={() => setRemoving(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void remove()}>
              {t('apiKeys.deleteNow')}
            </Button>
          </>
        }
      >
        <Banner tone="warn">{t('apiKeys.deleteText')}</Banner>
      </Dialog>
    </Section>
  )
}
